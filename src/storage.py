"""SQLite data access. Raw SQL, no ORM."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from src.config import CreatorConfig

SCHEMA = """
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
  raw_html TEXT
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
-- Composite index covers the common "posts for creator X above engagement Y"
-- query pattern in dashboard /posts and /creators/[id]. SQLite picks one
-- index per query, so without this composite the creator filter forces a
-- table scan.
CREATE INDEX IF NOT EXISTS idx_posts_creator_engagement
  ON posts(creator_id, engagement_score DESC);
CREATE INDEX IF NOT EXISTS idx_runs_date ON runs(run_date);
CREATE INDEX IF NOT EXISTS idx_cp_creator_time
  ON creator_profiles(creator_id, snapshot_at DESC);
"""


def init_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


# Idempotent column adds for older DBs that pre-date a schema change.
# CREATE TABLE IF NOT EXISTS leaves existing tables untouched, so new columns
# need an explicit ALTER. Each migration wraps in a column-presence check so
# re-running init_db is a no-op.
def _migrate(conn: sqlite3.Connection) -> None:
    posts_cols = {row[1] for row in conn.execute("PRAGMA table_info(posts)").fetchall()}
    if "raw_html" not in posts_cols:
        conn.execute("ALTER TABLE posts ADD COLUMN raw_html TEXT")
    if "growth_7d" not in posts_cols:
        # Follower delta in the 7 days following the post; recomputable via
        # src/analyzer/growth.py:recompute_post_growth.
        conn.execute("ALTER TABLE posts ADD COLUMN growth_7d INTEGER")

    # creator_profiles + profile_features are introduced in this migration;
    # CREATE IF NOT EXISTS in SCHEMA handles them on fresh DBs, but legacy
    # DBs still need them — re-run the relevant DDL here as a safety net.
    # (CREATE IF NOT EXISTS makes it idempotent regardless of how we arrive.)
    conn.execute(
        """
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
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS profile_features (
          creator_id INTEGER PRIMARY KEY REFERENCES creators(id),
          features_json TEXT NOT NULL,
          extracted_at TEXT DEFAULT CURRENT_TIMESTAMP,
          source_snapshot_id INTEGER REFERENCES creator_profiles(id)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_cp_creator_time
          ON creator_profiles(creator_id, snapshot_at DESC)
        """
    )


@contextmanager
def connect(path: Path | str) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL = readers don't block writers and vice versa. Critical when the
    # daily collector writes mid-analysis: without WAL, dashboard reads
    # serialize behind the analyzer's bulk UPDATEs. Set repo-wide once;
    # SQLite remembers the journal mode in the DB header.
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def compute_engagement_score(
    reactions: int,
    comments: int,
    reshares: int,
    follower_count: int | None,
) -> float | None:
    if not follower_count or follower_count <= 0:
        return None
    return (reactions + 5 * comments + 10 * reshares) / follower_count


def sync_creators(conn: sqlite3.Connection, creators: Iterable[CreatorConfig]) -> None:
    """Upsert creators and mark any not present as inactive."""
    yaml_urls: set[str] = set()
    for c in creators:
        yaml_urls.add(c.linkedin_url)
        conn.execute(
            """
            INSERT INTO creators (linkedin_url, display_name, weight, active)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(linkedin_url) DO UPDATE SET
              display_name = excluded.display_name,
              weight = excluded.weight,
              active = 1
            """,
            (c.linkedin_url, c.display_name, c.weight),
        )

    if yaml_urls:
        placeholders = ",".join("?" * len(yaml_urls))
        conn.execute(
            f"UPDATE creators SET active = 0 WHERE linkedin_url NOT IN ({placeholders})",
            tuple(yaml_urls),
        )
    else:
        conn.execute("UPDATE creators SET active = 0")


def select_todays_creators(conn: sqlite3.Connection, n: int = 10) -> list[sqlite3.Row]:
    """Rotation: priority = days_since_last_scraped * weight, desc; tie-break by id asc.

    Never-scraped creators get a large days_since value so they seed first.
    """
    now = datetime.now(timezone.utc).isoformat()
    rows = conn.execute(
        """
        SELECT
          id,
          linkedin_url,
          display_name,
          weight,
          last_scraped_at,
          current_follower_count,
          CASE
            WHEN last_scraped_at IS NULL THEN 365.0
            ELSE (julianday(?) - julianday(last_scraped_at))
          END AS days_since
        FROM creators
        WHERE active = 1
        """,
        (now,),
    ).fetchall()

    scored = [
        (r["days_since"] * r["weight"], r["id"], r)
        for r in rows
    ]
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [t[2] for t in scored[:n]]


def mark_creator_scraped(
    conn: sqlite3.Connection, creator_id: int, follower_count: int | None
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    if follower_count is not None:
        conn.execute(
            "UPDATE creators SET last_scraped_at = ?, current_follower_count = ? WHERE id = ?",
            (now, follower_count, creator_id),
        )
    else:
        conn.execute(
            "UPDATE creators SET last_scraped_at = ? WHERE id = ?",
            (now, creator_id),
        )


def deactivate_creator(conn: sqlite3.Connection, creator_id: int) -> None:
    conn.execute("UPDATE creators SET active = 0 WHERE id = ?", (creator_id,))


def insert_post(
    conn: sqlite3.Connection,
    *,
    post_urn: str,
    creator_id: int,
    post_url: str | None,
    post_text: str,
    reactions: int,
    comments: int,
    reshares: int,
    follower_count_at_collection: int | None,
    post_date: str | None,
    is_repost_with_commentary: bool = False,
    raw_html: str | None = None,
) -> bool:
    """Insert a post; returns True if inserted, False if duplicate.

    `raw_html` is the post container's inner HTML at scrape time. We persist
    it so the feature schema can be extended later without re-scraping —
    re-running the extractor against stored HTML is fast and free.
    """
    score = compute_engagement_score(reactions, comments, reshares, follower_count_at_collection)
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO posts
          (post_urn, creator_id, post_url, post_text, reactions, comments, reshares,
           follower_count_at_collection, engagement_score, post_date,
           is_repost_with_commentary, raw_html)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            post_urn,
            creator_id,
            post_url,
            post_text,
            reactions,
            comments,
            reshares,
            follower_count_at_collection,
            score,
            post_date,
            1 if is_repost_with_commentary else 0,
            raw_html,
        ),
    )
    return cur.rowcount > 0


def insert_profile_snapshot(
    conn: sqlite3.Connection,
    *,
    creator_id: int,
    follower_count: int | None = None,
    headline: str | None = None,
    about_text: str | None = None,
    current_role: str | None = None,
    current_company: str | None = None,
    location: str | None = None,
    has_profile_photo: bool | None = None,
    has_banner: bool | None = None,
    featured_count: int | None = None,
    raw_html: str | None = None,
    snapshot_at: str | None = None,
) -> int:
    """Insert a profile snapshot. Returns the new row's id.

    Snapshots accumulate — duplicate inserts are intentional. The deltas
    between consecutive rows (by snapshot_at) are what `growth.py` uses to
    attribute follower growth to posts and creators.
    """
    cur = conn.execute(
        """
        INSERT INTO creator_profiles
          (creator_id, follower_count, headline, about_text, current_role,
           current_company, location, has_profile_photo, has_banner,
           featured_count, raw_html, snapshot_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
        """,
        (
            creator_id,
            follower_count,
            headline,
            about_text,
            current_role,
            current_company,
            location,
            None if has_profile_photo is None else (1 if has_profile_photo else 0),
            None if has_banner is None else (1 if has_banner else 0),
            featured_count,
            raw_html,
            snapshot_at,
        ),
    )
    return int(cur.lastrowid or 0)


def latest_profile_snapshot(
    conn: sqlite3.Connection, creator_id: int
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM creator_profiles
        WHERE creator_id = ?
        ORDER BY snapshot_at DESC
        LIMIT 1
        """,
        (creator_id,),
    ).fetchone()


def list_profile_snapshots(
    conn: sqlite3.Connection, creator_id: int
) -> list[sqlite3.Row]:
    """Oldest first — convenient for time-series math (growth slope, deltas)."""
    return list(
        conn.execute(
            """
            SELECT id, snapshot_at, follower_count
            FROM creator_profiles
            WHERE creator_id = ?
              AND follower_count IS NOT NULL
            ORDER BY snapshot_at ASC
            """,
            (creator_id,),
        )
    )


def recompute_all_engagement_scores(conn: sqlite3.Connection) -> int:
    """Recompute engagement_score for every post; returns rows updated."""
    rows = conn.execute(
        "SELECT id, reactions, comments, reshares, follower_count_at_collection FROM posts"
    ).fetchall()
    updated = 0
    for r in rows:
        score = compute_engagement_score(
            r["reactions"], r["comments"], r["reshares"], r["follower_count_at_collection"]
        )
        conn.execute("UPDATE posts SET engagement_score = ? WHERE id = ?", (score, r["id"]))
        updated += 1
    return updated


def record_run(
    conn: sqlite3.Connection,
    *,
    run_date: str,
    started_at: str,
    ended_at: str,
    status: str,
    posts_collected: int,
    creators_scraped: int,
    errors: list[dict] | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO runs
          (run_date, started_at, ended_at, status, posts_collected, creators_scraped, errors_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_date,
            started_at,
            ended_at,
            status,
            posts_collected,
            creators_scraped,
            json.dumps(errors) if errors else None,
        ),
    )
    return cur.lastrowid


def get_creator_id_by_url(conn: sqlite3.Connection, url: str) -> int | None:
    row = conn.execute("SELECT id FROM creators WHERE linkedin_url = ?", (url,)).fetchone()
    return row["id"] if row else None


def recent_runs(conn: sqlite3.Connection, limit: int = 3) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()


def collection_summary(conn: sqlite3.Connection) -> dict:
    total = conn.execute("SELECT COUNT(*) AS n FROM posts").fetchone()["n"]
    first = conn.execute("SELECT MIN(collected_at) AS ts FROM posts").fetchone()["ts"]
    creators_with_20 = conn.execute(
        """
        SELECT COUNT(*) AS n FROM (
          SELECT creator_id FROM posts GROUP BY creator_id HAVING COUNT(*) >= 20
        )
        """
    ).fetchone()["n"]
    return {
        "total_posts": total,
        "first_collected_at": first,
        "creators_with_20_posts": creators_with_20,
    }


def top_bottom_posts(conn: sqlite3.Connection, fraction: float = 0.15) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    """Return (top, bottom) posts by engagement_score. Excludes posts with NULL scores."""
    all_scored = conn.execute(
        """
        SELECT posts.*, creators.display_name AS creator_name, creators.linkedin_url AS creator_url
        FROM posts
        JOIN creators ON creators.id = posts.creator_id
        WHERE engagement_score IS NOT NULL
        ORDER BY engagement_score DESC
        """
    ).fetchall()
    if not all_scored:
        return [], []
    cut = max(1, int(len(all_scored) * fraction))
    return all_scored[:cut], all_scored[-cut:]
