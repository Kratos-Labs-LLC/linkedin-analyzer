import json
from pathlib import Path

import pytest

from src import storage
from src.analyzer import profile_stats
from src.config import AppConfig, CreatorConfig


def _cfg(tmp_path: Path) -> AppConfig:
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


def _seed_two_creators_with_diverging_growth(cfg: AppConfig) -> None:
    """Creator A: high growth (1000 -> 1500 over 14 days). Profile uses
    benefit-led headline, narrow niche.
    Creator B: flat growth. Profile uses role-only headline, broad niche.
    Profile-axis stats should rank benefit-led + narrow above role-only +
    broad."""
    storage.init_db(cfg.db_path)
    with storage.connect(cfg.db_path) as conn:
        storage.sync_creators(
            conn,
            [
                CreatorConfig("https://linkedin.com/in/a", "A", 1.0),
                CreatorConfig("https://linkedin.com/in/b", "B", 1.0),
            ],
        )
        cid_a = storage.get_creator_id_by_url(conn, "https://linkedin.com/in/a")
        cid_b = storage.get_creator_id_by_url(conn, "https://linkedin.com/in/b")

        # A snapshots — strong growth
        for days, fc in [(0, 1000), (7, 1250), (14, 1500)]:
            storage.insert_profile_snapshot(
                conn,
                creator_id=cid_a,
                follower_count=fc,
                snapshot_at=f"2026-04-{1 + days:02d}T00:00:00Z",
            )
        # B snapshots — flat
        for days, fc in [(0, 5000), (7, 5005), (14, 5010)]:
            storage.insert_profile_snapshot(
                conn,
                creator_id=cid_b,
                follower_count=fc,
                snapshot_at=f"2026-04-{1 + days:02d}T00:00:00Z",
            )

        # Profile features — diverging
        a_feats = {
            "headline_style": "benefit-led",
            "headline_specificity": 8,
            "about_voice": "practical",
            "about_promise_clarity": 9,
            "about_proof_density": 8,
            "niche_breadth": "narrow",
            "audience_alignment": "founders",
            "cta_in_about": True,
            "featured_section_type": "lead_magnets",
            "post_to_profile_match_score": 9,
        }
        b_feats = {
            "headline_style": "role-only",
            "headline_specificity": 2,
            "about_voice": "inspirational",
            "about_promise_clarity": 3,
            "about_proof_density": 2,
            "niche_breadth": "broad",
            "audience_alignment": "mixed",
            "cta_in_about": False,
            "featured_section_type": "none",
            "post_to_profile_match_score": 4,
        }
        for cid, feats in [(cid_a, a_feats), (cid_b, b_feats)]:
            conn.execute(
                "INSERT INTO profile_features (creator_id, features_json) VALUES (?, ?)",
                (cid, json.dumps(feats)),
            )


