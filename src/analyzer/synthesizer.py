"""Synthesize linkedin-high-engagement-writer/SKILL.md.

Two-pass authoring: draft -> critique-and-revise. The static inputs (stats,
top/bottom posts, leadmagnet skill, voice) are sent as a cache-controlled
system block, so the second call reads them from prompt cache for ~10% of the
normal price. Roughly 2× total cost vs single-shot, ~10× the spec adherence.
"""
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


# --- Prompt blocks -----------------------------------------------------------


def _static_inputs(
    *,
    stats_json: str,
    top_posts_md: str,
    bottom_posts_md: str,
    leadmagnet_skill: str,
) -> str:
    """The bulk of the prompt — large, identical across draft + revise calls."""
    return f"""You are authoring a Claude skill called `linkedin-high-engagement-writer`, based on empirical analysis of LinkedIn posts from 25-30 creators in the AI / agency / B2B space.

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
</dugg_voice_profile>"""


TASK_DRAFT = """TASK:

Produce a complete SKILL.md file with:

1. YAML frontmatter:
   - `name`: linkedin-high-engagement-writer
   - `description`: rich triggering description covering when to use this skill vs the existing leadmagnet-post-writer.

2. A body with these sections in order:
   - Intro paragraph explaining the skill's empirical basis (cite n_posts_analyzed).
   - "Hooks that work" — top 3-5 hook types with data, examples from actual top posts (cited as "@creator X achieved Y engagement with..."), and templates Dugg can adapt.
     * IMPORTANT: the `by_topic` field in statistical_findings shows that a hook ranking #1 pooled may not rank #1 within a specific topic. Where the data supports it, surface topic-specific overrides (e.g. "for `ai` posts, contrarian hooks beat bold_claim").
   - "Structural patterns" — paragraph style, length sweet spots, line break density, all grounded in numeric_features.top_quartile_mean vs bottom_quartile_mean.
   - "CTA patterns" — which CTAs correlate with top-quartile engagement. Cite the per-topic numbers when they diverge from pooled.
   - "What doesn't work" — 3-5 concrete anti-patterns from the bottom quartile, with examples.
   - "The Dugg voice filter" — how to translate the patterns into his specific voice. Reference the voice profile.
   - "Worked examples" — 3 full post templates with placeholders, each based on a pattern from the data. Pick examples spanning at least 2 different topic_category values.
   - "Pre-post checklist" — 5-8 yes/no questions the writer should ask before publishing.
   - "When NOT to use this skill" — defer to leadmagnet-post-writer when the post is promoting a downloadable resource with a comment-keyword CTA.

3. Clear delineation vs existing skill: this one is for general thought-leadership / brand-building posts, not lead magnets.

FORMAT REQUIREMENTS:
- Markdown only.
- No code fences around the whole document.
- Frontmatter at the top.
- Keep under 800 lines total.

Author the skill now. Output only the SKILL.md content."""


TASK_CRITIQUE = """TASK:

You previously authored a draft of `linkedin-high-engagement-writer/SKILL.md`. Critique it against the checklist below, then output the revised final version.

CHECKLIST:
1. Frontmatter present? `name` exact (`linkedin-high-engagement-writer`)? `description` rich and triggering, mentioning the right intent (thought-leadership / brand-building, not lead magnets)?
2. Every empirical claim cites the data — bucket name + rank or n, or the @creator + engagement score for example posts. No vague "research shows" hand-waving.
3. The `by_topic` data is actually used. If you ranked hooks pooled and there are topic-specific differences in `by_topic`, the skill must call them out explicitly. If you didn't use `by_topic`, you missed the most useful signal.
4. Dugg voice applied throughout: lowercase, short sentences, "->" arrows, first-person only, no defensive language.
5. All 9 body sections present and in order (Intro, Hooks, Structural, CTAs, What doesn't work, Voice filter, Worked examples, Checklist, When NOT to use).
6. Worked examples have real placeholders ({like_this}), not just generic post outlines. They span at least 2 topic_category values.
7. Pre-post checklist has 5-8 yes/no questions.
8. Total length under 800 lines.
9. "When NOT to use this skill" clearly delineates from leadmagnet-post-writer.

OUTPUT:
- First, a brief <critique> block listing every checklist item you violated or under-delivered on, with one-line fixes.
- Then the FULL revised SKILL.md, addressing every critique point. Output the complete file, not a diff.
- Format: <critique>...</critique> followed by the markdown SKILL.md (no code fences around the doc, frontmatter at top).
"""


# --- Calls -------------------------------------------------------------------


def _draft(client: Anthropic, static_inputs_text: str) -> str:
    resp = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=[
            {
                "type": "text",
                "text": static_inputs_text,
                "cache_control": {"type": "ephemeral"},
            },
            {"type": "text", "text": TASK_DRAFT},
        ],
        messages=[{"role": "user", "content": "Author the skill now."}],
    )
    return _extract_text(resp)


def _critique_and_revise(
    client: Anthropic,
    static_inputs_text: str,
    draft: str,
) -> str:
    resp = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=[
            {
                "type": "text",
                "text": static_inputs_text,
                "cache_control": {"type": "ephemeral"},
            },
            {"type": "text", "text": TASK_CRITIQUE},
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    f"<draft>\n{draft}\n</draft>\n\n"
                    "Critique then output the revised SKILL.md."
                ),
            }
        ],
    )
    full = _extract_text(resp)
    # Strip the <critique>...</critique> block to leave just the SKILL.md.
    return _strip_critique_block(full)


def _extract_text(resp) -> str:
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()


def _strip_critique_block(text: str) -> str:
    """Remove a leading <critique>...</critique> block if present."""
    lower = text.lower()
    if "<critique>" not in lower:
        return text
    end = lower.find("</critique>")
    if end == -1:
        return text
    return text[end + len("</critique>") :].lstrip()


# --- Public API --------------------------------------------------------------


def synthesize(cfg: AppConfig, leadmagnet_skill_path: Path) -> Path:
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

    static = _static_inputs(
        stats_json=stats_json,
        top_posts_md=top_md,
        bottom_posts_md=bottom_md,
        leadmagnet_skill=leadmagnet,
    )

    client = Anthropic(api_key=cfg.anthropic_api_key)

    log.info("Synthesis pass 1/2: drafting (model=%s)...", MODEL)
    draft = _draft(client, static)
    log.info("Draft length: %d chars.", len(draft))

    log.info("Synthesis pass 2/2: critique-and-revise (cached static inputs)...")
    final = _critique_and_revise(client, static, draft)
    log.info("Final length: %d chars.", len(final))

    out_dir = cfg.output_dir / "linkedin-high-engagement-writer"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "SKILL.md"
    out_path.write_text(final)

    # Persist the draft for inspection / regression analysis.
    (cfg.output_dir / "synthesis_draft.md").write_text(draft)

    log.info("Wrote %s (%d chars). Draft preserved at output/synthesis_draft.md.", out_path, len(final))
    return out_path
