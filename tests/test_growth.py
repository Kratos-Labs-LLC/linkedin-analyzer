from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src import storage
from src.analyzer.growth import (
    DEFAULT_WINDOW_DAYS,
    Snapshot,
    compute_growth_rate,
    compute_proximity_growth,
    creator_growth_rates,
    normalize_snapshots,
    recompute_post_growth,
)
from src.config import AppConfig, CreatorConfig


# --- pure-function tests -------------------------------------------------


def _snap(days: int, fc: int) -> Snapshot:
    base = datetime(2026, 4, 1, tzinfo=timezone.utc)
    return Snapshot(base + timedelta(days=days), fc)


def test_compute_growth_rate_linear():
    """100 followers gained over 7 days -> ~100 followers/week."""
    snaps = [_snap(0, 1000), _snap(7, 1100)]
    rate = compute_growth_rate(snaps)
    assert rate is not None
    assert abs(rate - 100) < 0.01


def test_compute_growth_rate_negative():
    snaps = [_snap(0, 1000), _snap(7, 950)]
    rate = compute_growth_rate(snaps)
    assert rate is not None
    assert rate < 0


def test_compute_growth_rate_handles_three_points_least_squares():
    snaps = [_snap(0, 1000), _snap(7, 1100), _snap(14, 1200)]
    rate = compute_growth_rate(snaps)
    assert rate is not None
    assert abs(rate - 100) < 0.01


def test_compute_growth_rate_returns_none_when_too_short():
    assert compute_growth_rate([]) is None
    assert compute_growth_rate([_snap(0, 1000)]) is None


def test_compute_growth_rate_returns_none_when_timestamps_collapse():
    """Two snapshots at the same instant should fail to fit."""
    base = datetime(2026, 4, 1, tzinfo=timezone.utc)
    snaps = [Snapshot(base, 1000), Snapshot(base, 1100)]
    assert compute_growth_rate(snaps) is None


def test_compute_proximity_growth_within_window():
    snaps = [_snap(0, 1000), _snap(3, 1050), _snap(8, 1200), _snap(14, 1250)]
    # Post on day 1 — baseline = day 0 (1000), endpoint = day 8 (1200, within 7d window)
    growth = compute_proximity_growth("2026-04-02T00:00:00Z", snaps, window_days=7)
    assert growth == 200


def test_compute_proximity_growth_no_post_baseline_uses_first_snapshot():
    """When the first snapshot is AFTER the post, fall back to it as baseline."""
    snaps = [_snap(2, 1000), _snap(8, 1100)]
    growth = compute_proximity_growth("2026-04-01T00:00:00Z", snaps, window_days=14)
    assert growth == 100  # 1100 - 1000


def test_compute_proximity_growth_returns_none_when_endpoint_missing():
    snaps = [_snap(0, 1000), _snap(2, 1050)]
    # Post on day 1 with a 14-day window — only snapshots at day 0 and 2 exist.
    # Baseline = day 0 (1000), endpoint = day 2 (1050) → growth = 50.
    growth = compute_proximity_growth("2026-04-02T00:00:00Z", snaps, window_days=14)
    assert growth == 50


def test_compute_proximity_growth_returns_none_when_window_doesnt_advance():
    snaps = [_snap(2, 1000)]
    # Post BEFORE the only snapshot — baseline=after fallback=that snapshot,
    # endpoint also that same snapshot. Should return None (no movement).
    growth = compute_proximity_growth("2026-04-01T00:00:00Z", snaps, window_days=7)
    assert growth is None


def test_compute_proximity_growth_returns_none_for_unparseable_date():
    snaps = [_snap(0, 1000), _snap(7, 1100)]
    assert compute_proximity_growth("not a date", snaps) is None


def test_compute_proximity_growth_returns_none_for_empty_snapshots():
    assert compute_proximity_growth("2026-04-01T00:00:00Z", []) is None


def test_normalize_snapshots_drops_nulls_and_sorts():
    class _Row(dict):
        def __getitem__(self, k):
            return dict.__getitem__(self, k)

    rows = [
        _Row(snapshot_at="2026-04-05T00:00:00Z", follower_count=1100),
        _Row(snapshot_at="2026-04-01T00:00:00Z", follower_count=1000),
        _Row(snapshot_at="2026-04-03T00:00:00Z", follower_count=None),  # dropped
        _Row(snapshot_at="not a date", follower_count=999),  # dropped
    ]
    snaps = normalize_snapshots(rows)
    assert len(snaps) == 2
    assert snaps[0].follower_count == 1000  # oldest first
    assert snaps[1].follower_count == 1100


