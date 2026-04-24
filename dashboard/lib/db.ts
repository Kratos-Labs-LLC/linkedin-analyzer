import 'server-only';

import fs from 'node:fs';
import path from 'node:path';
import Database from 'better-sqlite3';

import { DB_PATH } from './paths';

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

const SCHEMA = `
CREATE TABLE IF NOT EXISTS creators (
  id INTEGER PRIMARY KEY,
  linkedin_url TEXT UNIQUE NOT NULL,
  display_name TEXT,
  current_follower_count INTEGER,
  weight REAL DEFAULT 1.0,
  last_scraped_at TEXT,
  active BOOLEAN DEFAULT 1,
  added_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS posts (
  id INTEGER PRIMARY KEY,
  post_urn TEXT UNIQUE NOT NULL,
  creator_id INTEGER REFERENCES creators(id),
  post_url TEXT,
  post_text TEXT NOT NULL,
  reactions INTEGER DEFAULT 0,
  comments INTEGER DEFAULT 0,
  reshares INTEGER DEFAULT 0,
  follower_count_at_collection INTEGER,
  engagement_score REAL,
  post_date TEXT,
  collected_at TEXT DEFAULT CURRENT_TIMESTAMP,
  is_repost_with_commentary BOOLEAN DEFAULT 0
);

CREATE TABLE IF NOT EXISTS post_features (
  post_id INTEGER PRIMARY KEY REFERENCES posts(id),
  features_json TEXT NOT NULL,
  extracted_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY,
  run_date TEXT NOT NULL,
  started_at TEXT,
  ended_at TEXT,
  status TEXT,
  posts_collected INTEGER,
  creators_scraped INTEGER,
  errors_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_posts_engagement ON posts(engagement_score DESC);
CREATE INDEX IF NOT EXISTS idx_posts_creator ON posts(creator_id);
CREATE INDEX IF NOT EXISTS idx_runs_date ON runs(run_date);
`;

let _db: Database.Database | null = null;

export function getDb(): Database.Database {
  if (_db) return _db;
  fs.mkdirSync(path.dirname(DB_PATH), { recursive: true });
  const db = new Database(DB_PATH);
  db.pragma('journal_mode = WAL');
  db.pragma('foreign_keys = ON');
  db.exec(SCHEMA);
  _db = db;
  return db;
}

export function closeDb() {
  if (_db) {
    _db.close();
    _db = null;
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
  const sql = `SELECT p.*, c.display_name AS creator_name, c.linkedin_url AS creator_url
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
      `SELECT p.*, c.display_name AS creator_name, c.linkedin_url AS creator_url
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
