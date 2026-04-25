import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src import storage
from src.analyzer import validator
from src.config import AppConfig, CreatorConfig


def _cfg(tmp_path: Path, *, api_key: str | None = "k") -> AppConfig:
    return AppConfig(
        anthropic_api_key=api_key,
        telegram_bot_token=None,
        telegram_chat_id=None,
        headless=True,
        creators=[],
        db_path=tmp_path / "a.db",
        chrome_profile_dir=tmp_path / "prof",
        logs_dir=tmp_path / "logs",
        output_dir=tmp_path / "out",
    )


def _seed_top_quartile_with_features(db_path: Path, n: int = 20) -> None:
    storage.init_db(db_path)
    with storage.connect(db_path) as conn:
        storage.sync_creators(conn, [CreatorConfig("https://linkedin.com/in/a", "A", 1.0)])
        cid = storage.get_creator_id_by_url(conn, "https://linkedin.com/in/a")
        for i in range(n):
            storage.insert_post(
                conn,
                post_urn=f"urn:li:activity:{i}",
                creator_id=cid,
                post_url=None,
                post_text=f"Post number {i}. " * 8,
                reactions=100 + i * 10,
                comments=5 + i,
                reshares=i,
                follower_count_at_collection=10_000,
                post_date=None,
            )
            row = conn.execute(
                "SELECT id FROM posts WHERE post_urn = ?", (f"urn:li:activity:{i}",)
            ).fetchone()
            features = {
                "topic_category": ["ai", "agency_ops", "sales", "marketing", "personal_brand"][i % 5],
                "hook_text": f"Hook for post {i}",
            }
            conn.execute(
                "INSERT INTO post_features (post_id, features_json) VALUES (?, ?)",
                (row["id"], json.dumps(features)),
            )


# --- pure helpers ----------------------------------------------------------


def test_parse_judge_json_happy():
    raw = json.dumps({"winner": "A", "reasoning": "ok", "missed_features": ["foo"]})
    p = validator._parse_judge_json(raw)
    assert p["winner"] == "A"
    assert p["missed_features"] == ["foo"]


def test_parse_judge_json_with_code_fences():
    raw = '```json\n{"winner": "B", "reasoning": "x"}\n```'
    p = validator._parse_judge_json(raw)
    assert p["winner"] == "B"
    assert p["missed_features"] == []


def test_parse_judge_json_unknown_winner_falls_back_to_tie():
    raw = json.dumps({"winner": "Z", "reasoning": "?"})
    assert validator._parse_judge_json(raw)["winner"] == "tie"


def test_parse_judge_json_unparseable():
    p = validator._parse_judge_json("not json at all")
    assert p["winner"] == "tie"
    assert "unparseable" in p["reasoning"]


def test_parse_judge_json_missing_winner_field():
    raw = json.dumps({"reasoning": "no verdict"})
    assert validator._parse_judge_json(raw)["winner"] == "tie"


def test_parse_judge_json_normalizes_bad_missed_features():
    raw = json.dumps({"winner": "A", "missed_features": "not a list"})
    p = validator._parse_judge_json(raw)
    assert p["missed_features"] == []


def test_stats_summary_compacts_known_keys(tmp_path: Path):
    payload = {
        "n_posts_analyzed": 200,
        "categorical_features": {
            "hook_type": {
                "question": {"n": 30, "mean_engagement": 0.01, "rank": 2},
                "bold_claim": {"n": 25, "mean_engagement": 0.02, "rank": 1},
            },
        },
        "numeric_features": {
            "total_word_count": {
                "correlation": -0.2,
                "top_quartile_mean": 180,
                "bottom_quartile_mean": 320,
            },
        },
        "proof_elements_frequency": {"top_quartile": {"client_names": 5}, "bottom_quartile": {}},
    }
    p = tmp_path / "stats.json"
    p.write_text(json.dumps(payload))
    summary = validator._stats_summary(p)
    parsed = json.loads(summary)
    assert parsed["top_per_categorical"]["hook_type"] == "bold_claim"
    assert parsed["numeric_signals"]["total_word_count"]["correlation_with_engagement"] == -0.2
    assert parsed["n_posts_analyzed"] == 200


