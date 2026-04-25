"""Personal profile audit — gated on MY_PROFILE_URL.

If the user sets MY_PROFILE_URL in .env, this step:
  1. Ad-hoc scrapes the profile page using the same persistent Chrome
     profile as the collector. (Burner is logged in; LinkedIn shows the
     same public profile content to any logged-in viewer.)
  2. Extracts features via the same prompt as profile_extractor.
  3. Calls Opus once with the user's profile + a compact summary of the
     learned patterns and asks for a concrete, prioritized audit.
  4. Writes output/profile_audit.md.

The user's profile does NOT enter the comparable creator pool — it's
scraped ad-hoc, never persisted to creator_profiles or profile_features.
That keeps the analysis honest.

Cost: ~$0.50 per run (one Sonnet feature call + one Opus audit call).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable

from anthropic import Anthropic

from src.config import AppConfig
from src.profile_parser import ProfileSnapshot, parse_profile_attributes
from src.analyzer.profile_extractor import (
    INSTRUCTION_BLOCK as PROFILE_EXTRACT_INSTRUCTION,
    MODEL as EXTRACT_MODEL,
    MAX_TOKENS as EXTRACT_MAX_TOKENS,
    _build_user_message as build_extract_user_message,
    _parse_and_validate as parse_extract_response,
)
from src.analyzer.validator import _stats_summary  # reuses the proven compactor

log = logging.getLogger(__name__)

AUDIT_MODEL = "claude-opus-4-7"
AUDIT_MAX_TOKENS = 4_000


# --- Live scrape ---------------------------------------------------------


async def _scrape_my_profile_async(cfg: AppConfig, url: str) -> ProfileSnapshot:
    """Single-shot Playwright fetch of MY_PROFILE_URL through the burner
    persistent context."""
    from playwright.async_api import async_playwright  # local import: heavy

    if not cfg.chrome_profile_dir.exists():
        raise RuntimeError(
            f"chrome_profile not found at {cfg.chrome_profile_dir}. "
            "Run scripts/auth_setup.py first."
        )

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(cfg.chrome_profile_dir),
            headless=cfg.headless,
            viewport={"width": 1920, "height": 1080},
        )
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            html = await page.content()
        finally:
            await context.close()

    return parse_profile_attributes(html)


def _scrape_my_profile_sync(cfg: AppConfig, url: str) -> ProfileSnapshot:
    return asyncio.run(_scrape_my_profile_async(cfg, url))


# --- Feature extraction (reuses the profile_extractor prompt) ------------


def _profile_to_extractor_row(snapshot: ProfileSnapshot) -> dict[str, Any]:
    """Adapt the in-memory ProfileSnapshot into the row-shape the existing
    profile_extractor user-message builder expects."""
    return {
        "headline": snapshot.headline,
        "about_text": snapshot.about_text,
        "current_role": snapshot.current_role,
        "current_company": snapshot.current_company,
        "location": snapshot.location,
        "has_profile_photo": 1 if snapshot.has_profile_photo else 0,
        "has_banner": 1 if snapshot.has_banner else 0,
        "featured_count": snapshot.featured_count,
        "follower_count": snapshot.follower_count,
    }


def _extract_features(client: Anthropic, snapshot: ProfileSnapshot) -> dict | None:
    row = _profile_to_extractor_row(snapshot)
    resp = client.messages.create(
        model=EXTRACT_MODEL,
        max_tokens=EXTRACT_MAX_TOKENS,
        temperature=0.2,
        system=[
            {
                "type": "text",
                "text": PROFILE_EXTRACT_INSTRUCTION,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                # No top posts — this is the user's own profile and we're not
                # judging post-to-profile alignment yet (could add later).
                "content": build_extract_user_message(profile_row=row, top_posts=[]),
            }
        ],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    return parse_extract_response(text)


# --- Audit prompt --------------------------------------------------------


AUDIT_SYSTEM = """You are auditing a single LinkedIn profile against an empirical engagement profile derived from 25-30 high-performing peers.

Goal: produce a concise, prioritized rewrite plan. Be direct, cite the data, and write in the user's voice profile when proposing copy.

Output a Markdown report with these sections in order:
1. **Verdict** — one sentence: where this profile sits on the pull spectrum (high / mid / low) given the empirical patterns.
2. **Top 3 fixes** — the three highest-leverage changes, each with: what's wrong now, why the data says it's wrong, the exact rewrite. Number them.
3. **Headline rewrite** — at least two new headline options that match the top-ranked headline_style + specificity from the data.
4. **About rewrite** — first paragraph rewritten to score higher on `about_promise_clarity` and `about_proof_density`. Keep it ≤ 4 sentences.
5. **Featured section action** — what specific items to feature (or remove), based on `featured_section_type` rankings.
6. **Profile-post alignment** — does the profile claim match the topics they post about? If not, propose either profile copy changes or a posting-cadence shift.
7. **What NOT to change** — anything in the current profile that already matches the high-pull pattern. Don't fix what isn't broken.

