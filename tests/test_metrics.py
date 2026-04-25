import json
from pathlib import Path

import pytest

from src import storage
from src.analyzer import metrics
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


def test_compute_metrics_on_empty_db(tmp_path: Path):
    cfg = _cfg(tmp_path)
    storage.init_db(cfg.db_path)
    m = metrics.compute_metrics(cfg)
    assert m["collection"]["total_posts"] == 0
    assert m["collection"]["active_creators"] == 0
    assert m["coverage"]["feature_coverage_pct"] == 0.0
    assert m["coverage"]["growth_coverage_pct"] == 0.0
    # All output flags absent
    assert all(v["present"] is False for v in m["outputs"].values())


def test_compute_metrics_with_seeded_data(tmp_path: Path):
    cfg = _cfg(tmp_path)
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

        for i in range(5):
            storage.insert_post(
                conn,
                post_urn=f"urn:li:activity:{i}",
                creator_id=cid_a,
                post_url=None,
                post_text=f"post {i}",
                reactions=10 + i,
                comments=i,
                reshares=0,
                follower_count_at_collection=1000,
                post_date=None,
            )
        # Two with growth_7d set
        conn.execute("UPDATE posts SET growth_7d = 50 WHERE post_urn LIKE 'urn:%:0'")
        conn.execute("UPDATE posts SET growth_7d = 30 WHERE post_urn LIKE 'urn:%:1'")
        # One feature row
        post_id = conn.execute("SELECT id FROM posts LIMIT 1").fetchone()["id"]
        conn.execute(
            "INSERT INTO post_features (post_id, features_json) VALUES (?, ?)",
            (post_id, json.dumps({"hook_type": "bold_claim"})),
        )
        # One profile snapshot
        storage.insert_profile_snapshot(
            conn, creator_id=cid_a, follower_count=1000, raw_html="<x/>"
        )

    m = metrics.compute_metrics(cfg)
    assert m["collection"]["total_posts"] == 5
    assert m["collection"]["active_creators"] == 2
    assert m["collection"]["snapshots"] == 1
    assert m["coverage"]["posts_with_features"] == 1
    assert m["coverage"]["posts_with_growth_7d"] == 2
    assert m["coverage"]["feature_coverage_pct"] == 20.0
    assert m["coverage"]["growth_coverage_pct"] == 40.0
    assert m["storage_bytes"]["raw_html_in_profiles"] == len("<x/>")


def test_write_metrics_creates_file(tmp_path: Path):
    cfg = _cfg(tmp_path)
    storage.init_db(cfg.db_path)
    path = metrics.write_metrics(cfg)
    assert path.exists()
    parsed = json.loads(path.read_text())
    assert parsed["collection"]["total_posts"] == 0
    assert "generated_at" in parsed


def test_metrics_reports_output_artifacts(tmp_path: Path):
    cfg = _cfg(tmp_path)
    cfg.output_dir.mkdir()
    (cfg.output_dir / "stats.json").write_text("{}")
    (cfg.output_dir / "linkedin-high-engagement-writer").mkdir()
    (cfg.output_dir / "linkedin-high-engagement-writer" / "SKILL.md").write_text("# x")
    storage.init_db(cfg.db_path)

    m = metrics.compute_metrics(cfg)
    assert m["outputs"]["stats_json"]["present"] is True
    assert m["outputs"]["writer_skill_md"]["present"] is True
    assert m["outputs"]["profile_skill_md"]["present"] is False


# --- Intelligence section ---------------------------------------------


def test_intelligence_section_empty_when_no_briefs(tmp_path: Path):
    cfg = _cfg(tmp_path)
    storage.init_db(cfg.db_path)
    m = metrics.compute_metrics(cfg)
    intel = m["intelligence"]
    assert intel["n_creators_with_doc"] == 0
    assert intel["total_md_bytes"] == 0
    assert intel["total_pack_bytes"] == 0
    assert intel["n_stub_docs"] == 0
    assert intel["last_intelligence_run_at"] is None


def test_intelligence_section_counts_briefs_and_packs(tmp_path: Path):
    cfg = _cfg(tmp_path)
    storage.init_db(cfg.db_path)
    intel_dir = cfg.output_dir / "intelligence"
    intel_dir.mkdir(parents=True)
    (intel_dir / "alice-1.md").write_text("## TL;DR\nbody")
    (intel_dir / "alice-1.pack.json").write_text('{"creator": {"id": 1}}')
    (intel_dir / "bob-2.md").write_text("## TL;DR\nbody")
    (intel_dir / "bob-2.pack.json").write_text('{"creator": {"id": 2}}')

    m = metrics.compute_metrics(cfg)
    intel = m["intelligence"]
    assert intel["n_creators_with_doc"] == 2
    assert intel["total_md_bytes"] > 0
    assert intel["total_pack_bytes"] > 0
    assert intel["n_stub_docs"] == 0
    assert intel["last_intelligence_run_at"] is not None


def test_intelligence_section_flags_stub_docs(tmp_path: Path):
    cfg = _cfg(tmp_path)
    storage.init_db(cfg.db_path)
    intel_dir = cfg.output_dir / "intelligence"
    intel_dir.mkdir(parents=True)
    (intel_dir / "broken-1.md").write_text(
        "# Intelligence brief: Broken (auto-stub)\n\nSynthesizer fallback."
    )
    (intel_dir / "good-2.md").write_text(
        "## TL;DR\n\nReal brief content with actual analysis."
    )

    m = metrics.compute_metrics(cfg)
    intel = m["intelligence"]
    assert intel["n_creators_with_doc"] == 2
    assert intel["n_stub_docs"] == 1
