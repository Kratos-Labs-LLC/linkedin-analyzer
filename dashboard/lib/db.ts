import 'server-only';

import fs from 'node:fs';
import path from 'node:path';
import Database from 'better-sqlite3';

import { DB_PATH, OUTPUT_DIR, REPO_ROOT } from './paths';

// Single source of truth for the schema lives at db/schema.sql, loaded by
// both Python and TS at module-init time. Matching the test ensures neither
// side can drift its CREATE TABLE statements out of sync.
const SCHEMA_PATH = path.join(REPO_ROOT, 'db', 'schema.sql');

export type CreatorRow = {
  id: number;
  linkedin_url: string;
  display_name: string | null;
  current_follower_count: number | null;
  weight: number;
  last_scraped_at: string | null;
  active: 0 | 1;
  added_at: string;
};

export type PostRow = {
  id: number;
  post_urn: string;
  creator_id: number;
  post_url: string | null;
  post_text: string;
  reactions: number;
  comments: number;
  reshares: number;
  follower_count_at_collection: number | null;
  engagement_score: number | null;
  post_date: string | null;
  collected_at: string;
  is_repost_with_commentary: 0 | 1;
  growth_7d: number | null;
  // raw_html is excluded from the public type because hot paths must not
  // pull it. If you need it, query it via a dedicated function (none ships
  // by default; the column lives in src/storage.py for re-extraction).
};

export type PostWithCreator = PostRow & {
  creator_name: string | null;
  creator_url: string;
};

export type RunRow = {
  id: number;
  run_date: string;
  started_at: string | null;
  ended_at: string | null;
  status: string;
  posts_collected: number | null;
  creators_scraped: number | null;
  errors_json: string | null;
};


// Cache the Database handle on globalThis so Next dev mode's HMR re-uses the
// open connection across module re-evaluations. Without this, every code
// edit during dev opens a fresh handle and orphans the prior one — slow
// connection leak. Production (`next start`) loads the module once and
// behaves identically either way.
declare global {
  // eslint-disable-next-line no-var
  var __linkedinAnalyzerDb: Database.Database | null | undefined;
}

export function getDb(): Database.Database {
  if (globalThis.__linkedinAnalyzerDb) return globalThis.__linkedinAnalyzerDb;
  fs.mkdirSync(path.dirname(DB_PATH), { recursive: true });
  const db = new Database(DB_PATH);
  db.pragma('journal_mode = WAL');
  db.pragma('foreign_keys = ON');
  db.exec(fs.readFileSync(SCHEMA_PATH, 'utf8'));
  _migrate(db);
  globalThis.__linkedinAnalyzerDb = db;
  return db;
}

/**
 * Idempotent column adds for older DBs that pre-date a schema change.
 * Mirror of src/storage.py:_migrate so the dashboard self-heals when
 * opening a DB written by an older Python run.
 */
function _migrate(db: Database.Database) {
  const postsCols = new Set<string>(
    db
      .prepare<[], { name: string }>(`PRAGMA table_info(posts)`)
      .all()
      .map((r) => r.name),
  );
  if (!postsCols.has('raw_html')) {
    db.exec('ALTER TABLE posts ADD COLUMN raw_html TEXT');
  }
  if (!postsCols.has('growth_7d')) {
    db.exec('ALTER TABLE posts ADD COLUMN growth_7d INTEGER');
  }
}

export function closeDb() {
  if (globalThis.__linkedinAnalyzerDb) {
    globalThis.__linkedinAnalyzerDb.close();
    globalThis.__linkedinAnalyzerDb = null;
  }
}

export function computeEngagementScore(
  reactions: number,
  comments: number,
  reshares: number,
  followerCount: number | null,
): number | null {
  if (!followerCount || followerCount <= 0) return null;
  return (reactions + 5 * comments + 10 * reshares) / followerCount;
}

// Explicit posts column list for SELECT — `raw_html` (often ~500KB-1MB per
// row in production) is intentionally excluded from hot-path queries. Add
// it back via a dedicated query only when you actually need the HTML
// (e.g. re-running the extractor against stored snapshots).
const POST_COLS_NO_HTML =
  'p.id, p.post_urn, p.creator_id, p.post_url, p.post_text, p.reactions, ' +
  'p.comments, p.reshares, p.follower_count_at_collection, p.engagement_score, ' +
  'p.post_date, p.collected_at, p.is_repost_with_commentary, p.growth_7d';

