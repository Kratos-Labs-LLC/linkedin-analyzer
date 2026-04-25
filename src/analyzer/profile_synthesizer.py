"""Synthesize linkedin-profile-optimizer/SKILL.md.

Same two-pass shape as src/analyzer/synthesizer.py: draft → critique-and-
revise, with the static inputs (profile-axis stats + sample headlines/abouts +
voice) sent as a cache-controlled system block. ~$1.30 per run.

The output skill triggers on profile-authoring intents (rewrite my headline,
audit my about, design my featured section) — distinct from the post-writer
skill that triggers on "write me a LinkedIn post."
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from anthropic import Anthropic

from src import storage
from src.config import AppConfig
from src.analyzer.synthesizer import (
    DUGG_VOICE_PROFILE,
    MAX_TOKENS_DRAFT,
    MAX_TOKENS_REVISE,
    MIN_REVISE_LENGTH,
    MODEL,
    TEMPERATURE,
    _extract_text,
    _strip_critique_block,
)

log = logging.getLogger(__name__)

SKILL_NAME = "linkedin-profile-optimizer"
SAMPLE_TOP_CREATOR_PROFILES = 6
SAMPLE_BOTTOM_CREATOR_PROFILES = 4

# Minimum creators with both growth-rate AND profile features before it's
# worth burning ~$1.30 of Opus on a profile-optimizer skill. Below this, the
# synth would be hallucinating from too-small a sample.
MIN_CREATORS_FOR_SYNTH = 5


# --- Prompt blocks -----------------------------------------------------------


def _static_inputs(
    *,
    profile_axis_json: str,
    top_profiles_md: str,
    bottom_profiles_md: str,
) -> str:
    return f"""You are authoring a Claude skill called `linkedin-profile-optimizer`, based on empirical analysis of LinkedIn profiles in the AI / agency / B2B space, scored by follower-growth-per-week (the cleanest available proxy for "this profile pulls people in").

<profile_axis_findings>
{profile_axis_json}
</profile_axis_findings>

<high_growth_profiles>
{top_profiles_md}
</high_growth_profiles>

<low_growth_profiles>
{bottom_profiles_md}
</low_growth_profiles>

<dugg_voice_profile>
{DUGG_VOICE_PROFILE}
</dugg_voice_profile>"""


TASK_DRAFT = """TASK:

Produce a complete SKILL.md file with:

1. YAML frontmatter:
   - `name`: linkedin-profile-optimizer
   - `description`: rich triggering description — when to use this skill (rewriting headline, about section, featured section, full profile audit) vs the related linkedin-high-engagement-writer (which is for individual posts).

2. A body with these sections in order:
   - Intro paragraph explaining the empirical basis. Cite n_creators_analyzed and the score field.
   - "Headlines that pull" — top 3-5 headline_style values with data, an example headline from each high-growth creator (cite by display_name + growth rate), and a 1-line template for each style.
   - "About sections that convert" — what `about_voice`, `about_promise_clarity`, `about_proof_density`, `cta_in_about` correlate with growth. Include 2-3 example openings from real high-growth abouts.
   - "Featured section strategy" — what `featured_section_type` ranks highest. If `none` is in the bottom quartile, call that out.
   - "Profile-post alignment" — `post_to_profile_match_score`. Explain why mismatch tanks pull. Concrete example of aligned vs misaligned.
   - "What kills profile pull" — 3-5 anti-patterns from the low-growth profiles with examples.
   - "The Dugg voice filter" — how to translate the patterns into his voice.
   - "Worked examples" — 3 full profile rewrites: one founder, one operator, one mixed audience. Show before/after for headline + first-paragraph of about.
   - "Pre-publish checklist" — 5-8 yes/no questions before publishing the new profile.
   - "When NOT to use this skill" — defer to linkedin-high-engagement-writer when the request is about authoring a single post, not editing the profile itself.

3. Clear delineation vs the post-writer skill: this one optimizes the profile surface that posts route to.

FORMAT REQUIREMENTS:
- Markdown only.
- No code fences around the whole document.
- Frontmatter at the top.
- Keep under 800 lines total.

Author the skill now. Output only the SKILL.md content."""


TASK_CRITIQUE = """TASK:

You previously drafted `linkedin-profile-optimizer/SKILL.md`. Critique it against the checklist below, then output the revised final version.

