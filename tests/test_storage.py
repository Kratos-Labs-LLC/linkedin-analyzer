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