export type YamlCreator = { url: string; name: string; weight: number };

export function syncCreatorsFromYaml(creators: YamlCreator[]) {
  const db = getDb();
  const upsert = db.prepare(`
    INSERT INTO creators (linkedin_url, display_name, weight, active)
    VALUES (?, ?, ?, 1)
    ON CONFLICT(linkedin_url) DO UPDATE SET
      display_name = excluded.display_name,
      weight = excluded.weight,
      active = 1
  `);
  const deactivate = db.prepare(
    `UPDATE creators SET active = 0 WHERE linkedin_url NOT IN (${
      creators.length === 0 ? `''` : creators.map(() => '?').join(',')
    })`,
  );
  const urls = creators.map((c) => c.url);
  const txn = db.transaction(() => {
    for (const c of creators) upsert.run(c.url, c.name, c.weight);
    if (creators.length === 0) db.exec('UPDATE creators SET active = 0');
    else deactivate.run(...urls);
  });
  txn();
}

export function listCreatorsWithCounts() {
  const db = getDb();
  const creators = db
    .prepare<[], CreatorRow>(
      `SELECT * FROM creators ORDER BY active DESC, weight DESC, display_name ASC`,
    )
    .all();
  const counts = db
    .prepare<[], { creator_id: number; n: number }>(
      `SELECT creator_id, COUNT(*) AS n FROM posts GROUP BY creator_id`,
    )
    .all();
  const byId = new Map(counts.map((c) => [c.creator_id, c.n]));
  return creators.map((c) => ({ ...c, post_count: byId.get(c.id) ?? 0 }));
}

export function listActiveCreators() {
  return getDb()
    .prepare<[], { id: number; display_name: string | null }>(
      `SELECT id, display_name FROM creators WHERE active = 1 ORDER BY display_name`,
    )
    .all();
}

export function activeCreatorCount(): number {
  return (
    getDb()
      .prepare<[], { n: number }>(`SELECT COUNT(*) AS n FROM creators WHERE active = 1`)
      .get()?.n ?? 0
  );
}

export function listPosts(opts: {
  creatorId?: number;
  minScore?: number;
  sort?: 'engagement' | 'date';
  limit?: number;
}): PostWithCreator[] {
  const { creatorId, minScore, sort = 'engagement', limit = 100 } = opts;
  const clauses: string[] = ['1=1'];
  const params: (number | string)[] = [];
  if (creatorId) {
    clauses.push('p.creator_id = ?');
    params.push(creatorId);
  }
  if (typeof minScore === 'number' && !Number.isNaN(minScore)) {
    clauses.push('p.engagement_score >= ?');
    params.push(minScore);
  }
  const order =
    sort === 'date'
      ? 'p.collected_at DESC'
      : 'p.engagement_score DESC NULLS LAST';
  const sql = `SELECT ${POST_COLS_NO_HTML}, c.display_name AS creator_name, c.linkedin_url AS creator_url
               FROM posts p JOIN creators c ON c.id = p.creator_id
               WHERE ${clauses.join(' AND ')}
               ORDER BY ${order}
               LIMIT ?`;
  params.push(limit);
  return getDb().prepare<unknown[], PostWithCreator>(sql).all(...params);
}

export function getPost(id: number) {
  const db = getDb();
  const post = db
    .prepare<[number], PostWithCreator>(
      `SELECT ${POST_COLS_NO_HTML}, c.display_name AS creator_name, c.linkedin_url AS creator_url
       FROM posts p JOIN creators c ON c.id = p.creator_id WHERE p.id = ?`,
    )
    .get(id);
  if (!post) return null;
  const featuresRow = db
    .prepare<[number], { features_json: string }>(
      `SELECT features_json FROM post_features WHERE post_id = ?`,
    )
    .get(id);
  let features: Record<string, unknown> | null = null;
  if (featuresRow) {
    try {
      features = JSON.parse(featuresRow.features_json);
    } catch {
      features = null;
    }
  }
  return { post, features };
}

export function listRuns(limit = 100): RunRow[] {
  return getDb()
    .prepare<[number], RunRow>(`SELECT * FROM runs ORDER BY id DESC LIMIT ?`)
    .all(limit);
}

