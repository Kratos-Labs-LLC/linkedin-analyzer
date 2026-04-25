-- Canonical SQLite schema for the LinkedIn analyzer.
-- Loaded by both src/storage.py and dashboard/lib/db.ts so neither side can
-- drift. Migrations remain in the language-specific modules (Python uses
-- sqlite3.execute for ALTER + column-presence checks; TS mirrors).

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
  is_repost_with_commentary BOOLEAN DEFAULT 0,
  raw_html TEXT,
  growth_7d INTEGER
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

-- Profile snapshots over time — one row per visit per creator. The collector
-- already lands on the profile page each scrape; we persist what's there so
-- we can compute follower-growth attribution without changing scrape volume.
CREATE TABLE IF NOT EXISTS creator_profiles (
  id INTEGER PRIMARY KEY,
  creator_id INTEGER NOT NULL REFERENCES creators(id),
  snapshot_at TEXT DEFAULT CURRENT_TIMESTAMP,
  follower_count INTEGER,
  headline TEXT,
  about_text TEXT,
  current_role TEXT,
  current_company TEXT,
  location TEXT,
  has_profile_photo BOOLEAN,
  has_banner BOOLEAN,
  featured_count INTEGER,
  raw_html TEXT
);

-- One Claude-extracted feature row per active creator (latest snapshot).
-- History is in creator_profiles; rich features here.
CREATE TABLE IF NOT EXISTS profile_features (
  creator_id INTEGER PRIMARY KEY REFERENCES creators(id),
  features_json TEXT NOT NULL,
  extracted_at TEXT DEFAULT CURRENT_TIMESTAMP,
  source_snapshot_id INTEGER REFERENCES creator_profiles(id)
);

CREATE INDEX IF NOT EXISTS idx_posts_engagement ON posts(engagement_score DESC);
CREATE INDEX IF NOT EXISTS idx_posts_creator ON posts(creator_id);
CREATE INDEX IF NOT EXISTS idx_posts_creator_engagement
  ON posts(creator_id, engagement_score DESC);
CREATE INDEX IF NOT EXISTS idx_runs_date ON runs(run_date);
CREATE INDEX IF NOT EXISTS idx_cp_creator_time
  ON creator_profiles(creator_id, snapshot_at DESC);