def test_stats_summary_handles_missing_file(tmp_path: Path):
    s = validator._stats_summary(tmp_path / "missing.json")
    assert "no stats.json" in s


def test_stats_summary_handles_garbage(tmp_path: Path):
    p = tmp_path / "stats.json"
    p.write_text("{not json")
    assert "could not be parsed" in validator._stats_summary(p)


# --- sampling --------------------------------------------------------------


def test_sample_prompts_diversifies_topics_and_skips_held_out(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _seed_top_quartile_with_features(cfg.db_path, n=20)

    # Pretend HELD_OUT_OFFSET is small so we have something to sample.
    with patch.object(validator, "HELD_OUT_OFFSET", 0):
        cases = validator._sample_prompts(cfg, n=10)

    assert 1 <= len(cases) <= 10
    # PER_TOPIC_CAP is 2, so at most 2 per category
    by_topic: dict[str, int] = {}
    for c in cases:
        by_topic[c.topic_category] = by_topic.get(c.topic_category, 0) + 1
    assert all(v <= validator.PER_TOPIC_CAP for v in by_topic.values())


def test_sample_prompts_returns_empty_for_empty_db(tmp_path: Path):
    cfg = _cfg(tmp_path)
    storage.init_db(cfg.db_path)
    assert validator._sample_prompts(cfg, n=5) == []


# --- end-to-end with mocked Anthropic --------------------------------------


class _FakeBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _FakeResp:
    def __init__(self, text: str):
        self.content = [_FakeBlock(text)]


class _FakeAnthropic:
    """Returns canned drafts and judge verdicts.

    Drafts: returns "DRAFT-{seq}" for each draft call.
    Judges: returns winner='A' for the first, then 'B', then 'tie', cycling.
    """

    def __init__(self, *args, **kwargs):
        self._draft_seq = 0
        self._judge_seq = 0
        self.messages = self

    def create(self, **kwargs):
        system = kwargs.get("system", [])
        sys_text = system[0]["text"] if system else ""
        if "evaluating two LinkedIn posts" in sys_text:
            verdicts = [
                {"winner": "A", "reasoning": "A is sharper", "missed_features": ["hook"]},
                {"winner": "B", "reasoning": "B has data", "missed_features": []},
                {"winner": "tie", "reasoning": "even", "missed_features": []},
            ]
            v = verdicts[self._judge_seq % len(verdicts)]
            self._judge_seq += 1
            return _FakeResp(json.dumps(v))
        # otherwise it's a draft call
        self._draft_seq += 1
        return _FakeResp(f"DRAFT-{self._draft_seq}")


def test_run_validation_end_to_end(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _seed_top_quartile_with_features(cfg.db_path, n=10)
    cfg.output_dir.mkdir()
    (cfg.output_dir / "stats.json").write_text(json.dumps({"n_posts_analyzed": 10}))

    old_skill = tmp_path / "old.md"
    new_skill = tmp_path / "new.md"
    old_skill.write_text("OLD SKILL CONTENT")
    new_skill.write_text("NEW SKILL CONTENT")

    with patch.object(validator, "Anthropic", _FakeAnthropic), \
         patch.object(validator, "HELD_OUT_OFFSET", 0):
        result = validator.run_validation(
            cfg,
            old_skill_path=old_skill,
            new_skill_path=new_skill,
            n=3,
            seed=42,
        )

    assert result.n_prompts == 3
    assert result.new_wins + result.old_wins + result.ties == 3
    assert all(p.winner in ("new", "old", "tie") for p in result.pairs)
    assert all(p.new_draft.startswith("DRAFT-") for p in result.pairs)
    assert all(p.old_draft.startswith("DRAFT-") for p in result.pairs)


def test_run_validation_writes_report(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _seed_top_quartile_with_features(cfg.db_path, n=10)
    cfg.output_dir.mkdir()
    old_skill = tmp_path / "old.md"
    new_skill = tmp_path / "new.md"
    old_skill.write_text("OLD")
    new_skill.write_text("NEW")

    with patch.object(validator, "Anthropic", _FakeAnthropic), \
         patch.object(validator, "HELD_OUT_OFFSET", 0):
        result = validator.run_validation(
            cfg, old_skill_path=old_skill, new_skill_path=new_skill, n=3, seed=1
        )

    report = validator.write_report(result, cfg.output_dir)
    assert report.exists()
    body = report.read_text()
    assert "# Skill validation report" in body
    assert "prompts evaluated" in body
    assert "decided win-rate" in body


def test_run_validation_requires_api_key(tmp_path: Path):
    cfg = _cfg(tmp_path, api_key=None)
    old_skill = tmp_path / "old.md"
    new_skill = tmp_path / "new.md"
    old_skill.write_text("a")
    new_skill.write_text("b")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        validator.run_validation(cfg, old_skill_path=old_skill, new_skill_path=new_skill)


def test_run_validation_requires_skill_files(tmp_path: Path):
    cfg = _cfg(tmp_path)
    with pytest.raises(FileNotFoundError):
        validator.run_validation(
            cfg,
            old_skill_path=tmp_path / "missing-old.md",
            new_skill_path=tmp_path / "missing-new.md",
        )


def test_run_validation_winner_mapping_handles_swapped_order(tmp_path: Path):
    """When the new skill is shown as B and judge picks B, winner must be 'new'."""
    cfg = _cfg(tmp_path)
    _seed_top_quartile_with_features(cfg.db_path, n=4)
    cfg.output_dir.mkdir()
    old_skill = tmp_path / "old.md"
    new_skill = tmp_path / "new.md"
    old_skill.write_text("OLD")
    new_skill.write_text("NEW")

    # Force the RNG so new is always B (rng.random() returns value >= 0.5).
    class _NewIsB(_FakeAnthropic):
        def create(self, **kwargs):
            sys_text = kwargs.get("system", [{"text": ""}])[0]["text"]
            if "evaluating two LinkedIn posts" in sys_text:
                # Always pick B (which is the new skill given our forced RNG)
                return _FakeResp(json.dumps({"winner": "B", "reasoning": "B wins"}))
            return _FakeResp(f"DRAFT-{kwargs.get('messages')[0]['content'][:6]}")

    rng_state = []

    class _Rng:
        def __init__(self, seed=None):
            self.seed = seed

        def random(self):
            rng_state.append(0.9)
            return 0.9  # >= 0.5 -> new_is_a False -> new is B

    with patch.object(validator, "Anthropic", _NewIsB), \
         patch.object(validator, "HELD_OUT_OFFSET", 0), \
         patch("src.analyzer.validator.random.Random", _Rng):
        result = validator.run_validation(
            cfg, old_skill_path=old_skill, new_skill_path=new_skill, n=2, seed=0
        )

    assert result.new_wins == 2
    assert result.old_wins == 0
    assert result.ties == 0


def test_to_dict_shape(tmp_path: Path):
    result = validator.ValidationResult(
        n_prompts=2,
        new_wins=1,
        old_wins=1,
        ties=0,
        pairs=[
            validator.PairVerdict(
                prompt="p",
                topic="ai",
                winner="new",
                reasoning="r",
                new_draft="n",
                old_draft="o",
                judge_raw="{}",
            )
        ],
    )
    d = validator.to_dict(result)
    assert d["n_prompts"] == 2
    assert d["win_rate"] == 0.5
    assert d["pairs"][0]["winner"] == "new"
