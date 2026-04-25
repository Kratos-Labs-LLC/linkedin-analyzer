"""Skill validation harness — head-to-head A/B between old and new skills.

Trust loop on the synthesizer's output. Without this, every other improvement
to the pipeline is unmeasurable.

Flow:
    1. Sample N held-out top-quartile posts (skip first 50 the synthesizer saw),
       diversified across topic_category.
    2. For each, derive a topic prompt from the post's hook + topic.
    3. Generate a draft with each skill (Sonnet, low temp).
    4. Blind-judge with Opus, with A/B order randomized per prompt, against the
       empirical patterns in stats.json.
    5. Aggregate: win-rate, ties, common reasons the loser missed.
    6. Write output/validation.md.
"""
from __future__ import annotations

import json
import logging
import random
from dataclasses import asdict, dataclass
from pathlib import Path

from anthropic import Anthropic

from src import storage
from src.analyzer._retry import with_retry
from src.config import AppConfig

log = logging.getLogger(__name__)

DRAFT_MODEL = "claude-sonnet-4-5"
JUDGE_MODEL = "claude-opus-4-7"

DEFAULT_N_PROMPTS = 15
MAX_N_PROMPTS = 50  # safety cap on --validate-n; ~$3 ceiling
# Skip the top-50 posts the synthesizer saw verbatim. Posts beyond 50 are still
# indirectly represented through stats.json (means, correlations) — but no
# verbatim memorization is possible from this slice.
HELD_OUT_OFFSET = 50
PER_TOPIC_CAP = 2     # diversify
DRAFT_MAX_TOKENS = 1_200
JUDGE_MAX_TOKENS = 1_200
DRAFT_TEMPERATURE = 0.3  # low enough that A/B is reproducible across runs


@dataclass
class PromptCase:
    post_id: int
    topic_category: str
    hook_excerpt: str
    actual_score: float
    prompt: str


@dataclass
class PairVerdict:
    prompt: str
    topic: str
    winner: str  # 'new' | 'old' | 'tie'
    reasoning: str
    new_draft: str
    old_draft: str
    judge_raw: str


@dataclass
class ValidationResult:
    n_prompts: int
    new_wins: int
    old_wins: int
    ties: int
    pairs: list[PairVerdict]

    @property
    def win_rate(self) -> float:
        decided = self.new_wins + self.old_wins
        return self.new_wins / decided if decided else 0.0


# --- Prompt sampling ---------------------------------------------------------


