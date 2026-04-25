"""Synthesize a per-creator competitive-intelligence markdown doc.

Single-pass Opus 4.7 call per creator. The cohort baseline + the section
template + instructions are sent as a cache-controlled system block, so
all but the first creator's call read those from prompt cache. The
per-creator user message is just the intelligence_pack JSON.

Single-pass (not draft+revise) because these docs are downstream of the
production skills — the goal is "dense, factual, structured" not
"polished marketing prose." Two passes would double the cost without
moving the needle on faithfulness, since faithfulness is enforced by
the explicit "do not invent counts" instruction + sanity-check fallback.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from anthropic import Anthropic

log = logging.getLogger(__name__)

MODEL = "claude-opus-4-7"
MAX_TOKENS = 8_000
TEMPERATURE = 0.5

# The exact section headers the prose must contain. The fallback fires
# if any are missing — the doc is the artifact, structure is the
# contract.
REQUIRED_SECTIONS = [
    "## TL;DR",
    "## Identity & positioning",
    "## What they post",
    "## What works for them",
    "## What doesn't work",
    "## Why followers grow",
    "## Profile-post coherence",
    "## Replicable plays",
    "## Appendix",
]
MIN_DOC_LENGTH = 1_500


# ---------------------------------------------------------------------
# Prompt blocks
# ---------------------------------------------------------------------


def _system_block(baseline: dict) -> str:
    return f"""You are writing a competitive-intelligence brief about a single LinkedIn creator, for a strategist who wants to understand what the creator does and why it works for them.

The brief is one of ~25 generated this run. Quality bar:

- Every numeric claim must come from the data pack. Do NOT invent counts, percentages, correlations, or follower numbers. If a number is not in the pack, do not state it.
- Compare the creator against the cohort baseline below. When you say "they over-index on X," reference the actual percentages from `cohort_delta`.
- Cite specific posts from the appendix sections by their distinguishing phrasing or topic, not by index.
- The brief is for someone who already knows the cohort. Skip generic framing. Get to the specific patterns.

<cohort_baseline>
{json.dumps(baseline, indent=2, default=str)}
</cohort_baseline>

<output_structure>
The brief MUST have these section headers, in this order, each as a `##` heading:

1. ## TL;DR — 3-5 bullets stating what makes this creator effective. Each bullet should be a one-line claim grounded in a specific data point from the pack.
2. ## Identity & positioning — what their headline + about + role say. Quote distinctive phrases. Note headline_style and audience_alignment from profile.features.
3. ## What they post — topic mix from `cohort_delta.topic_category`, hook patterns from `cohort_delta.hook_type`, voice from `cohort_delta.emotional_register`. Cite percentages.
4. ## What works for them — top-quartile features from `self_topquartile_vs_bottomquartile.categorical_features`. Show ranks and counts. Include a comparison column to the cohort baseline where useful.
5. ## What doesn't work — bottom-quartile patterns. What features dominate their bottom-quartile posts? Cite n's.
6. ## Why followers grow — analyze `growth_correlation.top_growth_posts` and `patterns_in_top_growth`. Tie to `creator.growth_rate_per_week`. If there are no posts with growth data, say so explicitly and skip numeric claims for the section.
7. ## Profile-post coherence — pull `profile.features.post_to_profile_match_score`. Does the profile claim match what they actually post about? Where's the mismatch?
8. ## Replicable plays — 3-5 specific patterns a strategist could study or borrow. Each play names the pattern (concretely), the data evidence, and one example post phrasing from the appendix.
9. ## Appendix — paste the top 5 posts and bottom 3 posts from the pack with raw text and feature mix. Use a `### Top post 1` / `### Bottom post 1` style.
</output_structure>

FORMAT:
- Markdown only.
- No code fences around the whole document. No frontmatter required.
- 600-1200 lines is fine. Density beats brevity, but don't pad.
- Quote post text inside `>` block-quotes when citing.

Write the brief now using the data pack in the user message."""


# ---------------------------------------------------------------------
# Synth + sanity check
# ---------------------------------------------------------------------


def _looks_like_intel_doc(text: str) -> bool:
    if len(text) < MIN_DOC_LENGTH:
        return False
    for header in REQUIRED_SECTIONS:
        if header not in text:
            return False
    return True


def _stub_doc(pack: dict, missing_reason: str) -> str:
    """Fallback when Opus output fails the sanity check. Embeds the pack
    JSON inside a stub so the dashboard still has something to render and
    the operator can see what data the synth had to work with."""
    name = pack.get("creator", {}).get("display_name", "unknown")
    return (
        f"# Intelligence brief: {name} (auto-stub)\n\n"
        f"_Synthesizer output failed sanity check: {missing_reason}._\n\n"
        f"_Pack contents are embedded below as JSON for inspection._\n\n"
        "## TL;DR\n\n_(synthesizer fallback)_\n\n"
        "## Identity & positioning\n\n_(synthesizer fallback)_\n\n"
        "## What they post\n\n_(synthesizer fallback)_\n\n"
        "## What works for them\n\n_(synthesizer fallback)_\n\n"
        "## What doesn't work\n\n_(synthesizer fallback)_\n\n"
        "## Why followers grow\n\n_(synthesizer fallback)_\n\n"
        "## Profile-post coherence\n\n_(synthesizer fallback)_\n\n"
        "## Replicable plays\n\n_(synthesizer fallback)_\n\n"
        "## Appendix\n\n```json\n"
        f"{json.dumps(pack, indent=2, default=str)}\n"
        "```\n"
    )


def _extract_text(resp: Any) -> str:
    return "".join(
        b.text for b in resp.content if getattr(b, "type", "") == "text"
    ).strip()


def _log_usage(resp: Any, *, name: str) -> None:
    """Mirror the token-usage log shape used elsewhere in the analyzer."""
    usage = getattr(resp, "usage", None)
    if usage is None:
        return
    log.info(
        "intelligence_synth[%s] usage: input=%s output=%s cache_create=%s cache_read=%s",
        name,
        getattr(usage, "input_tokens", None),
        getattr(usage, "output_tokens", None),
        getattr(usage, "cache_creation_input_tokens", None),
        getattr(usage, "cache_read_input_tokens", None),
    )


def synthesize_intelligence(
    pack: dict,
    baseline: dict,
    *,
    client: Anthropic | None = None,
    api_key: str | None = None,
) -> str:
    """Return a markdown intelligence brief. The caller owns where to write
    the file — this function does not touch disk."""
    if client is None:
        if not api_key:
            raise RuntimeError(
                "intelligence_synth needs either a client or an api_key."
            )
        client = Anthropic(api_key=api_key)

    name = pack.get("creator", {}).get("display_name") or str(
        pack.get("creator", {}).get("id")
    )
    log.info(
        "intelligence_synth: drafting brief for creator '%s' (model=%s)",
        name,
        MODEL,
    )

    resp = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
        system=[
            {
                "type": "text",
                "text": _system_block(baseline),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    "<intelligence_pack>\n"
                    f"{json.dumps(pack, indent=2, default=str)}\n"
                    "</intelligence_pack>\n\n"
                    "Write the brief now."
                ),
            }
        ],
    )
    _log_usage(resp, name=name)
    text = _extract_text(resp)

    if not _looks_like_intel_doc(text):
        missing = next(
            (s for s in REQUIRED_SECTIONS if s not in text),
            f"length={len(text)} < {MIN_DOC_LENGTH}",
        )
        log.warning(
            "intelligence_synth[%s] sanity-check failed (missing=%s); writing stub.",
            name,
            missing,
        )
        return _stub_doc(pack, missing_reason=missing)

    return text
