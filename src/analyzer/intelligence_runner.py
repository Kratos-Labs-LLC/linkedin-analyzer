"""Orchestrate per-creator intelligence brief generation.

Filters eligible creators, builds the cohort baseline once, then iterates
each creator: build pack -> synth markdown -> write
output/intelligence/<slug>.md (and <slug>.pack.json alongside).

The brief is the human artifact. The pack JSON is the machine artifact;
the dashboard reads it for the per-creator drill-down. Both are written
on every successful synth.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from anthropic import Anthropic

from src import storage
from src.analyzer import intelligence
from src.analyzer.intelligence_synth import synthesize_intelligence
from src.config import AppConfig

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Slug helpers (must match dashboard/lib/db.ts:creatorSlug)
# ---------------------------------------------------------------------


def creator_slug(*, display_name: str | None, creator_id: int) -> str:
    """Stable file-safe slug used for the intelligence doc filename.

    Same algorithm runs in dashboard/lib/db.ts:creatorSlug — keep them
    aligned. Falls back to `creator-<id>` when the display name is empty
    or strips down to nothing.
    """
    base = (display_name or "").lower().strip()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    if not base:
        return f"creator-{creator_id}"
    return f"{base}-{creator_id}"


# ---------------------------------------------------------------------
# Output dir resolution
# ---------------------------------------------------------------------


def intelligence_dir(cfg: AppConfig) -> Path:
    return cfg.output_dir / "intelligence"


# ---------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------


def run_intelligence(
    cfg: AppConfig,
    *,
    target_creator_url: str | None = None,
    client: Anthropic | None = None,
) -> dict:
    """Generate intelligence briefs. Returns counters for logging.

    `target_creator_url` filters to a single creator. The cohort baseline
    is still computed across the full eligible pool so a single-creator
    run yields the same data as that creator's slot in a full run.
    """
    if client is None:
        if not cfg.anthropic_api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set; cannot run intelligence."
            )
        client = Anthropic(api_key=cfg.anthropic_api_key)

    out_dir = intelligence_dir(cfg)
    out_dir.mkdir(parents=True, exist_ok=True)

    counters = {"eligible": 0, "synthesized": 0, "skipped": 0, "failed": 0}

    with storage.connect(cfg.db_path) as conn:
        baseline = intelligence.build_cohort_baseline(conn)
        if baseline["n_creators_in_baseline"] == 0:
            log.warning(
                "No eligible creators (need >= %d featured posts). Skipping intelligence.",
                intelligence.MIN_FEATURED_POSTS,
            )
            return counters

        # Resolve target if specified.
        target_id: int | None = None
        if target_creator_url:
            row = conn.execute(
                "SELECT id FROM creators WHERE linkedin_url = ?",
                (target_creator_url,),
            ).fetchone()
            if row is None:
                log.error(
                    "Target creator url not found: %s", target_creator_url
                )
                return counters
            target_id = int(row["id"])

        if target_id is not None:
            eligible_ids = [target_id] if target_id in intelligence.eligible_creator_ids(conn) else []
            if not eligible_ids:
                log.warning(
                    "Target creator %s has fewer than %d featured posts; skipping.",
                    target_creator_url,
                    intelligence.MIN_FEATURED_POSTS,
                )
                return counters
        else:
            eligible_ids = intelligence.eligible_creator_ids(conn)

        counters["eligible"] = len(eligible_ids)
        log.info(
            "intelligence: synthesizing %d brief(s); cohort baseline n_creators=%d, n_posts=%d",
            len(eligible_ids),
            baseline["n_creators_in_baseline"],
            baseline["n_posts_in_baseline"],
        )

        for cid in eligible_ids:
            pack = intelligence.build_intelligence_pack(conn, cid, baseline)
            if pack is None:
                counters["skipped"] += 1
                continue

            slug = creator_slug(
                display_name=pack["creator"].get("display_name"),
                creator_id=cid,
            )

            try:
                doc = synthesize_intelligence(
                    pack, baseline, client=client
                )
            except Exception as exc:  # noqa: BLE001
                log.exception("Synth failed for creator %s: %s", cid, exc)
                counters["failed"] += 1
                continue

            md_path = out_dir / f"{slug}.md"
            json_path = out_dir / f"{slug}.pack.json"
            md_path.write_text(doc)
            json_path.write_text(json.dumps(pack, indent=2, default=str))
            counters["synthesized"] += 1
            log.info(
                "intelligence: wrote %s (%d chars) + pack.json",
                md_path,
                len(doc),
            )

    return counters
