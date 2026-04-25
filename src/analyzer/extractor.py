"""Feature extraction via Claude. Runs on the top/bottom quartile posts.

Uses prompt caching on the instruction block — the schema is identical across
all ~300 calls, so caching makes this step roughly 10x cheaper than uncached.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from anthropic import Anthropic

from src import storage
from src.analyzer._retry import with_retry
from src.config import AppConfig

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-5"
BATCH_PER_MINUTE = 10
MAX_TOKENS = 2_000

EXPECTED_KEYS = {
    "hook_type",
    "hook_text",
    "opening_word_count",
    "total_word_count",
    "line_break_count",
    "paragraph_style",
    "uses_list",
    "list_style",
    "uses_emojis",
    "emoji_count",
    "cta_type",
    "cta_text",
    "emotional_register",
    "specificity_score",
    "proof_elements",
    "narrative_arc",
    "opens_with_pronoun",
    "ends_with",
    "topic_category",
    "controversy_score",
}

INSTRUCTION_BLOCK = """You are analyzing a LinkedIn post for structural and rhetorical features. Respond with JSON only — no preamble, no markdown fences, no commentary.

Extract these features and return as a flat JSON object:

{
  "hook_type": "one of: question, bold_claim, number, story_start, contrarian, confession, observation, list_promise, quote, scene",
  "hook_text": "the first 1-2 sentences verbatim, max 200 chars",
  "opening_word_count": "integer, word count before the first line break",
  "total_word_count": "integer",
  "line_break_count": "integer, count of \\n\\n paragraph breaks",
  "paragraph_style": "one of: one_liners, short_chunks, medium_paragraphs, walls_of_text, mixed",
  "uses_list": "boolean",
  "list_style": "one of: bullets, numbered, arrows, dashes, emojis, none",
  "uses_emojis": "boolean",
  "emoji_count": "integer",
  "cta_type": "one of: comment_keyword, dm_request, link_click, question_prompt, follow_request, none",
  "cta_text": "the CTA sentence verbatim, or null",
  "emotional_register": "one of: analytical, vulnerable, aggressive, practical, inspirational, contrarian, teaching, storytelling",
  "specificity_score": "integer 0-10, based on density of numbers, names, dates, dollar amounts, percentages",
  "proof_elements": "array from: [screenshots_referenced, client_names, revenue_figures, credentials, case_study, data_chart, none]",
  "narrative_arc": "one of: none, before_after, problem_solution, hero_journey, listicle, framework_reveal, contrarian_takedown",
  "opens_with_pronoun": "one of: i, you, we, they, none",
  "ends_with": "one of: question, cta, statement, ellipsis, emoji_only, mic_drop",
  "topic_category": "one of: ai, agency_ops, sales, marketing, personal_brand, technical, mindset, hiring, saas_building, career, other",
  "controversy_score": "integer 0-10, how likely the post provokes disagreement or strong reactions"
}

Return only the JSON object."""


def _build_user_message(post_row) -> str:
    return (
        "POST:\n<<<\n"
        f"{post_row['post_text']}\n"
        ">>>\n\n"
        "METADATA:\n"
        f"Author follower count: {post_row['follower_count_at_collection']}\n"
        f"Reactions: {post_row['reactions']}\n"
        f"Comments: {post_row['comments']}\n"
        f"Reshares: {post_row['reshares']}\n"
        f"Normalized engagement score: {post_row['engagement_score']}"
    )


def _parse_and_validate(raw: str) -> dict | None:
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
        log.warning("Feature response missing keys: %s", missing)
        return None
    return data


def _call_claude(client: Anthropic, post_row, temperature: float = 0.2) -> dict | None:
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
            messages=[{"role": "user", "content": _build_user_message(post_row)}],
        ),
        description=f"feature extraction post={post_row['id']}",
    )
    text = "".join(
        block.text for block in resp.content if getattr(block, "type", "") == "text"
    )
    return _parse_and_validate(text)


def extract_features(cfg: AppConfig, fraction: float = 0.15) -> dict:
    if not cfg.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set; cannot run feature extraction.")

    client = Anthropic(api_key=cfg.anthropic_api_key)
    extracted = 0
    skipped = 0
    interval = 60.0 / BATCH_PER_MINUTE

    with storage.connect(cfg.db_path) as conn:
        top, bottom = storage.top_bottom_posts(conn, fraction=fraction)
        pool = list(top) + list(bottom)
        log.info("Extracting features for %d posts (top+bottom).", len(pool))

        for i, post in enumerate(pool):
            existing = conn.execute(
                "SELECT 1 FROM post_features WHERE post_id = ?", (post["id"],)
            ).fetchone()
            if existing:
                continue

            t0 = time.time()
            features = _call_claude(client, post, temperature=0.2)
            if features is None:
                log.warning("First-pass parse failed for post %d; retrying at temp=0", post["id"])
                features = _call_claude(client, post, temperature=0.0)
            if features is None:
                skipped += 1
                log.error("Skipping post %d after two parse failures", post["id"])
                continue

            conn.execute(
                "INSERT OR REPLACE INTO post_features (post_id, features_json) VALUES (?, ?)",
                (post["id"], json.dumps(features)),
            )
            extracted += 1
            elapsed = time.time() - t0
            if elapsed < interval:
                time.sleep(interval - elapsed)

    log.info("Feature extraction complete: %d extracted, %d skipped.", extracted, skipped)
    return {"extracted": extracted, "skipped": skipped, "total_pool": len(pool)}
