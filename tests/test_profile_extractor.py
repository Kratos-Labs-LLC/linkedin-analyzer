import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src import storage
from src.analyzer import profile_extractor
from src.config import AppConfig, CreatorConfig


@pytest.fixture(autouse=True)
def _no_sleep():
    """Skip rate-limit sleeps so the suite runs in <1s."""
    with patch("src.analyzer.profile_extractor.time.sleep", lambda *_: None):
        yield


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


def _seed_creator_with_profile(cfg: AppConfig, *, with_posts: bool = True) -> int:
    storage.init_db(cfg.db_path)
    with storage.connect(cfg.db_path) as conn:
        storage.sync_creators(
            conn, [CreatorConfig("https://linkedin.com/in/a", "Alice", 1.0)]
        )
        cid = storage.get_creator_id_by_url(conn, "https://linkedin.com/in/a")
        storage.insert_profile_snapshot(
            conn,
            creator_id=cid,
            follower_count=12_345,
            headline="Founder helping agencies escape the retainer trap",
            about_text="I help agency founders productize their delivery.",
            current_role="Founder",
            current_company="Kratos Labs",
            location="London, UK",
            has_profile_photo=True,
            has_banner=True,
            featured_count=2,
            raw_html="<html />",
        )
        if with_posts:
            for i in range(3):
                storage.insert_post(
                    conn,
                    post_urn=f"urn:li:activity:{i}",
                    creator_id=cid,
                    post_url=None,
                    post_text=f"Post {i}: thoughts on agency operations.",
                    reactions=100 + i * 10,
                    comments=5 + i,
                    reshares=i,
                    follower_count_at_collection=12_345,
                    post_date=None,
                )
        return cid


# --- pure helpers --------------------------------------------------------


def test_parse_and_validate_happy():
    raw = json.dumps(
        {k: ("test" if "type" in k or "voice" in k or "alignment" in k or "breadth" in k else 5)
         for k in profile_extractor.EXPECTED_KEYS}
    )
    out = profile_extractor._parse_and_validate(raw)
    assert out is not None
    assert set(out.keys()) >= profile_extractor.EXPECTED_KEYS


def test_parse_and_validate_handles_code_fences():
    payload = {k: 0 for k in profile_extractor.EXPECTED_KEYS}
    raw = "```json\n" + json.dumps(payload) + "\n```"
    assert profile_extractor._parse_and_validate(raw) is not None


def test_parse_and_validate_rejects_missing_keys():
    raw = json.dumps({"headline_style": "credentialed"})  # only 1 of 10 keys
    assert profile_extractor._parse_and_validate(raw) is None


def test_parse_and_validate_rejects_unparseable():
    assert profile_extractor._parse_and_validate("not json") is None


def test_build_user_message_includes_profile_and_posts():
    class _Row(dict):
        def __getitem__(self, k):
            return dict.__getitem__(self, k)

    profile = _Row(
        headline="My headline",
        about_text="About me text",
        current_role="CTO",
        current_company="Acme",
        location="Berlin",
        has_profile_photo=1,
        has_banner=0,
        featured_count=2,
        follower_count=42_000,
    )
    posts = [_Row(post_text="recent winning post about AI")]
    msg = profile_extractor._build_user_message(profile_row=profile, top_posts=posts)
    assert "My headline" in msg
    assert "About me text" in msg
    assert "CTO" in msg
    assert "Acme" in msg
    assert "42000" in msg
    assert "recent winning post about AI" in msg


# --- end-to-end with mocked Anthropic ------------------------------------


_GOOD_FEATURES = {
    "headline_style": "benefit-led",
    "headline_specificity": 7,
    "about_voice": "practical",
    "about_promise_clarity": 8,
    "about_proof_density": 6,
    "niche_breadth": "narrow",
    "audience_alignment": "founders",
    "cta_in_about": True,
    "featured_section_type": "lead_magnets",
    "post_to_profile_match_score": 9,
}


class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Resp:
    def __init__(self, text):
        self.content = [_Block(text)]


class _FakeAnthropic:
    def __init__(self, *args, **kwargs):
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Resp(json.dumps(_GOOD_FEATURES))


def test_extract_profile_features_writes_one_row_per_creator(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _seed_creator_with_profile(cfg)

    fake = _FakeAnthropic()
    with patch.object(profile_extractor, "Anthropic", lambda **kw: fake):
        summary = profile_extractor.extract_profile_features(cfg)

    assert summary["extracted"] == 1
    assert summary["skipped_no_snapshot"] == 0
    assert summary["failed_parse"] == 0

    with storage.connect(cfg.db_path) as conn:
        rows = conn.execute("SELECT features_json FROM profile_features").fetchall()
    assert len(rows) == 1
    parsed = json.loads(rows[0]["features_json"])
    assert parsed["headline_style"] == "benefit-led"


def test_extract_profile_features_idempotent_on_rerun(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _seed_creator_with_profile(cfg)

    fake = _FakeAnthropic()
    with patch.object(profile_extractor, "Anthropic", lambda **kw: fake):
        a = profile_extractor.extract_profile_features(cfg)
        b = profile_extractor.extract_profile_features(cfg)

    assert a["extracted"] == 1
    assert b["extracted"] == 0
    assert b["skipped_already_done"] == 1
    # Only one Anthropic call total
    assert len(fake.calls) == 1


def test_extract_profile_features_skips_creators_without_snapshots(tmp_path: Path):
    """Creators with no profile snapshot are skipped — no API call burned."""
    cfg = _cfg(tmp_path)
    storage.init_db(cfg.db_path)
    with storage.connect(cfg.db_path) as conn:
        storage.sync_creators(
            conn, [CreatorConfig("https://linkedin.com/in/no-snap", "X", 1.0)]
        )

    fake = _FakeAnthropic()
    with patch.object(profile_extractor, "Anthropic", lambda **kw: fake):
        summary = profile_extractor.extract_profile_features(cfg)

    assert summary["extracted"] == 0
    assert summary["skipped_no_snapshot"] == 1
    assert len(fake.calls) == 0


def test_extract_profile_features_requires_api_key(tmp_path: Path):
    cfg = _cfg(tmp_path, api_key=None)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        profile_extractor.extract_profile_features(cfg)


class _BadParseFakeAnthropic:
    def __init__(self, *args, **kwargs):
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Resp("not json at all")


def test_extract_profile_features_retries_then_skips_on_double_parse_failure(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _seed_creator_with_profile(cfg)

    fake = _BadParseFakeAnthropic()
    with patch.object(profile_extractor, "Anthropic", lambda **kw: fake):
        summary = profile_extractor.extract_profile_features(cfg)

    assert summary["extracted"] == 0
    assert summary["failed_parse"] == 1
    # Two calls: first attempt + retry at temperature=0
    assert len(fake.calls) == 2
    assert fake.calls[0]["temperature"] == 0.2
    assert fake.calls[1]["temperature"] == 0.0


def test_extract_profile_features_uses_cache_control_on_instruction_block(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _seed_creator_with_profile(cfg)

    fake = _FakeAnthropic()
    with patch.object(profile_extractor, "Anthropic", lambda **kw: fake):
        profile_extractor.extract_profile_features(cfg)

    call = fake.calls[0]
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "headline_style" in call["system"][0]["text"]
