"""Profile-axis statistics — correlate profile features with creator-level
follower-growth-per-week.

Reuses src/analyzer/stats.py:_stats_for_subset for the actual aggregation;
this module just builds the right (score, features) parsed list and parks
the result under stats.json's `profile_axis` key for the synthesizer to
consume.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src import storage
from src.analyzer.growth import creator_growth_rates
from src.analyzer.stats import _stats_for_subset
from src.config import AppConfig

log = logging.getLogger(__name__)

# Profile feature schema (mirrors src/analyzer/profile_extractor.py:EXPECTED_KEYS)
PROFILE_CATEGORICAL_KEYS = {
    "headline_style",
    "about_voice",
    "niche_breadth",
    "audience_alignment",
    "featured_section_type",
}
PROFILE_NUMERIC_KEYS = {
    "headline_specificity",
    "about_promise_clarity",
    "about_proof_density",
    "post_to_profile_match_score",
}
PROFILE_BOOLEAN_KEYS = {"cta_in_about"}


def compute_profile_stats(cfg: AppConfig) -> dict:
    """Build the profile-axis stats blob.

    Score = follower_growth_rate_per_week per creator. Aggregations run only
    over creators that have BOTH a non-null growth rate (≥2 snapshots) AND a
    profile_features row. Output shape mirrors stats.compute_stats so the
    synthesizer prompt can look at it the same way.
    """
    rates = creator_growth_rates(cfg)

    parsed: list[tuple[float, dict]] = []
    creators_with_features = 0
    creators_without_features = 0
    creators_without_rate = 0

    with storage.connect(cfg.db_path) as conn:
        # Only ingest features for currently-active creators. If a creator
        # was removed from creators.yaml and later re-added, their old
        # features row survives in the DB; without this filter, stale data
        # from an inactive period would skew the profile-axis analysis.
        feature_rows = conn.execute(
            """
            SELECT pf.creator_id, pf.features_json
            FROM profile_features pf
            JOIN creators c ON c.id = pf.creator_id
            WHERE c.active = 1
            """
        ).fetchall()
        feats_by_creator: dict[int, dict[str, Any]] = {}
        for r in feature_rows:
            try:
                feats_by_creator[r["creator_id"]] = json.loads(r["features_json"])
            except json.JSONDecodeError:
                continue

        creators = conn.execute(
            "SELECT id, display_name, linkedin_url FROM creators WHERE active = 1"
        ).fetchall()

    creator_summaries: list[dict] = []

    for c in creators:
        cid = c["id"]
        rate = rates.get(cid)
        feats = feats_by_creator.get(cid)
        creator_summaries.append({
            "creator_id": cid,
            "display_name": c["display_name"],
            "growth_rate_per_week": rate,
            "has_features": feats is not None,
        })
        if feats is None:
            creators_without_features += 1
            continue
        if rate is None:
            creators_without_rate += 1
            continue
        creators_with_features += 1
        parsed.append((rate, feats))

    # Sort by growth rate descending — _stats_for_subset expects this.
    parsed.sort(key=lambda t: t[0], reverse=True)

    pooled = _stats_for_subset(
        parsed,
        categorical_keys=PROFILE_CATEGORICAL_KEYS,
        numeric_keys=PROFILE_NUMERIC_KEYS,
        boolean_keys=PROFILE_BOOLEAN_KEYS,
    )

    # Top creators by growth (the names worth quoting in the synthesized skill).
    creator_summaries.sort(
        key=lambda s: (s["growth_rate_per_week"] is not None, s["growth_rate_per_week"] or 0),
        reverse=True,
    )

    return {
        **pooled,
        "n_creators_analyzed": creators_with_features,
        "creators_without_features": creators_without_features,
        "creators_without_rate": creators_without_rate,
        "score_field": "growth_rate_per_week",
        "score_units": "followers per week (linear-fit slope over 30-day window)",
        "creator_growth_summary": [
            {
                "display_name": s["display_name"],
                "growth_rate_per_week": s["growth_rate_per_week"],
            }
            for s in creator_summaries[:25]  # cap output size
        ],
    }


def merge_into_stats_json(profile_axis: dict, output_dir: Path) -> Path:
    """Read existing stats.json (written by stats.write_stats_json), add a
    `profile_axis` field, and write back. Returns the file path.

    Idempotent: re-running with a different profile_axis overwrites cleanly.
    """
    path = output_dir / "stats.json"
    base: dict = {}
    if path.exists():
        try:
            base = json.loads(path.read_text())
        except json.JSONDecodeError:
            log.warning("Existing stats.json could not be parsed — overwriting cleanly.")
            base = {}
    base["profile_axis"] = profile_axis
    output_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(base, indent=2))
    return path