export function recentRuns(limit = 10): RunRow[] {
  return listRuns(limit);
}

export function collectionSummary() {
  const db = getDb();
  const total = db.prepare<[], { n: number }>(`SELECT COUNT(*) AS n FROM posts`).get();
  const first = db
    .prepare<[], { ts: string | null }>(`SELECT MIN(collected_at) AS ts FROM posts`)
    .get();
  const creatorsWith20 = db
    .prepare<[], { n: number }>(
      `SELECT COUNT(*) AS n FROM (SELECT creator_id FROM posts GROUP BY creator_id HAVING COUNT(*) >= 20)`,
    )
    .get();
  return {
    total_posts: total?.n ?? 0,
    first_collected_at: first?.ts ?? null,
    creators_with_20_posts: creatorsWith20?.n ?? 0,
  };
}

export function avgEngagement(): number | null {
  return (
    getDb()
      .prepare<[], { s: number | null }>(
        `SELECT AVG(engagement_score) AS s FROM posts WHERE engagement_score IS NOT NULL`,
      )
      .get()?.s ?? null
  );
}

export function postsPerDay(days = 14): { date: string; count: number }[] {
  const rows = getDb()
    .prepare<[string], { date: string; count: number }>(
      `SELECT substr(collected_at, 1, 10) AS date, COUNT(*) AS count
       FROM posts
       WHERE collected_at >= date('now', ?)
       GROUP BY date
       ORDER BY date ASC`,
    )
    .all(`-${days} days`);
  return rows;
}

// --- Profile snapshots ----------------------------------------------------

export type ProfileSnapshotRow = {
  id: number;
  creator_id: number;
  snapshot_at: string;
  follower_count: number | null;
  headline: string | null;
  about_text: string | null;
  current_role: string | null;
  current_company: string | null;
  location: string | null;
  has_profile_photo: 0 | 1 | null;
  has_banner: 0 | 1 | null;
  featured_count: number | null;
};

export function getCreator(id: number) {
  return getDb()
    .prepare<[number], CreatorRow>(`SELECT * FROM creators WHERE id = ?`)
    .get(id);
}

export function listProfileSnapshots(creatorId: number): ProfileSnapshotRow[] {
  return getDb()
    .prepare<[number], ProfileSnapshotRow>(
      `SELECT id, creator_id, snapshot_at, follower_count, headline, about_text,
              current_role, current_company, location, has_profile_photo,
              has_banner, featured_count
       FROM creator_profiles
       WHERE creator_id = ?
       ORDER BY snapshot_at ASC`,
    )
    .all(creatorId);
}

export function latestProfileSnapshot(creatorId: number): ProfileSnapshotRow | undefined {
  return getDb()
    .prepare<[number], ProfileSnapshotRow>(
      `SELECT id, creator_id, snapshot_at, follower_count, headline, about_text,
              current_role, current_company, location, has_profile_photo,
              has_banner, featured_count
       FROM creator_profiles
       WHERE creator_id = ?
       ORDER BY snapshot_at DESC
       LIMIT 1`,
    )
    .get(creatorId);
}

export function postsForCreatorByGrowth(creatorId: number, limit = 30): PostWithCreator[] {
  return getDb()
    .prepare<[number, number], PostWithCreator>(
      `SELECT ${POST_COLS_NO_HTML}, c.display_name AS creator_name, c.linkedin_url AS creator_url
       FROM posts p JOIN creators c ON c.id = p.creator_id
       WHERE p.creator_id = ?
       ORDER BY p.growth_7d DESC NULLS LAST, p.engagement_score DESC NULLS LAST
       LIMIT ?`,
    )
    .all(creatorId, limit);
}

/**
 * Normalize SQLite/ISO timestamp strings into a deterministic JS Date.
 *
 * SQLite's `CURRENT_TIMESTAMP` default emits 'YYYY-MM-DD HH:MM:SS' (UTC, no
 * timezone marker). `new Date()` on that string is implementation-defined
 * per ECMA-262 — V8 has historically interpreted it as local time, Safari as
 * UTC. Python's `datetime.fromisoformat` (which growth.py uses) handles it
 * deterministically. We normalize the string so JS matches.
 *
 * Accepts either:
 *   '2026-04-01 00:00:00'         (SQLite default)
 *   '2026-04-01T00:00:00'         (Python isoformat, naive)
 *   '2026-04-01T00:00:00Z'        (UTC marker)
 *   '2026-04-01T00:00:00+00:00'   (offset)
 */
