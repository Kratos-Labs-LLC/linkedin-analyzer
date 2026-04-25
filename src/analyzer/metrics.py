"""Per-run metrics — written to output/metrics.json after analysis.

Aggregates collection rate, attribution coverage, axis n-counts, and the
generation status of each output file. Cheap (single-pass over the DB +
filesystem stat) and the dashboard's /metrics page reads it as-is.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src import storage
from src.config import AppConfig


def compute_metrics(cfg: AppConfig) -> dict:
    """Snapshot of the analyzer's current state. Idempotent — safe to call
    repeatedly without side effects beyond reading from the DB and FS."""
    summary = {}

    storage.init_db(cfg.db_path)
    with storage.connect(cfg.db_path) as conn:
        s = storage.collection_summary(conn)
        active_creators = conn.execute(
            "SELECT COUNT(*) AS n FROM creators WHERE active = 1"
        ).fetchone()["n"]
        post_features = conn.execute(
            "SELECT COUNT(*) AS n FROM post_features"
        ).fetchone()["n"]
        profile_features = conn.execute(
            "SELECT COUNT(*) AS n FROM profile_features"
        ).fetchone()["n"]
        snapshots = conn.execute(
            "SELECT COUNT(*) AS n FROM creator_profiles"
        ).fetchone()["n"]
        posts_with_growth = conn.execute(
            "SELECT COUNT(*) AS n FROM posts WHERE growth_7d IS NOT NULL"
        ).fetchone()["n"]
        avg_engagement = conn.execute(
            "SELECT AVG(engagement_score) AS s FROM posts "
            "WHERE engagement_score IS NOT NULL"
        ).fetchone()["s"]
        recent_runs = [
            dict(r) for r in storage.recent_runs(conn, limit=5)
        ]
        # Storage size: rough byte count of raw_html in each table.
        raw_html_bytes_posts = conn.execute(
            "SELECT COALESCE(SUM(LENGTH(raw_html)), 0) AS b FROM posts"
        ).fetchone()["b"]
        raw_html_bytes_profiles = conn.execute(
            "SELECT COALESCE(SUM(LENGTH(raw_html)), 0) AS b FROM creator_profiles"
        ).fetchone()["b"]

    summary["generated_at"] = datetime.now(timezone.utc).isoformat()
    summary["collection"] = {
        "total_posts": s["total_posts"],
        "first_collected_at": s["first_collected_at"],
        "active_creators": active_creators,
        "creators_with_20_posts": s["creators_with_20_posts"],
        "snapshots": snapshots,
        "avg_engagement_score": avg_engagement,
    }
    summary["coverage"] = {
        "posts_with_features": post_features,
        "profiles_with_features": profile_features,
        "posts_with_growth_7d": posts_with_growth,
        "feature_coverage_pct": (
            round(100 * post_features / s["total_posts"], 1)
            if s["total_posts"]
            else 0.0
        ),
        "growth_coverage_pct": (
            round(100 * posts_with_growth / s["total_posts"], 1)
            if s["total_posts"]
            else 0.0
        ),
    }
    summary["storage_bytes"] = {
        "raw_html_in_posts": raw_html_bytes_posts,
        "raw_html_in_profiles": raw_html_bytes_profiles,
    }
    summary["outputs"] = _output_status(cfg.output_dir)
    summary["recent_runs"] = recent_runs
    return summary


def _output_status(output_dir: Path) -> dict:
    """For each artifact the pipeline produces, report present/missing + bytes."""
    files = {
        "stats_json": output_dir / "stats.json",
        "top_posts_md": output_dir / "top_posts.md",
        "bottom_posts_md": output_dir / "bottom_posts.md",
        "top_growth_posts_md": output_dir / "top_growth_posts.md",
        "validation_md": output_dir / "validation.md",
        "writer_skill_md": output_dir / "linkedin-high-engagement-writer" / "SKILL.md",
        "profile_skill_md": output_dir / "linkedin-profile-optimizer" / "SKILL.md",
        "profile_audit_md": output_dir / "profile_audit.md",
        "synthesis_draft_md": output_dir / "synthesis_draft.md",
        "profile_synthesis_draft_md": output_dir / "profile_synthesis_draft.md",
    }
    out = {}
    for name, p in files.items():
        if p.exists():
            out[name] = {"present": True, "size_bytes": p.stat().st_size}
        else:
            out[name] = {"present": False}
    return out


def write_metrics(cfg: AppConfig) -> Path:
    metrics = compute_metrics(cfg)
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.output_dir / "metrics.json"
    path.write_text(json.dumps(metrics, indent=2, default=str))
    return path
