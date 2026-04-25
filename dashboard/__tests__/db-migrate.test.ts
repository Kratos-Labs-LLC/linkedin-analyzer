import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import Database from 'better-sqlite3';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const hoisted = vi.hoisted(() => ({ tmpDir: '' as string, dbPath: '' as string }));

vi.mock('@/lib/paths', () => ({
  get DB_PATH() {
    return hoisted.dbPath;
  },
  get REPO_ROOT() {
    return hoisted.tmpDir;
  },
  get LOGS_DIR() {
    return path.join(hoisted.tmpDir, 'logs');
  },
  get OUTPUT_DIR() {
    return path.join(hoisted.tmpDir, 'output');
  },
  get CREATORS_YAML_PATH() {
    return path.join(hoisted.tmpDir, 'creators.yaml');
  },
  get CHROME_PROFILE_DIR() {
    return path.join(hoisted.tmpDir, 'chrome_profile');
  },
  get VENV_PYTHON() {
    return '/usr/bin/true';
  },
  get AUTH_SENTINEL() {
    return path.join(hoisted.tmpDir, 'chrome_profile', '.authed');
  },
  get NEW_SKILL_PATH() {
    return path.join(hoisted.tmpDir, 'output', 'new', 'SKILL.md');
  },
  get PROFILE_SKILL_PATH() {
    return path.join(hoisted.tmpDir, 'output', 'profile', 'SKILL.md');
  },
  get PROFILE_AUDIT_PATH() {
    return path.join(hoisted.tmpDir, 'output', 'profile_audit.md');
  },
  ANCHOR_WEIGHT: 1.5,
  STANDARD_WEIGHT: 1.0,
  leadmagnetSkillPath: () => null,
}));

// Real schema.sql lives at <project root>/db/schema.sql. The mocked REPO_ROOT
// points at a per-test tmp dir, so we copy schema.sql into <tmp>/db/schema.sql
// at fixture setup so getDb's `fs.readFileSync(SCHEMA_PATH)` resolves.
const REAL_SCHEMA = fs.readFileSync(
  path.resolve(__dirname, '..', '..', 'db', 'schema.sql'),
  'utf8',
);

describe('dashboard _migrate', () => {
  beforeEach(() => {
    hoisted.tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'dash-mig-'));
    hoisted.dbPath = path.join(hoisted.tmpDir, 'analyzer.db');
    fs.mkdirSync(path.join(hoisted.tmpDir, 'db'), { recursive: true });
    fs.writeFileSync(path.join(hoisted.tmpDir, 'db', 'schema.sql'), REAL_SCHEMA);
    vi.resetModules();
  });

  afterEach(() => {
    fs.rmSync(hoisted.tmpDir, { recursive: true, force: true });
  });

  it('adds growth_7d to a legacy posts table without losing rows', async () => {
    // Build a pre-PR-#6 DB by hand: posts has no growth_7d, no raw_html.
    const legacy = new Database(hoisted.dbPath);
    legacy.exec(`
      CREATE TABLE creators (
        id INTEGER PRIMARY KEY,
        linkedin_url TEXT UNIQUE NOT NULL,
        display_name TEXT,
        weight REAL DEFAULT 1.0,
        active BOOLEAN DEFAULT 1
      );
      CREATE TABLE posts (
        id INTEGER PRIMARY KEY,
        post_urn TEXT UNIQUE NOT NULL,
        creator_id INTEGER REFERENCES creators(id),
        post_text TEXT NOT NULL,
        engagement_score REAL
      );
      INSERT INTO creators (linkedin_url, display_name) VALUES ('u', 'A');
      INSERT INTO posts (post_urn, creator_id, post_text) VALUES ('urn:x', 1, 'hi');
    `);
    legacy.close();

    const { getDb } = await import('@/lib/db');
    const db = getDb();
    const cols = db
      .prepare<[], { name: string }>(`PRAGMA table_info(posts)`)
      .all()
      .map((r) => r.name);
    expect(cols).toContain('growth_7d');
    expect(cols).toContain('raw_html');

    // Existing data preserved
    const post = db
      .prepare<[], { post_urn: string; growth_7d: number | null }>(
        `SELECT post_urn, growth_7d FROM posts`,
      )
      .get();
    expect(post?.post_urn).toBe('urn:x');
    expect(post?.growth_7d).toBeNull();
  });

  it('is idempotent — running getDb twice does not duplicate columns', async () => {
    const { getDb, closeDb } = await import('@/lib/db');
    getDb(); // first init creates schema + runs migrate
    closeDb();
    getDb(); // second init re-runs CREATE IF NOT EXISTS + migrate
    const db = getDb();
    const cols = db
      .prepare<[], { name: string }>(`PRAGMA table_info(posts)`)
      .all()
      .map((r) => r.name);
    expect(cols.filter((c) => c === 'growth_7d')).toHaveLength(1);
    expect(cols.filter((c) => c === 'raw_html')).toHaveLength(1);
  });
});
