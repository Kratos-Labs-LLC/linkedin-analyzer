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
"""


def init_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def connect(path: Path | str) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
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
) -> bool:
    """Insert a post; returns True if inserted, False if duplicate."""
    score = compute_engagement_score(reactions, comments, reshares, follower_count_at_collection)
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO posts
          (post_urn, creator_id, post_url, post_text, reactions, comments, reshares,
           follower_count_at_collection, engagement_score, post_date, is_repost_with_commentary)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        ),
    )
    return cur.rowcount > 0


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
