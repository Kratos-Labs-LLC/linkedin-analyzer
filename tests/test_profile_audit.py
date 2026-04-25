import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from src.analyzer import profile_audit
from src.config import AppConfig
from src.profile_parser import ProfileSnapshot


def _cfg(tmp_path: Path, *, api_key: str | None = "k") -> AppConfig:
    cfg = AppConfig(
        anthropic_api_key=api_key,
        telegram_bot_token=None,
        telegram_chat_id=None,
        headless=True,
        creators=[],
        db_path=tmp_path / "db.sqlite",
        chrome_profile_dir=tmp_path / "prof",
        logs_dir=tmp_path / "logs",
        output_dir=tmp_path / "out",
    )
    cfg.output_dir.mkdir()
    cfg.chrome_profile_dir.mkdir()
    (cfg.output_dir / "stats.json").write_text(
        json.dumps(
            {
                "n_posts_analyzed": 100,
                "categorical_features": {
                    "headline_style": {
                        "benefit-led": {"rank": 1, "n": 5, "mean_engagement": 0.02},
                        "role-only": {"rank": 2, "n": 4, "mean_engagement": 0.01},
                    }
                },
                "numeric_features": {},
                "profile_axis": {
                    "n_creators_analyzed": 8,
                    "score_field": "growth_rate_per_week",
                    "categorical_features": {
                        "headline_style": {
                            "benefit-led": {"rank": 1, "n": 5, "mean_engagement": 50},
                        }
                    },
                },
            }
        )
    )
    return cfg


_FAKE_SNAPSHOT = ProfileSnapshot(
    follower_count=8_500,
    headline="Founder | Building Acme",
    about_text="Building Acme since 2022. Helping companies do things.",
    current_role="Founder",
    current_company="Acme",
    location="London, UK",
    has_profile_photo=True,
    has_banner=False,
    featured_count=0,
    raw_html="<html />",
)


_GOOD_FEATURES = {
    "headline_style": "role-only",
    "headline_specificity": 3,
    "about_voice": "inspirational",
    "about_promise_clarity": 4,
    "about_proof_density": 2,
    "niche_breadth": "broad",
    "audience_alignment": "mixed",
    "cta_in_about": False,
    "featured_section_type": "none",
    "post_to_profile_match_score": 5,
}


class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Resp:
    def __init__(self, text):
        self.content = [_Block(text)]


class _FakeAnthropic:
    """First call (Sonnet) returns features; second call (Opus) returns audit."""

    def __init__(self, *args, **kwargs):
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["model"].startswith("claude-sonnet"):
            return _Resp(json.dumps(_GOOD_FEATURES))
        # Opus call → audit markdown
        return _Resp(
            "# Verdict\n\nMid pull.\n\n"
            "# Top 3 fixes\n\n1. Headline\n2. About\n3. Featured\n\n"
            "# Headline rewrite\n\n- Option A\n- Option B\n\n"
            "# About rewrite\n\nNew about.\n\n"
            "# Featured section action\n\nAdd lead magnets.\n\n"
            "# Profile-post alignment\n\nMatch is OK.\n\n"
            "# What NOT to change\n\nLocation."
        )


@pytest.fixture(autouse=True)
def _no_my_profile_by_default():
    """Strip MY_PROFILE_URL from the test env unless a test sets it."""
    saved = os.environ.pop("MY_PROFILE_URL", None)
    try:
        yield
    finally:
        if saved is not None:
            os.environ["MY_PROFILE_URL"] = saved


# --- gating --------------------------------------------------------------


def test_audit_returns_none_when_url_unset(tmp_path: Path):
    cfg = _cfg(tmp_path)
    assert "MY_PROFILE_URL" not in os.environ
    assert profile_audit.audit(cfg) is None


def test_audit_requires_api_key(tmp_path: Path):
    os.environ["MY_PROFILE_URL"] = "https://linkedin.com/in/me"
    cfg = _cfg(tmp_path, api_key=None)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        profile_audit.audit(cfg)