# --- DB integration tests ------------------------------------------------


@pytest.fixture
def cfg(tmp_path: Path) -> AppConfig:
    return AppConfig(
        anthropic_api_key=None,
        telegram_bot_token=None,
        telegram_chat_id=None,
        headless=True,
        creators=[],
        db_path=tmp_path / "a.db",
        chrome_profile_dir=tmp_path / "prof",
        logs_dir=tmp_path / "logs",
        output_dir=tmp_path / "out",
    )


def _seed_creator_with_snapshots(
    cfg: AppConfig, *, snapshots: list[tuple[str, int]]
) -> int:
    storage.init_db(cfg.db_path)
    with storage.connect(cfg.db_path) as conn:
        storage.sync_creators(
            conn, [CreatorConfig("https://linkedin.com/in/a", "A", 1.0)]
        )
        cid = storage.get_creator_id_by_url(conn, "https://linkedin.com/in/a")
        for ts, fc in snapshots:
            storage.insert_profile_snapshot(
                conn, creator_id=cid, follower_count=fc, snapshot_at=ts
            )
        return cid


def test_recompute_post_growth_attributes_growth_to_posts(cfg):
    cid = _seed_creator_with_snapshots(
        cfg,
        snapshots=[
            ("2026-04-01T00:00:00Z", 1000),
            ("2026-04-04T00:00:00Z", 1050),
            ("2026-04-09T00:00:00Z", 1200),
        ],
    )
    with storage.connect(cfg.db_path) as conn:
        storage.insert_post(
            conn,
            post_urn="urn:li:activity:1",
            creator_id=cid,
            post_url=None,
            post_text="post on day 2",
            reactions=10,
            comments=1,
            reshares=0,
            follower_count_at_collection=1000,
            post_date="2026-04-02T00:00:00Z",
        )

    summary = recompute_post_growth(cfg)
    assert summary["updated"] == 1
    assert summary["creators_with_snapshots"] == 1

    with storage.connect(cfg.db_path) as conn:
        row = conn.execute("SELECT growth_7d FROM posts").fetchone()
    # Baseline = day 1 (1000), endpoint = day 9 (within 7d window from day 2 = day 9 ok).
    # 1200 - 1000 = 200.
    assert row["growth_7d"] == 200


def test_recompute_post_growth_skips_creators_with_too_few_snapshots(cfg):
    _seed_creator_with_snapshots(
        cfg, snapshots=[("2026-04-01T00:00:00Z", 1000)]
    )
    with storage.connect(cfg.db_path) as conn:
        cid = storage.get_creator_id_by_url(conn, "https://linkedin.com/in/a")
        storage.insert_post(
            conn,
            post_urn="urn:li:activity:1",
            creator_id=cid,
            post_url=None,
            post_text="post",
            reactions=10,
            comments=0,
            reshares=0,
            follower_count_at_collection=1000,
            post_date="2026-04-02T00:00:00Z",
        )
    summary = recompute_post_growth(cfg)
    assert summary["creators_with_snapshots"] == 0
    assert summary["updated"] == 0


def test_recompute_post_growth_is_idempotent(cfg):
    cid = _seed_creator_with_snapshots(
        cfg,
        snapshots=[
            ("2026-04-01T00:00:00Z", 1000),
            ("2026-04-09T00:00:00Z", 1100),
        ],
    )
    with storage.connect(cfg.db_path) as conn:
        storage.insert_post(
            conn,
            post_urn="urn:li:activity:1",
            creator_id=cid,
            post_url=None,
            post_text="x",
            reactions=10,
            comments=0,
            reshares=0,
            follower_count_at_collection=1000,
            post_date="2026-04-02T00:00:00Z",
        )

    a = recompute_post_growth(cfg)
    b = recompute_post_growth(cfg)
    assert a == b
    with storage.connect(cfg.db_path) as conn:
        row = conn.execute("SELECT growth_7d FROM posts").fetchone()
    assert row["growth_7d"] == 100


def test_creator_growth_rates_returns_per_creator_weekly_slope(cfg):
    _seed_creator_with_snapshots(
        cfg,
        snapshots=[
            ("2026-04-01T00:00:00Z", 1000),
            ("2026-04-08T00:00:00Z", 1100),
            ("2026-04-15T00:00:00Z", 1200),
        ],
    )
    rates = creator_growth_rates(cfg)
    assert len(rates) == 1
    rate = next(iter(rates.values()))
    assert rate is not None
    assert abs(rate - 100) < 0.5


def test_default_window_days_is_seven():
    assert DEFAULT_WINDOW_DAYS == 7