FORMAT:
- Markdown only. No code fences around the whole document.
- Cite specific data points: "headline_style:benefit-led ranks #1 with N=X creators..."
- No generic advice. Every claim must reference the data."""


def _build_audit_user_message(
    *,
    snapshot: ProfileSnapshot,
    features: dict | None,
    stats_summary: str,
    voice_profile: str,
) -> str:
    feats_block = json.dumps(features, indent=2) if features else "(extraction failed)"
    return (
        "<your_profile>\n"
        f"Headline: {snapshot.headline or '(missing)'}\n"
        f"Current role: {snapshot.current_role or '(unknown)'}\n"
        f"Current company: {snapshot.current_company or '(unknown)'}\n"
        f"Followers: {snapshot.follower_count or 'unknown'}\n"
        f"Featured count: {snapshot.featured_count or 0}\n"
        f"Has profile photo: {'yes' if snapshot.has_profile_photo else 'no'}\n"
        f"Has banner: {'yes' if snapshot.has_banner else 'no'}\n"
        f"About:\n{snapshot.about_text or '(missing)'}\n"
        "</your_profile>\n\n"
        "<your_profile_features>\n"
        f"{feats_block}\n"
        "</your_profile_features>\n\n"
        "<empirical_patterns>\n"
        f"{stats_summary}\n"
        "</empirical_patterns>\n\n"
        "<voice_profile>\n"
        f"{voice_profile}\n"
        "</voice_profile>\n\n"
        "Produce the audit now."
    )


def _generate_audit(
    client: Anthropic,
    *,
    snapshot: ProfileSnapshot,
    features: dict | None,
    stats_summary: str,
    voice_profile: str,
) -> str:
    resp = client.messages.create(
        model=AUDIT_MODEL,
        max_tokens=AUDIT_MAX_TOKENS,
        temperature=0.5,
        system=[{"type": "text", "text": AUDIT_SYSTEM}],
        messages=[
            {
                "role": "user",
                "content": _build_audit_user_message(
                    snapshot=snapshot,
                    features=features,
                    stats_summary=stats_summary,
                    voice_profile=voice_profile,
                ),
            }
        ],
    )
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()


# --- Public API ----------------------------------------------------------


def audit(
    cfg: AppConfig,
    *,
    scrape_fn: Callable[[AppConfig, str], ProfileSnapshot] | None = None,
    voice_profile: str | None = None,
) -> Path | None:
    """Run the audit. Returns the path to profile_audit.md, or None if
    MY_PROFILE_URL is unset (audit deliberately skipped).

    `scrape_fn` exists for testing — production callers should leave it
    None to use the Playwright-backed scraper.
    """
    url = os.environ.get("MY_PROFILE_URL")
    if not url:
        log.info("Skipping profile audit: MY_PROFILE_URL not set.")
        return None

    if not cfg.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set; cannot run profile audit.")

    stats_path = cfg.output_dir / "stats.json"
    if not stats_path.exists():
        raise FileNotFoundError(
            f"stats.json not found at {stats_path}. Run analysis first."
        )

    log.info("Profile audit: scraping %s ...", url)
    scraper = scrape_fn or _scrape_my_profile_sync
    snapshot = scraper(cfg, url)

    if not snapshot.headline and not snapshot.about_text:
        log.warning(
            "Audit scrape returned no headline or about text. "
            "Burner may not be logged in, or the profile selectors have rotated."
        )

    if voice_profile is None:
        # Lazy import to avoid a circular when synthesizer.py imports nothing
        # from this module — but if the user runs audit-only without going
        # through synthesize, the voice profile constant is still where we
        # want to read it.
        from src.analyzer.synthesizer import DUGG_VOICE_PROFILE

        voice_profile = DUGG_VOICE_PROFILE

    client = Anthropic(api_key=cfg.anthropic_api_key)

    log.info("Profile audit: extracting features ...")
    features = _extract_features(client, snapshot)
    if features is None:
        log.warning("Feature extraction failed — proceeding with profile text only.")

    stats_summary = _stats_summary(stats_path)

    log.info("Profile audit: generating audit (Opus, ~$0.50) ...")
    audit_md = _generate_audit(
        client,
        snapshot=snapshot,
        features=features,
        stats_summary=stats_summary,
        voice_profile=voice_profile,
    )

    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    out_path = cfg.output_dir / "profile_audit.md"
    out_path.write_text(audit_md)
    log.info("Wrote %s.", out_path)
    return out_path
