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
from src.analyzer import extractor, stats, synthesizer, validator  # noqa: E402
from src.config import load_config  # noqa: E402

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
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
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
    args = parser.parse_args()

    cfg = load_config()
    storage.init_db(cfg.db_path)

    if not args.validate_only:
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
            log.info("Step 1/4: feature extraction")
            extractor.extract_features(cfg)

        log.info("Step 2/4: stats")
        stats_dict = stats.compute_stats(cfg)
        stats.write_stats_json(stats_dict, cfg.output_dir)
        stats.write_top_bottom_markdown(cfg, cfg.output_dir, limit=50)
        log.info("Wrote stats.json, top_posts.md, bottom_posts.md to %s", cfg.output_dir)

        if args.skip_synth:
            log.info("Skipping synthesis.")
            if args.skip_validate:
                return 0
        else:
            log.info("Step 3/4: synthesis")
            out = synthesizer.synthesize(cfg, args.leadmagnet_skill_path)
            log.info("Wrote %s. Review, then install to ~/.claude/skills/.", out)

    if args.skip_validate:
        return 0

    new_skill_path = cfg.output_dir / "linkedin-high-engagement-writer" / "SKILL.md"
    if not new_skill_path.exists():
        log.error("Cannot validate: %s does not exist. Run synthesis first.", new_skill_path)
        return 2

    log.info("Step 4/4: validation (this calls Claude ~%d times, ~$1)", args.validate_n * 3)
    result = validator.run_validation(
        cfg,
        old_skill_path=args.leadmagnet_skill_path,
        new_skill_path=new_skill_path,
        n=args.validate_n,
    )
    report_path = validator.write_report(result, cfg.output_dir)
    log.info(
        "Validation: new=%d old=%d ties=%d (win-rate %.0f%%) -> %s",
        result.new_wins,
        result.old_wins,
        result.ties,
        result.win_rate * 100,
        report_path,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