def test_audit_requires_stats_json(tmp_path: Path):
    os.environ["MY_PROFILE_URL"] = "https://linkedin.com/in/me"
    cfg = _cfg(tmp_path)
    (cfg.output_dir / "stats.json").unlink()
    with pytest.raises(FileNotFoundError):
        profile_audit.audit(cfg, scrape_fn=lambda *_: _FAKE_SNAPSHOT)


# --- end-to-end with mocks -----------------------------------------------


def test_audit_writes_markdown_and_calls_two_models(tmp_path: Path):
    os.environ["MY_PROFILE_URL"] = "https://linkedin.com/in/me"
    cfg = _cfg(tmp_path)

    fake = _FakeAnthropic()
    with patch.object(profile_audit, "Anthropic", lambda **kw: fake):
        out = profile_audit.audit(cfg, scrape_fn=lambda *_: _FAKE_SNAPSHOT)

    assert out is not None
    assert out.exists()
    body = out.read_text()
    assert "Verdict" in body
    assert "Top 3 fixes" in body

    # Two Anthropic calls: extraction (Sonnet), audit (Opus).
    assert len(fake.calls) == 2
    assert fake.calls[0]["model"].startswith("claude-sonnet")
    assert fake.calls[1]["model"].startswith("claude-opus")


def test_audit_passes_user_profile_into_audit_prompt(tmp_path: Path):
    os.environ["MY_PROFILE_URL"] = "https://linkedin.com/in/me"
    cfg = _cfg(tmp_path)

    fake = _FakeAnthropic()
    with patch.object(profile_audit, "Anthropic", lambda **kw: fake):
        profile_audit.audit(cfg, scrape_fn=lambda *_: _FAKE_SNAPSHOT)

    audit_user_msg = fake.calls[1]["messages"][0]["content"]
    assert "Founder | Building Acme" in audit_user_msg
    assert "<empirical_patterns>" in audit_user_msg
    assert "<voice_profile>" in audit_user_msg


def test_audit_continues_when_feature_extraction_fails(tmp_path: Path):
    os.environ["MY_PROFILE_URL"] = "https://linkedin.com/in/me"
    cfg = _cfg(tmp_path)

    class _ExtractFails(_FakeAnthropic):
        def create(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs["model"].startswith("claude-sonnet"):
                return _Resp("not json")  # parse will fail
            return _Resp("# Verdict\n\nLow pull.\n\n" + "# Top 3 fixes\n\n1.\n2.\n3.\n")

    fake = _ExtractFails()
    with patch.object(profile_audit, "Anthropic", lambda **kw: fake):
        out = profile_audit.audit(cfg, scrape_fn=lambda *_: _FAKE_SNAPSHOT)

    assert out is not None
    body = out.read_text()
    assert "Verdict" in body


def test_audit_handles_empty_snapshot_gracefully(tmp_path: Path):
    """Burner not logged in / selectors rotated — snapshot has nothing.
    Audit should still produce output (the audit prompt sees empty fields)."""
    os.environ["MY_PROFILE_URL"] = "https://linkedin.com/in/me"
    cfg = _cfg(tmp_path)

    empty_snap = ProfileSnapshot(
        follower_count=None,
        headline=None,
        about_text=None,
        current_role=None,
        current_company=None,
        location=None,
        has_profile_photo=None,
        has_banner=None,
        featured_count=None,
        raw_html="<html />",
    )

    fake = _FakeAnthropic()
    with patch.object(profile_audit, "Anthropic", lambda **kw: fake):
        out = profile_audit.audit(cfg, scrape_fn=lambda *_: empty_snap)

    assert out is not None
    # The audit prompt got '(missing)' tokens — that's fine, the model
    # will tell the user to fix it.
    user_msg = fake.calls[-1]["messages"][0]["content"]
    assert "(missing)" in user_msg
