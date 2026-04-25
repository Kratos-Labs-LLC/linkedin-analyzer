import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src import storage
from src.config import CreatorConfig


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    storage.init_db(path)
    return path


def creator(url: str, name: str, weight: float = 1.0) -> CreatorConfig:
    return CreatorConfig(url, name, weight)


def test_compute_engagement_score_basic():
    # 100 reactions, 10 comments (*5 = 50), 2 reshares (*10 = 20) -> 170 / 10000 = 0.017
    assert storage.compute_engagement_score(100, 10, 2, 10_000) == pytest.approx(0.017)


def test_compute_engagement_score_zero_followers_returns_none():
    assert storage.compute_engagement_score(100, 0, 0, 0) is None
    assert storage.compute_engagement_score(100, 0, 0, None) is None


def test_sync_creators_deactivates_removed(db: Path):
    with storage.connect(db) as conn:
        storage.sync_creators(conn, [
            creator("https://linkedin.com/in/a", "A"),
            creator("https://linkedin.com/in/b", "B", weight=1.5),
        ])
        rows = conn.execute("SELECT linkedin_url, active, weight FROM creators ORDER BY linkedin_url").fetchall()
        assert len(rows) == 2
        assert all(r["active"] == 1 for r in rows)

    with storage.connect(db) as conn:
        storage.sync_creators(conn, [creator("https://linkedin.com/in/a", "A")])
        by_url = {r["linkedin_url"]: dict(r) for r in conn.execute(
            "SELECT linkedin_url, active FROM creators"
        )}
        assert by_url["https://linkedin.com/in/a"]["active"] == 1
        assert by_url["https://linkedin.com/in/b"]["active"] == 0


def test_select_todays_creators_rotation(db: Path):
    """Never-scraped creators come first; then oldest-scraped; weight breaks near-ties."""
    with storage.connect(db) as conn:
        storage.sync_creators(conn, [
            creator("https://linkedin.com/in/recent", "R"),   # scraped today
            creator("https://linkedin.com/in/old", "O"),      # scraped 5 days ago
            creator("https://linkedin.com/in/new", "N"),      # never scraped
            creator("https://linkedin.com/in/anchor", "AN", weight=1.5),  # scraped 5 days ago, high weight
        ])

        ids = {r["linkedin_url"]: r["id"] for r in conn.execute(
            "SELECT id, linkedin_url FROM creators"
        )}

        now = datetime.now(timezone.utc)
        conn.execute(
            "UPDATE creators SET last_scraped_at = ? WHERE id = ?",
            (now.isoformat(), ids["https://linkedin.com/in/recent"]),
        )
        five_days_ago = (now - timedelta(days=5)).isoformat()
        conn.execute(
            "UPDATE creators SET last_scraped_at = ? WHERE id IN (?, ?)",
            (five_days_ago, ids["https://linkedin.com/in/old"], ids["https://linkedin.com/in/anchor"]),
        )

        picks = storage.select_todays_creators(conn, n=3)
        picked_urls = [p["linkedin_url"] for p in picks]

    # Never-scraped creator should be first (highest days_since)
    assert picked_urls[0] == "https://linkedin.com/in/new"
    # Among the two scraped 5 days ago, the weighted one (anchor) outranks the unweighted (old).
    assert picked_urls[1] == "https://linkedin.com/in/anchor"
    assert picked_urls[2] == "https://linkedin.com/in/old"


def test_insert_post_dedupes(db: Path):
    with storage.connect(db) as conn:
        storage.sync_creators(conn, [creator("https://linkedin.com/in/a", "A")])
        cid = storage.get_creator_id_by_url(conn, "https://linkedin.com/in/a")

        first = storage.insert_post(
            conn,
            post_urn="urn:li:activity:1",
            creator_id=cid,
            post_url=None,
            post_text="hello",
            reactions=100,
            comments=10,
            reshares=1,
            follower_count_at_collection=10_000,
            post_date=None,
        )
        second = storage.insert_post(
            conn,
            post_urn="urn:li:activity:1",
            creator_id=cid,
            post_url=None,
            post_text="hello",
            reactions=100,
            comments=10,
            reshares=1,
            follower_count_at_collection=10_000,
            post_date=None,
        )
        assert first is True
        assert second is False

        rows = conn.execute("SELECT engagement_score FROM posts").fetchall()
        assert len(rows) == 1
        # (100 + 5*10 + 10*1) / 10000 = 160/10000 = 0.016
        assert rows[0]["engagement_score"] == pytest.approx(0.016)


def test_recompute_all_engagement_scores(db: Path):
    with storage.connect(db) as conn:
        storage.sync_creators(conn, [creator("https://linkedin.com/in/a", "A")])
        cid = storage.get_creator_id_by_url(conn, "https://linkedin.com/in/a")
        storage.insert_post(
            conn,
            post_urn="urn:li:activity:1",
            creator_id=cid,
            post_url=None,
            post_text="hi",
            reactions=50,
            comments=5,
            reshares=0,
            follower_count_at_collection=5_000,
            post_date=None,
        )
        # Corrupt the score
        conn.execute("UPDATE posts SET engagement_score = -1")
        storage.recompute_all_engagement_scores(conn)
        row = conn.execute("SELECT engagement_score FROM posts").fetchone()
        assert row["engagement_score"] == pytest.approx(75 / 5_000)


