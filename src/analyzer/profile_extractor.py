"""Claude feature extraction for LinkedIn profiles.

One Sonnet call per active creator over the latest profile snapshot, plus a
small sample of that creator's top posts (for the post_to_profile_match_score
field — Claude needs to see both surfaces to judge alignment).

Same prompt-cache + retry shape as src/analyzer/extractor.py for posts.
~25 calls per day-31 run, ~$0.30.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from anthropic import Anthropic

from src import storage
from src.analyzer._retry import with_retry
from src.config import AppConfig

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-5"
BATCH_PER_MINUTE = 10
MAX_TOKENS = 1_500
TOP_POSTS_PER_CREATOR = 3   # how many recent top posts we show the model

EXPECTED_KEYS = {
    "headline_style",
    "headline_specificity",
    "about_voice",
    "about_promise_clarity",
    "about_proof_density",
    "niche_breadth",
    "audience_alignment",
    "cta_in_about",
    "featured_section_type",
    "post_to_profile_match_score",
}

INSTRUCTION_BLOCK = """You are analyzing a LinkedIn profile for structural and rhetorical features. Respond with JSON only — no preamble, no markdown fences, no commentary.

Extract these features and return as a flat JSON object:

{
  "headline_style": "one of: credentialed, benefit-led, provocation, mission, role-only, other",
  "headline_specificity": "integer 0-10 — density of numbers, niches, named outcomes",
  "about_voice": "one of: analytical, vulnerable, aggressive, practical, inspirational, contrarian, teaching, storytelling",
  "about_promise_clarity": "integer 0-10 — how clearly the about text says 'I help X do Y'",
  "about_proof_density": "integer 0-10 — case studies, numbers, named clients, credentials",
  "niche_breadth": "one of: narrow, medium, broad",
  "audience_alignment": "one of: founders, operators, marketers, engineers, mixed, unclear",
  "cta_in_about": "boolean — explicit 'DM me' / 'book a call' / 'subscribe' / 'follow for X' anywhere in the about",
  "featured_section_type": "one of: lead_magnets, case_studies, press, podcasts, none, mixed",
  "post_to_profile_match_score": "integer 0-10 — does the profile claim match the topics they actually post about?"
}

Return only the JSON object."""


def build_user_message(*, profile_row: Any, top_posts: list[Any]) -> str:
    headline = profile_row["headline"] or "(no headline parsed)"
    about = profile_row["about_text"] or "(no about text parsed)"
    role = profile_row["current_role"] or "(unknown)"
    company = profile_row["current_company"] or "(unknown)"
    featured = profile_row["featured_count"] or 0
    has_photo = "yes" if profile_row["has_profile_photo"] else "no"
    has_banner = "yes" if profile_row["has_banner"] else "no"
    follower_count = profile_row["follower_count"] or 0

    posts_block = (
        "\n\n".join(f"- {p['post_text'][:500]}" for p in top_posts)
        if top_posts
        else "(no posts available)"
    )

    return (
        "PROFILE:\n<<<\n"
        f"Headline: {headline}\n"
        f"Current role: {role}\n"
        f"Current company: {company}\n"
        f"Followers: {follower_count}\n"
        f"Featured count: {featured}\n"
        f"Has profile photo: {has_photo}\n"
        f"Has banner: {has_banner}\n"
        f"About:\n{about}\n"
        ">>>\n\n"
        "RECENT TOP POSTS BY THIS CREATOR (use to score post_to_profile_match_score):\n"
        f"{posts_block}"
    )


def parse_and_validate(raw: str) -> dict | None:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].lstrip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    missing = EXPECTED_KEYS - set(data.keys())
    if missing:
        log.warning("Profile feature response missing keys: %s", missing)
        return None
    return data


def _call_claude(
    client: Anthropic,
    *,
    profile_row: Any,
    top_posts: list[Any],
    temperature: float = 0.2,
) -> dict | None:
    resp = with_retry(
        lambda: client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            temperature=temperature,
            system=[
                {
                    "type": "text",
                    "text": INSTRUCTION_BLOCK,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": build_user_message(
                        profile_row=profile_row, top_posts=top_posts
                    ),
                }
            ],
        ),
        description="profile feature extraction",
    )
    text = "".join(
        block.text for block in resp.content if getattr(block, "type", "") == "text"
    )
    return parse_and_validate(text)


def extract_profile_features(cfg: AppConfig) -> dict:
    """Extract one feature row per active creator who has at least one snapshot
    AND at least one post (the post sample feeds the alignment score).

    Idempotent — skips creators whose profile_features row already exists.
    Use --force-refresh by deleting the profile_features row(s) you want
    re-extracted; this function does not re-extract on its own.

    Returns a small summary dict for logging.
    """
    if not cfg.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set; cannot run profile feature extraction."
        )

    client = Anthropic(api_key=cfg.anthropic_api_key)
    extracted = 0
    skipped_no_snapshot = 0
    skipped_already_done = 0
    failed_parse = 0
    interval = 60.0 / BATCH_PER_MINUTE

    with storage.connect(cfg.db_path) as conn:
        creators = conn.execute(
            "SELECT id, display_name FROM creators WHERE active = 1"
        ).fetchall()
        log.info("Profile feature extraction across %d active creators.", len(creators))

        for c in creators:
            cid = c["id"]
            existing = conn.execute(
                "SELECT 1 FROM profile_features WHERE creator_id = ?", (cid,)
            ).fetchone()
            if existing:
                skipped_already_done += 1
                continue

            snapshot = storage.latest_profile_snapshot(conn, cid)
            if snapshot is None:
                skipped_no_snapshot += 1
                continue

            top_posts = conn.execute(
                """
                SELECT post_text
                FROM posts
                WHERE creator_id = ?
                  AND engagement_score IS NOT NULL
                ORDER BY engagement_score DESC
                LIMIT ?
                """,
                (cid, TOP_POSTS_PER_CREATOR),
            ).fetchall()

            t0 = time.time()
            features = _call_claude(
                client, profile_row=snapshot, top_posts=top_posts, temperature=0.2
            )
            if features is None:
                log.warning(
                    "First-pass parse failed for creator %s; retrying at temp=0",
                    c["display_name"] or cid,
                )
                features = _call_claude(
                    client, profile_row=snapshot, top_posts=top_posts, temperature=0.0
                )
            if features is None:
                failed_parse += 1
                log.error(
                    "Skipping creator %s after two parse failures",
                    c["display_name"] or cid,
                )
                continue

            conn.execute(
                """
                INSERT OR REPLACE INTO profile_features
                  (creator_id, features_json, source_snapshot_id)
                VALUES (?, ?, ?)
                """,
                (cid, json.dumps(features), snapshot["id"]),
            )
            extracted += 1

            elapsed = time.time() - t0
            if elapsed < interval:
                time.sleep(interval - elapsed)

    return {
        "extracted": extracted,
        "skipped_already_done": skipped_already_done,
        "skipped_no_snapshot": skipped_no_snapshot,
        "failed_parse": failed_parse,
    }
