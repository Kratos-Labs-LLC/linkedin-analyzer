#!/usr/bin/env python3
"""Day-31 analysis entrypoint: extractor -> stats -> synthesizer -> validator."""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src import storage  # noqa: E402
from src.analyzer import (  # noqa: E402
    extractor,
    growth,
    metrics,
    profile_audit,
    profile_extractor,
    profile_stats,
    profile_synthesizer,
    stats,
    synthesizer,
    validator,
)
from src.config import load_config  # noqa: E402
from src.log_setup import setup_logging  # noqa: E402

MIN_POSTS = 800
MIN_CREATORS_WITH_20 = 15
MIN_DAYS_ELAPSED = 30


def _check_readiness(cfg) -> list[str]:
    reasons: list[str] = []
    with storage.connect(cfg.db_path) as conn:
        s = storage.collection_summary(conn)

    if s["total_posts"] < MIN_POSTS:
        reasons.append(f"only {s['total_posts']} posts collected (need {MIN_POSTS})")
    if s["creators_with_20_posts"] < MIN_CREATORS_WITH_20:
        reasons.append(
            f"only {s['creators_with_20_posts']} creators have >=20 posts (need {MIN_CREATORS_WITH_20})"
        )
    if s["first_collected_at"]:
        try:
            first = datetime.fromisoformat(s["first_collected_at"].replace("Z", "+00:00"))
            if first.tzinfo is None:
                first = first.replace(tzinfo=timezone.utc)
            days = (datetime.now(timezone.utc) - first).days
            if days < MIN_DAYS_ELAPSED:
                reasons.append(f"only {days} days of collection (need {MIN_DAYS_ELAPSED})")
        except ValueError:
            reasons.append("could not parse first_collected_at")
    else:
        reasons.append("no posts collected yet")

    return reasons


def main() -> int:
    setup_logging()
    log = logging.getLogger("run_analysis")

    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Skip readiness gates.")
    parser.add_argument(
        "--leadmagnet-skill-path",
        required=True,
        type=Path,
        help="Path to existing leadmagnet-post-writer/SKILL.md for synthesis input.",
    )
    parser.add_argument("--skip-extract", action="store_true", help="Skip feature extraction step.")
    parser.add_argument("--skip-synth", action="store_true", help="Skip synthesizer (stats only).")
    parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="Skip head-to-head validation against the existing skill.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Skip extract/stats/synth; just run the validator against existing output.",
    )
    parser.add_argument(
        "--validate-n",
        type=int,
        default=validator.DEFAULT_N_PROMPTS,
        help="How many prompts to evaluate during validation (default 15).",
    )
    parser.add_argument(
        "--skip-profile",
        action="store_true",
        help="Skip profile axis (extractor + stats + profile synth).",
    )
    parser.add_argument(
        "--profile-only",
        action="store_true",
        help="Skip post extract/stats/synth/validate; only run the profile axis.",
    )
    args = parser.parse_args()

    cfg = load_config()
    storage.init_db(cfg.db_path)

    # --profile-only and --validate-only short-circuit the post pipeline.
    run_post_pipeline = not (args.validate_only or args.profile_only)
    run_profile_pipeline = not args.skip_profile and not args.validate_only
    run_validation = not args.skip_validate and not args.profile_only

    if run_post_pipeline:
        reasons = _check_readiness(cfg)
        if reasons and not args.force:
            log.error("Analysis preconditions not met:")
            for r in reasons:
                log.error("  - %s", r)
            log.error("Pass --force to run anyway.")
            return 2
        if reasons:
            log.warning("Forcing run despite: %s", "; ".join(reasons))

        # Recompute engagement scores in case follower counts changed mid-run.
        with storage.connect(cfg.db_path) as conn:
            n = storage.recompute_all_engagement_scores(conn)
            log.info("Recomputed engagement scores for %d posts.", n)

        if not args.skip_extract:
            log.info("Post step 1: feature extraction")
            extractor.extract_features(cfg)

        log.info("Post step 2: stats")
        stats_dict = stats.compute_stats(cfg)
        stats.write_stats_json(stats_dict, cfg.output_dir)
        stats.write_top_bottom_markdown(cfg, cfg.output_dir, limit=50)
        log.info("Wrote stats.json, top_posts.md, bottom_posts.md to %s", cfg.output_dir)

    # Profile pipeline runs BEFORE post synthesis when both are enabled, so the
    # post synth can read the growth-axis findings (top_growth_posts.md +
    # profile_axis under stats.json) into its prompt.
    if run_profile_pipeline:
        log.info("Profile step 1: backfill posts.growth_7d from snapshots")
        growth_summary = growth.recompute_post_growth(cfg)
        log.info(
            "  growth_7d: updated=%d skipped=%d creators_with_snapshots=%d",
            growth_summary["updated"],
            growth_summary["skipped"],
            growth_summary["creators_with_snapshots"],
        )
        stats.write_top_growth_markdown(cfg, cfg.output_dir, limit=30)

        log.info("Profile step 2: extract profile features (~$0.30)")
        profile_extract_summary = profile_extractor.extract_profile_features(cfg)
        log.info("  profile features: %s", profile_extract_summary)

        log.info("Profile step 3: profile-axis stats merged into stats.json")
        profile_axis = profile_stats.compute_profile_stats(cfg)
        profile_stats.merge_into_stats_json(profile_axis, cfg.output_dir)
        log.info(
            "  profile axis: n_creators_analyzed=%d",
            profile_axis.get("n_creators_analyzed", 0),
        )

    if run_post_pipeline and not args.skip_synth:
        log.info("Post step 3: synthesis (now profile-pull-aware)")
        out = synthesizer.synthesize(cfg, args.leadmagnet_skill_path)
        log.info("Wrote %s.", out)
    elif run_post_pipeline:
        log.info("Skipping post synthesis.")

    if run_profile_pipeline:
        log.info("Profile step 4: synthesize linkedin-profile-optimizer (~$1.30)")
        profile_skill_path = profile_synthesizer.synthesize(cfg)
        if profile_skill_path is not None:
            log.info("Wrote %s.", profile_skill_path)
        else:
            log.info("Profile skill synth skipped (insufficient data).")

        # Step 5 (gated): personal audit. Only fires when MY_PROFILE_URL is
        # set. Returns None silently otherwise — no error.
        log.info("Profile step 5: personal audit (gated on MY_PROFILE_URL, ~$0.50)")
        audit_path = profile_audit.audit(cfg)
        if audit_path is not None:
            log.info("Wrote %s.", audit_path)

    if run_validation:
        new_skill_path = cfg.output_dir / "linkedin-high-engagement-writer" / "SKILL.md"
        if not new_skill_path.exists():
            log.error(
                "Cannot validate: %s does not exist. Run synthesis first.",
                new_skill_path,
            )
            return 2

        log.info("Validation: head-to-head new vs leadmagnet (~$1)")
        result = validator.run_validation(
            cfg,
            old_skill_path=args.leadmagnet_skill_path,
            new_skill_path=new_skill_path,
            n=args.validate_n,
        )
        report_path = validator.write_report(result, cfg.output_dir)
        log.info(
            "  validation: new=%d old=%d ties=%d (win-rate %.0f%%) -> %s",
            result.new_wins,
            result.old_wins,
            result.ties,
            result.win_rate * 100,
            report_path,
        )

    # Write a single per-run metrics snapshot to output/metrics.json — read
    # by the dashboard's /metrics page and useful for ad-hoc inspection.
    metrics_path = metrics.write_metrics(cfg)
    log.info("Wrote %s.", metrics_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