def test_top_bottom_posts_partition(db: Path):
    with storage.connect(db) as conn:
        storage.sync_creators(conn, [creator("https://linkedin.com/in/a", "A")])
        cid = storage.get_creator_id_by_url(conn, "https://linkedin.com/in/a")
        for i in range(10):
            storage.insert_post(
                conn,
                post_urn=f"urn:li:activity:{i}",
                creator_id=cid,
                post_url=None,
                post_text=f"post {i}",
                reactions=i * 10,
                comments=0,
                reshares=0,
                follower_count_at_collection=1_000,
                post_date=None,
            )
        top, bottom = storage.top_bottom_posts(conn, fraction=0.2)
        assert len(top) == 2
        assert len(bottom) == 2
        assert top[0]["reactions"] == 90
        assert bottom[-1]["reactions"] == 0


# --- raw_html column ----------------------------------------------------


def test_insert_post_persists_raw_html(db: Path):
    """Each insert stores the post container's inner_html for later re-extraction."""
    with storage.connect(db) as conn:
        storage.sync_creators(conn, [creator("https://linkedin.com/in/a", "A")])
        cid = storage.get_creator_id_by_url(conn, "https://linkedin.com/in/a")
        storage.insert_post(
            conn,
            post_urn="urn:li:activity:html-1",
            creator_id=cid,
            post_url=None,
            post_text="hello",
            reactions=10,
            comments=0,
            reshares=0,
            follower_count_at_collection=1_000,
            post_date=None,
            raw_html="<div data-urn='urn:li:activity:html-1'>marker</div>",
        )
        row = conn.execute("SELECT raw_html FROM posts").fetchone()
        assert row["raw_html"] is not None
        assert "marker" in row["raw_html"]


def test_insert_post_raw_html_optional(db: Path):
    """Missing raw_html stores NULL — backwards-compat for older callers."""
    with storage.connect(db) as conn:
        storage.sync_creators(conn, [creator("https://linkedin.com/in/a", "A")])
        cid = storage.get_creator_id_by_url(conn, "https://linkedin.com/in/a")
        storage.insert_post(
            conn,
            post_urn="urn:li:activity:nohtml",
            creator_id=cid,
            post_url=None,
            post_text="hi",
            reactions=1,
            comments=0,
            reshares=0,
            follower_count_at_collection=1_000,
            post_date=None,
        )
        row = conn.execute("SELECT raw_html FROM posts").fetchone()
        assert row["raw_html"] is None


def test_init_db_migrates_old_schema_to_add_raw_html(tmp_path: Path):
    """If a pre-migration DB exists (no raw_html column), init_db must add it
    without losing data."""
    db_path = tmp_path / "old.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Build a posts table that matches the pre-migration shape, with one row.
    import sqlite3

    legacy_schema = """
    CREATE TABLE creators (
      id INTEGER PRIMARY KEY,
      linkedin_url TEXT UNIQUE NOT NULL,
      display_name TEXT,
      current_follower_count INTEGER,
      weight REAL DEFAULT 1.0,
      last_scraped_at TEXT,
      active BOOLEAN DEFAULT 1,
      added_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE posts (
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
    """
    legacy = sqlite3.connect(str(db_path))
    legacy.executescript(legacy_schema)
    legacy.execute(
        "INSERT INTO creators (linkedin_url, display_name) VALUES (?, ?)",
        ("https://linkedin.com/in/legacy", "Legacy"),
    )
    legacy.execute(
        "INSERT INTO posts (post_urn, creator_id, post_text) VALUES (?, ?, ?)",
        ("urn:li:activity:legacy", 1, "old post"),
    )
    legacy.commit()
    legacy.close()

    storage.init_db(db_path)

    with storage.connect(db_path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(posts)").fetchall()}
        assert "raw_html" in cols
        # Existing data preserved
        row = conn.execute("SELECT post_urn, raw_html FROM posts").fetchone()
        assert row["post_urn"] == "urn:li:activity:legacy"
        assert row["raw_html"] is None


def test_init_db_migration_is_idempotent(db: Path):
    """Re-running init_db on an already-migrated DB is a no-op."""
    storage.init_db(db)  # already called by the fixture; this is the second run
    with storage.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(posts)").fetchall()}
        # Must not have duplicated columns
        assert sum(1 for c in cols if c == "raw_html") == 1


def test_parse_post_html_populates_raw_html_field():
    from src.parser import parse_post_html

    html = (
        '<div data-urn="urn:li:activity:12345" class="feed-shared-update-v2">'
        '<span class="update-components-text">hello</span></div>'
    )
    cand = parse_post_html(html)
    assert cand is not None
    assert cand.raw_html == html