def _sample_prompts(cfg: AppConfig, n: int = DEFAULT_N_PROMPTS) -> list[PromptCase]:
    """Pick N held-out top-quartile posts, diversified by topic_category.

    Restrict to the top half of scored posts (matches what the synthesizer
    actually trained on), then skip the verbatim-leaked top 50.
    """
    with storage.connect(cfg.db_path) as conn:
        rows = conn.execute(
            """
            SELECT p.id, p.post_text, p.engagement_score, pf.features_json
            FROM posts p
            LEFT JOIN post_features pf ON pf.post_id = p.id
            WHERE p.engagement_score IS NOT NULL
            ORDER BY p.engagement_score DESC
            """,
        ).fetchall()

    if not rows:
        return []

    # Top half = "top quartile" in the synthesizer's framing.
    top_half_cut = max(1, len(rows) // 2)
    top_half = list(rows)[:top_half_cut]
    held_out = top_half[HELD_OUT_OFFSET:]
    if not held_out:
        # Tiny dataset (or all in the held-out window) — fall back to whatever
        # is available so we still produce a result.
        held_out = top_half or list(rows)

    cases: list[PromptCase] = []
    per_topic: dict[str, int] = {}
    for r in held_out:
        topic = "other"
        hook = ""
        if r["features_json"]:
            try:
                feats = json.loads(r["features_json"])
                topic = str(feats.get("topic_category") or "other")
                hook = str(feats.get("hook_text") or "")
            except json.JSONDecodeError:
                pass
        if per_topic.get(topic, 0) >= PER_TOPIC_CAP:
            continue

        text = r["post_text"]
        if not hook:
            hook = " ".join(text.split()[:25])

        prompt = _prompt_from_post(topic=topic, hook=hook, text=text)
        cases.append(PromptCase(
            post_id=r["id"],
            topic_category=topic,
            hook_excerpt=hook[:160],
            actual_score=float(r["engagement_score"]),
            prompt=prompt,
        ))
        per_topic[topic] = per_topic.get(topic, 0) + 1
        if len(cases) >= n:
            break

    if len(cases) < n:
        log.warning("Validator: only %d cases found (asked for %d).", len(cases), n)
    return cases


def _prompt_from_post(*, topic: str, hook: str, text: str) -> str:
    """Make a 'write a post about X' prompt from a real top post."""
    summary = " ".join(text.split()[:60])
    return (
        f"Write an original LinkedIn post for a marketing agency founder.\n"
        f"Topic category: {topic}.\n"
        f"The post should cover roughly this idea (do not copy verbatim):\n"
        f"\"{summary}\""
    )


# --- Draft + judge -----------------------------------------------------------


def _draft(client: Anthropic, skill_md: str, prompt: str) -> str:
    """Use the skill content as the system prompt; ask for one post."""
    resp = with_retry(
        lambda: client.messages.create(
            model=DRAFT_MODEL,
            max_tokens=DRAFT_MAX_TOKENS,
            temperature=DRAFT_TEMPERATURE,
            system=[{
                "type": "text",
                "text": skill_md,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{
                "role": "user",
                "content": prompt + "\n\nReturn only the post text. No preamble, no markdown fences.",
            }],
        ),
        description="validator draft",
    )
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()


JUDGE_SYSTEM = (
    "You are evaluating two LinkedIn posts (A and B) against an empirical "
    "engagement profile. Decide which post better matches the high-engagement "
    "patterns. Respond with JSON only: "
    '{"winner": "A" | "B" | "tie", "reasoning": "1-2 sentences citing specific '
    'features from the profile", "missed_features": ["..."]}.'
)


def _judge(
    client: Anthropic,
    *,
    prompt: str,
    draft_a: str,
    draft_b: str,
    stats_summary: str,
) -> dict:
    user_msg = (
        f"<engagement_profile>\n{stats_summary}\n</engagement_profile>\n\n"
        f"<prompt>{prompt}</prompt>\n\n"
        f"<post_a>\n{draft_a}\n</post_a>\n\n"
        f"<post_b>\n{draft_b}\n</post_b>\n\n"
        "Return JSON only."
    )
    resp = with_retry(
        lambda: client.messages.create(
            model=JUDGE_MODEL,
            max_tokens=JUDGE_MAX_TOKENS,
            temperature=0.0,
            system=[{
                "type": "text",
                "text": JUDGE_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": user_msg}],
        ),
        description="validator judge",
    )
    raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    return _parse_judge_json(raw)


def _parse_judge_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").lstrip()
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {"winner": "tie", "reasoning": f"unparseable judge output: {raw[:160]}", "missed_features": []}
    if not isinstance(data, dict) or "winner" not in data:
        return {"winner": "tie", "reasoning": "missing winner", "missed_features": []}
    if data["winner"] not in ("A", "B", "tie"):
        data["winner"] = "tie"
    data.setdefault("reasoning", "")
    data.setdefault("missed_features", [])
    if not isinstance(data["missed_features"], list):
        data["missed_features"] = []
    return data


# --- Stats summary (passed to judge) ----------------------------------------


def _stats_summary(stats_path: Path) -> str:
    """Compact, JSON-stringified subset of stats.json the judge can reason over."""
    if not stats_path.exists():
        return "(no stats.json available — judge on general engagement heuristics)"
    try:
        data = json.loads(stats_path.read_text())
    except json.JSONDecodeError:
        return "(stats.json could not be parsed)"

    summary: dict[str, object] = {}
    cat = data.get("categorical_features") or {}
    # Top-ranked value per categorical feature (rank 1)
    top_categories: dict[str, str] = {}
    for feature, buckets in cat.items():
        if not isinstance(buckets, dict):
            continue
        ranked = sorted(
            ((k, v) for k, v in buckets.items() if isinstance(v, dict)),
            key=lambda kv: kv[1].get("rank", 999),
        )
        if ranked:
            top_categories[feature] = ranked[0][0]
    summary["top_per_categorical"] = top_categories

    num = data.get("numeric_features") or {}
    summary["numeric_signals"] = {
        feature: {
            "correlation_with_engagement": v.get("correlation"),
            "top_quartile_mean": v.get("top_quartile_mean"),
            "bottom_quartile_mean": v.get("bottom_quartile_mean"),
        }
        for feature, v in num.items()
        if isinstance(v, dict)
    }
    summary["proof_elements_frequency"] = data.get("proof_elements_frequency", {})
    summary["n_posts_analyzed"] = data.get("n_posts_analyzed")

    # Include the profile-pull axis when present so the judge can evaluate
    # which post is more likely to drive profile clicks, not just engagement.
    profile_axis = data.get("profile_axis") or {}
    if profile_axis:
        pa_cat = profile_axis.get("categorical_features") or {}
        pa_top: dict[str, str] = {}
        for feature, buckets in pa_cat.items():
            if not isinstance(buckets, dict):
                continue
            ranked = sorted(
                ((k, v) for k, v in buckets.items() if isinstance(v, dict)),
                key=lambda kv: kv[1].get("rank", 999),
            )
            if ranked:
                pa_top[feature] = ranked[0][0]
        summary["profile_axis"] = {
            "n_creators_analyzed": profile_axis.get("n_creators_analyzed"),
            "score_field": profile_axis.get("score_field"),
            "top_per_categorical": pa_top,
        }
    return json.dumps(summary, indent=2)


# --- Orchestration -----------------------------------------------------------


def run_validation(
    cfg: AppConfig,
    *,
    old_skill_path: Path,
    new_skill_path: Path,
    n: int = DEFAULT_N_PROMPTS,
    seed: int | None = None,
) -> ValidationResult:
    if not cfg.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set; cannot run validation.")
    if not old_skill_path.exists():
        raise FileNotFoundError(f"old skill not found: {old_skill_path}")
    if not new_skill_path.exists():
        raise FileNotFoundError(f"new skill not found: {new_skill_path}")

    if n > MAX_N_PROMPTS:
        log.warning("Capping --validate-n from %d to %d to bound API spend.", n, MAX_N_PROMPTS)
        n = MAX_N_PROMPTS

    rng = random.Random(seed)
    old_md = old_skill_path.read_text()
    new_md = new_skill_path.read_text()
    stats_summary = _stats_summary(cfg.output_dir / "stats.json")

    cases = _sample_prompts(cfg, n=n)
    if not cases:
        raise RuntimeError(
            "No held-out posts available. Run feature extraction first or lower HELD_OUT_OFFSET."
        )

    client = Anthropic(api_key=cfg.anthropic_api_key)
    pairs: list[PairVerdict] = []
    new_wins = old_wins = ties = 0

    for i, case in enumerate(cases, start=1):
        log.info("Validating %d/%d (%s)", i, len(cases), case.topic_category)
        new_draft = _draft(client, new_md, case.prompt)
        old_draft = _draft(client, old_md, case.prompt)

        new_is_a = rng.random() < 0.5
        if new_is_a:
            verdict = _judge(
                client,
                prompt=case.prompt,
                draft_a=new_draft,
                draft_b=old_draft,
                stats_summary=stats_summary,
            )
        else:
            verdict = _judge(
                client,
                prompt=case.prompt,
                draft_a=old_draft,
                draft_b=new_draft,
                stats_summary=stats_summary,
            )

        winner_letter = verdict["winner"]
        if winner_letter == "tie":
            winner = "tie"
            ties += 1
        elif (winner_letter == "A" and new_is_a) or (winner_letter == "B" and not new_is_a):
            winner = "new"
            new_wins += 1
        else:
            winner = "old"
            old_wins += 1

        pairs.append(PairVerdict(
            prompt=case.prompt,
            topic=case.topic_category,
            winner=winner,
            reasoning=str(verdict.get("reasoning", "")),
            new_draft=new_draft,
            old_draft=old_draft,
            judge_raw=json.dumps(verdict),
        ))

    return ValidationResult(
        n_prompts=len(cases),
        new_wins=new_wins,
        old_wins=old_wins,
        ties=ties,
        pairs=pairs,
    )


def write_report(result: ValidationResult, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "validation.md"

    lines = [
        "# Skill validation report",
        "",
        f"- prompts evaluated: **{result.n_prompts}**",
        f"- new skill wins: **{result.new_wins}**",
        f"- existing skill wins: **{result.old_wins}**",
        f"- ties: **{result.ties}**",
        f"- decided win-rate (new): **{result.win_rate:.0%}**",
        "",
        "## Verdict",
        "",
    ]
    if result.new_wins > result.old_wins and result.win_rate >= 0.6:
        lines.append("✅ The new skill wins decisively. Install it.")
    elif result.new_wins > result.old_wins:
        lines.append("⚠️ The new skill wins on net but only by a thin margin. Spot-check the misses below before installing.")
    elif result.new_wins == result.old_wins:
        lines.append("⚠️ Tied. Iterate on the synthesizer prompt or extend collection before shipping.")
    else:
        lines.append("❌ The existing skill still wins. Do not install the new one until the synthesizer is improved.")
    lines.append("")
    lines.append("## Per-prompt verdicts")
    for i, p in enumerate(result.pairs, start=1):
        emoji = {"new": "🟢 new", "old": "🔴 old", "tie": "⚪ tie"}[p.winner]
        lines.append(f"\n### {i}. [{p.topic}] — {emoji}")
        lines.append(f"\n**Prompt**\n\n```\n{p.prompt}\n```")
        lines.append(f"\n**Reasoning:** {p.reasoning}")
        lines.append("\n<details><summary>Drafts</summary>\n")
        lines.append("**New skill draft:**")
        lines.append(f"\n```\n{p.new_draft}\n```\n")
        lines.append("**Existing skill draft:**")
        lines.append(f"\n```\n{p.old_draft}\n```\n")
        lines.append("</details>")

    path.write_text("\n".join(lines))
    return path


def to_dict(result: ValidationResult) -> dict:
    """Serializable form for tests / debugging."""
    return {
        "n_prompts": result.n_prompts,
        "new_wins": result.new_wins,
        "old_wins": result.old_wins,
        "ties": result.ties,
        "win_rate": result.win_rate,
        "pairs": [asdict(p) for p in result.pairs],
    }