export function parseSqliteTimestamp(s: string): number {
  let iso = s.includes('T') ? s : s.replace(' ', 'T');
  if (!/(?:Z|[+-]\d\d:?\d\d)$/.test(iso)) iso += 'Z';
  return new Date(iso).getTime();
}

/**
 * Per-creator follower-growth slope (followers per week) over the snapshot
 * series. Returns null when the creator has fewer than 2 snapshots or all
 * timestamps collapse. Mirrors src/analyzer/growth.py:compute_growth_rate.
 */
export function computeGrowthRatePerWeek(snapshots: ProfileSnapshotRow[]): number | null {
  const points = snapshots
    .filter((s) => s.follower_count !== null)
    .map((s) => ({
      t: parseSqliteTimestamp(s.snapshot_at) / 1000,
      f: s.follower_count as number,
    }))
    .filter((p) => Number.isFinite(p.t));
  if (points.length < 2) return null;
  const t0 = points[0].t;
  const xs = points.map((p) => p.t - t0);
  const ys = points.map((p) => p.f);
  const range = Math.max(...xs) - Math.min(...xs);
  if (range <= 0) return null;
  const n = xs.length;
  const mx = xs.reduce((a, b) => a + b, 0) / n;
  const my = ys.reduce((a, b) => a + b, 0) / n;
  let num = 0;
  let den = 0;
  for (let i = 0; i < n; i++) {
    num += (xs[i] - mx) * (ys[i] - my);
    den += (xs[i] - mx) ** 2;
  }
  if (den === 0) return null;
  return (num / den) * 7 * 24 * 3600;
}

// --- Intelligence briefs --------------------------------------------------

/**
 * Stable file-safe slug for a creator. Must match
 * src/analyzer/intelligence_runner.py:creator_slug exactly — both sides
 * resolve the same `output/intelligence/<slug>.md` path.
 */
export function creatorSlug(args: {
  display_name: string | null;
  id: number;
}): string {
  const base = (args.display_name ?? '')
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  if (!base) return `creator-${args.id}`;
  return `${base}-${args.id}`;
}

export type IntelligenceArtifacts = {
  markdown: string | null;
  packJson: unknown | null;
};

/**
 * Read both the markdown brief and the pack.json sidecar. Returns nulls
 * for whichever pieces are missing — the page renders the brief when
 * present and a placeholder when not.
 */
export function readIntelligenceArtifacts(creator: {
  id: number;
  display_name: string | null;
}): IntelligenceArtifacts {
  const slug = creatorSlug(creator);
  const mdPath = path.join(OUTPUT_DIR, 'intelligence', `${slug}.md`);
  const jsonPath = path.join(OUTPUT_DIR, 'intelligence', `${slug}.pack.json`);

  let markdown: string | null = null;
  try {
    markdown = fs.readFileSync(mdPath, 'utf8');
  } catch {
    markdown = null;
  }

  let packJson: unknown = null;
  try {
    const raw = fs.readFileSync(jsonPath, 'utf8');
    packJson = JSON.parse(raw);
  } catch {
    packJson = null;
  }

  return { markdown, packJson };
}

export function topGrowthCreators(limit = 5): Array<{
  id: number;
  display_name: string | null;
  growth_rate_per_week: number | null;
  latest_follower_count: number | null;
}> {
  const creators = getDb()
    .prepare<[], { id: number; display_name: string | null }>(
      `SELECT id, display_name FROM creators WHERE active = 1`,
    )
    .all();
  const out = creators
    .map((c) => {
      const snaps = listProfileSnapshots(c.id);
      const rate = computeGrowthRatePerWeek(snaps);
      const latest = snaps.length ? snaps[snaps.length - 1].follower_count : null;
      return {
        id: c.id,
        display_name: c.display_name,
        growth_rate_per_week: rate,
        latest_follower_count: latest,
      };
    })
    .filter((r) => r.growth_rate_per_week !== null);
  out.sort(
    (a, b) =>
      (b.growth_rate_per_week ?? -Infinity) - (a.growth_rate_per_week ?? -Infinity),
  );
  return out.slice(0, limit);
}
