"""Synthesize linkedin-high-engagement-writer/SKILL.md from stats + top/bottom posts."""
from __future__ import annotations

import logging
from pathlib import Path

from anthropic import Anthropic

from src.config import AppConfig

log = logging.getLogger(__name__)

MODEL = "claude-opus-4-7"
MAX_TOKENS = 8_000

DUGG_VOICE_PROFILE = """- All lowercase
- Short, punchy sentences
- Direct, anti-hype tone
- First-person only
- No defensive language
- Uses "->" arrows
- Minimal punctuation
- Target audience: UK/US marketing agency founders and MDs"""


def _build_prompt(
    stats_json: str,
    top_posts_md: str,
    bottom_posts_md: str,
    leadmagnet_skill: str,
) -> str:
    return f"""You are authoring a new Claude skill called `linkedin-high-engagement-writer`, based on empirical analysis of 300 LinkedIn posts from 25-30 creators in the AI/agency/B2B space.

INPUTS:

<statistical_findings>
{stats_json}
</statistical_findings>

<top_performing_posts>
{top_posts_md}
</top_performing_posts>

<bottom_performing_posts>
{bottom_posts_md}
</bottom_performing_posts>

<existing_related_skill>
{leadmagnet_skill}
</existing_related_skill>

<dugg_voice_profile>
{DUGG_VOICE_PROFILE}
</dugg_voice_profile>

TASK:

Produce a complete SKILL.md file with:

1. YAML frontmatter:
   - `name`: linkedin-high-engagement-writer
   - `description`: rich triggering description covering when to use this skill vs the existing leadmagnet-post-writer

2. A body with these sections in order:
   - Intro paragraph explaining the skill's empirical basis
   - "Hooks that work" — top 3-5 hook types with data, examples from actual top posts (cited as "@creator X achieved Y engagement with..."), and templates Dugg can adapt
   - "Structural patterns" — paragraph style, length sweet spots, line break density, all grounded in the data
   - "CTA patterns" — which CTAs correlate with top-quartile engagement
   - "What doesn't work" — 3-5 concrete anti-patterns from the bottom quartile, with examples
   - "The Dugg voice filter" — how to translate the patterns into his specific voice (reference the voice profile)
   - "Worked examples" — 3 full post templates with placeholders, each based on a pattern from the data
   - "Pre-post checklist" — 5-8 yes/no questions the writer should ask before publishing
   - "When NOT to use this skill" — defer to leadmagnet-post-writer when the post is promoting a downloadable resource with a comment-keyword CTA

3. Clear delineation vs existing skill: this one is for general thought-leadership / brand-building posts, not lead magnets

FORMAT REQUIREMENTS:
- Markdown only
- No code fences around the whole document
- Frontmatter at top
- Keep under 800 lines total

Author the skill now."""


def synthesize(
    cfg: AppConfig,
    leadmagnet_skill_path: Path,
) -> Path:
    if not cfg.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set; cannot run synthesis.")
    if not leadmagnet_skill_path.exists():
        raise FileNotFoundError(
            f"leadmagnet-post-writer SKILL.md not found at {leadmagnet_skill_path}. "
            "Supply via --leadmagnet-skill-path."
        )

    stats_json = (cfg.output_dir / "stats.json").read_text()
    top_md = (cfg.output_dir / "top_posts.md").read_text()
    bottom_md = (cfg.output_dir / "bottom_posts.md").read_text()
    leadmagnet = leadmagnet_skill_path.read_text()

    prompt = _build_prompt(stats_json, top_md, bottom_md, leadmagnet)

    client = Anthropic(api_key=cfg.anthropic_api_key)
    log.info("Calling %s for skill synthesis (this may take a minute)...", MODEL)
    resp = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    skill_md = "".join(
        block.text for block in resp.content if getattr(block, "type", "") == "text"
    )

    out_dir = cfg.output_dir / "linkedin-high-engagement-writer"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "SKILL.md"
    out_path.write_text(skill_md)
    log.info("Wrote %s (%d chars)", out_path, len(skill_md))
    return out_path