def test_compute_profile_stats_returns_axis_shape(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _seed_two_creators_with_diverging_growth(cfg)

    axis = profile_stats.compute_profile_stats(cfg)

    assert axis["n_creators_analyzed"] == 2
    assert axis["score_field"] == "growth_rate_per_week"
    assert "categorical_features" in axis
    assert "numeric_features" in axis
    # Sanity: benefit-led ranked above role-only because A grows, B doesn't.
    hook = axis["categorical_features"]["headline_style"]
    assert hook["benefit-led"]["rank"] == 1
    assert hook["role-only"]["rank"] == 2


def test_compute_profile_stats_skips_creators_without_features(tmp_path: Path):
    cfg = _cfg(tmp_path)
    storage.init_db(cfg.db_path)
    with storage.connect(cfg.db_path) as conn:
        storage.sync_creators(conn, [CreatorConfig("https://linkedin.com/in/x", "X", 1.0)])
        cid = storage.get_creator_id_by_url(conn, "https://linkedin.com/in/x")
        for days, fc in [(0, 1000), (7, 1100)]:
            storage.insert_profile_snapshot(
                conn, creator_id=cid, follower_count=fc,
                snapshot_at=f"2026-04-{1 + days:02d}T00:00:00Z",
            )
    axis = profile_stats.compute_profile_stats(cfg)
    assert axis["n_creators_analyzed"] == 0
    assert axis["creators_without_features"] == 1


def test_compute_profile_stats_excludes_inactive_creators(tmp_path: Path):
    """B3: a deactivated creator's profile_features row must not contaminate
    the analysis. Reproduce the bug by setting active=0 after seeding."""
    cfg = _cfg(tmp_path)
    _seed_two_creators_with_diverging_growth(cfg)

    # Now deactivate creator A — their stale features should drop out.
    with storage.connect(cfg.db_path) as conn:
        conn.execute(
            "UPDATE creators SET active = 0 WHERE linkedin_url = ?",
            ("https://linkedin.com/in/a",),
        )

    axis = profile_stats.compute_profile_stats(cfg)
    # B alone is left, so n_creators_analyzed is 1.
    assert axis["n_creators_analyzed"] == 1


def test_compute_profile_stats_skips_creators_without_growth_rate(tmp_path: Path):
    cfg = _cfg(tmp_path)
    storage.init_db(cfg.db_path)
    with storage.connect(cfg.db_path) as conn:
        storage.sync_creators(conn, [CreatorConfig("https://linkedin.com/in/x", "X", 1.0)])
        cid = storage.get_creator_id_by_url(conn, "https://linkedin.com/in/x")
        # Only one snapshot — growth rate is None
        storage.insert_profile_snapshot(
            conn, creator_id=cid, follower_count=1000,
            snapshot_at="2026-04-01T00:00:00Z",
        )
        conn.execute(
            "INSERT INTO profile_features (creator_id, features_json) VALUES (?, ?)",
            (cid, json.dumps({k: 0 for k in profile_stats.PROFILE_NUMERIC_KEYS})),
        )
    axis = profile_stats.compute_profile_stats(cfg)
    assert axis["n_creators_analyzed"] == 0
    assert axis["creators_without_rate"] == 1


def test_creator_growth_summary_orders_top_first(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _seed_two_creators_with_diverging_growth(cfg)
    axis = profile_stats.compute_profile_stats(cfg)
    summary = axis["creator_growth_summary"]
    assert len(summary) >= 2
    # A's growth is much higher than B's.
    assert summary[0]["display_name"] == "A"


def test_merge_into_stats_json_creates_file_when_absent(tmp_path: Path):
    cfg = _cfg(tmp_path)
    cfg.output_dir.mkdir()
    profile_axis = {"n_creators_analyzed": 2}
    path = profile_stats.merge_into_stats_json(profile_axis, cfg.output_dir)
    assert path.exists()
    parsed = json.loads(path.read_text())
    assert parsed["profile_axis"]["n_creators_analyzed"] == 2


def test_merge_into_stats_json_preserves_existing_post_axis(tmp_path: Path):
    cfg = _cfg(tmp_path)
    cfg.output_dir.mkdir()
    (cfg.output_dir / "stats.json").write_text(
        json.dumps({"n_posts_analyzed": 100, "categorical_features": {"hook_type": {}}})
    )
    profile_stats.merge_into_stats_json({"n_creators_analyzed": 5}, cfg.output_dir)
    parsed = json.loads((cfg.output_dir / "stats.json").read_text())
    assert parsed["n_posts_analyzed"] == 100
    assert parsed["profile_axis"]["n_creators_analyzed"] == 5


def test_merge_into_stats_json_overwrites_previous_profile_axis(tmp_path: Path):
    cfg = _cfg(tmp_path)
    cfg.output_dir.mkdir()
    (cfg.output_dir / "stats.json").write_text(
        json.dumps({"profile_axis": {"old": True}})
    )
    profile_stats.merge_into_stats_json({"new": True}, cfg.output_dir)
    parsed = json.loads((cfg.output_dir / "stats.json").read_text())
    assert parsed["profile_axis"] == {"new": True}


def test_merge_into_stats_json_recovers_from_unparseable_file(tmp_path: Path):
    cfg = _cfg(tmp_path)
    cfg.output_dir.mkdir()
    (cfg.output_dir / "stats.json").write_text("{garbage")
    profile_stats.merge_into_stats_json({"x": 1}, cfg.output_dir)
    parsed = json.loads((cfg.output_dir / "stats.json").read_text())
    assert parsed == {"profile_axis": {"x": 1}}