CHECKLIST:
1. Frontmatter present? `name` exact (`linkedin-profile-optimizer`)? `description` rich and triggering, with clear delineation vs linkedin-high-engagement-writer?
2. Every empirical claim cites the data — bucket name + rank or n, or @creator + growth rate.
3. The 9 body sections are present and in order.
4. Real example headlines / about openings cited from the high-growth set, not generic placeholders.
5. `post_to_profile_match_score` is actually used as a skill instruction, not just mentioned.
6. Featured section guidance reflects the ranking in `featured_section_type` (e.g. if `lead_magnets` ranks #1, the skill should say so).
7. Worked examples have real before/after, not just bullet outlines.
8. Total length under 800 lines.
9. Dugg voice applied throughout (lowercase, short sentences, "->" arrows, first-person where relevant, no defensive language).
10. "When NOT to use" clearly redirects single-post intents to linkedin-high-engagement-writer.

OUTPUT:
- A brief <critique>...</critique> block listing every checklist item violated, with one-line fixes.
- Then the FULL revised SKILL.md (no code fences around the doc, frontmatter at top).
"""


# --- Calls -------------------------------------------------------------------


def _draft(client: Anthropic, static_inputs_text: str) -> str:
    resp = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS_DRAFT,
        temperature=TEMPERATURE,
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
    client: Anthropic, static_inputs_text: str, draft: str
) -> str:
    resp = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS_REVISE,
        temperature=TEMPERATURE,
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
    return _strip_critique_block(full)


def _looks_like_profile_skill(text: str) -> bool:
    if len(text) < MIN_REVISE_LENGTH:
        return False
    head = text.lstrip()
    if not head.startswith("---"):
        return False
    if SKILL_NAME not in text:
        return False
    return True


# --- Sample profile rendering ------------------------------------------------


def _render_top_bottom_profiles(cfg: AppConfig) -> tuple[str, str]:
    """Build markdown blobs of high-growth and low-growth creator profiles for
    the synthesizer to cite. Each block: display_name, growth rate, headline,
    about excerpt."""
    from src.analyzer.growth import creator_growth_rates

    rates = creator_growth_rates(cfg)
    with storage.connect(cfg.db_path) as conn:
        creators = conn.execute(
            "SELECT id, display_name FROM creators WHERE active = 1"
        ).fetchall()
        snapshots: dict[int, dict] = {}
        for c in creators:
            snap = storage.latest_profile_snapshot(conn, c["id"])
            if snap is not None:
                snapshots[c["id"]] = dict(snap)

    rated = [
        (c["id"], c["display_name"], rates.get(c["id"]))
        for c in creators
        if rates.get(c["id"]) is not None and c["id"] in snapshots
    ]
    rated.sort(key=lambda t: t[2] or 0, reverse=True)

    top = rated[:SAMPLE_TOP_CREATOR_PROFILES]
    bottom = rated[-SAMPLE_BOTTOM_CREATOR_PROFILES:] if len(rated) > SAMPLE_TOP_CREATOR_PROFILES else []

    def render_one(cid: int, name: str | None, rate: float | None) -> str:
        snap = snapshots[cid]
        about = (snap.get("about_text") or "").strip()
        if len(about) > 800:
            about = about[:800] + "…"
        headline = (snap.get("headline") or "(no headline parsed)").strip()
        return (
            f"### {name or 'Creator ' + str(cid)} — growth {rate:.1f}/week\n\n"
            f"**Headline:** {headline}\n\n"
            f"**About:**\n\n{about or '(no about text parsed)'}\n\n"
            f"**Featured items:** {snap.get('featured_count') or 0}\n\n"
            "---\n"
        )

    top_md = "\n".join(render_one(cid, n, r) for cid, n, r in top) or "(no high-growth profiles available)"
    bot_md = "\n".join(render_one(cid, n, r) for cid, n, r in bottom) or "(no low-growth profiles available)"
    return top_md, bot_md


# --- Public API --------------------------------------------------------------


def synthesize(cfg: AppConfig) -> Path | None:
    """Author linkedin-profile-optimizer/SKILL.md.

    Returns None — and burns no Anthropic spend — when the dataset is too
    small (`profile_axis.n_creators_analyzed < MIN_CREATORS_FOR_SYNTH`).
    The collector and feature extractor will accumulate more data on the
    next run; the user can re-invoke `--profile-only` once the threshold
    is met.
    """
    if not cfg.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set; cannot run profile synthesis.")

    profile_axis_path = cfg.output_dir / "stats.json"
    if not profile_axis_path.exists():
        raise FileNotFoundError(
            f"stats.json not found at {profile_axis_path}. Run compute_profile_stats first."
        )
    base_stats = json.loads(profile_axis_path.read_text())
    profile_axis = base_stats.get("profile_axis")
    if not profile_axis:
        raise RuntimeError(
            "stats.json has no profile_axis field. Run compute_profile_stats then merge_into_stats_json."
        )

    n_creators = int(profile_axis.get("n_creators_analyzed") or 0)
    if n_creators < MIN_CREATORS_FOR_SYNTH:
        log.warning(
            "Profile synth skipped: only %d creators have features+growth-rate "
            "(need >= %d). Re-run after more snapshots accumulate.",
            n_creators,
            MIN_CREATORS_FOR_SYNTH,
        )
        return None

    top_md, bottom_md = _render_top_bottom_profiles(cfg)
    static = _static_inputs(
        profile_axis_json=json.dumps(profile_axis, indent=2),
        top_profiles_md=top_md,
        bottom_profiles_md=bottom_md,
    )

    client = Anthropic(api_key=cfg.anthropic_api_key)

    log.info("Profile synth pass 1/2: drafting (model=%s)...", MODEL)
    draft = _draft(client, static)
    log.info("Draft length: %d chars.", len(draft))

    log.info("Profile synth pass 2/2: critique-and-revise (cached static inputs)...")
    final = _critique_and_revise(client, static, draft)
    log.info("Final length: %d chars.", len(final))

    if not _looks_like_profile_skill(final):
        log.warning(
            "Revise output failed sanity check (len=%d, frontmatter=%s, name=%s). "
            "Falling back to draft.",
            len(final),
            final.lstrip().startswith("---"),
            SKILL_NAME in final,
        )
        final = draft

    out_dir = cfg.output_dir / SKILL_NAME
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "SKILL.md"
    out_path.write_text(final)

    (cfg.output_dir / "profile_synthesis_draft.md").write_text(draft)

    log.info(
        "Wrote %s (%d chars). Draft preserved at output/profile_synthesis_draft.md.",
        out_path,
        len(final),
    )
    return out_path
