"""Watchdog alert routing tests. The rules themselves are exercised via
dry-run integration; here we focus on Telegram dispatch."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src import storage, watchdog
from src.config import AppConfig, CreatorConfig


def _cfg(tmp_path: Path, *, token: str | None = None, chat_id: str | None = None) -> AppConfig:
    return AppConfig(
        anthropic_api_key=None,
        telegram_bot_token=token,
        telegram_chat_id=chat_id,
        headless=True,
        creators=[],
        db_path=tmp_path / "a.db",
        chrome_profile_dir=tmp_path / "prof",
        logs_dir=tmp_path / "logs",
        output_dir=tmp_path / "out",
    )


def _seed_low_yield_run(db_path: Path) -> None:
    storage.init_db(db_path)
    with storage.connect(db_path) as conn:
        storage.sync_creators(conn, [CreatorConfig("https://linkedin.com/in/a", "A", 1.0)])
        now = datetime.now(timezone.utc).isoformat()
        storage.record_run(
            conn,
            run_date=now[:10],
            started_at=now,
            ended_at=now,
            status="success",
            posts_collected=3,  # below MIN_POSTS_PER_RUN=20 → low_yield rule fires
            creators_scraped=1,
        )


class _FakeResp:
    def __init__(self, status_code=200, text="ok"):
        self.status_code = status_code
        self.text = text


def test_check_and_alert_posts_to_telegram_when_configured(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, token="t-123", chat_id="99")
    _seed_low_yield_run(cfg.db_path)

    calls = []

    def fake_post(url, json, timeout):
        calls.append({"url": url, "json": json, "timeout": timeout})
        return _FakeResp()

    monkeypatch.setattr("src.watchdog.requests.post", fake_post)

    alerts = watchdog.check_and_alert(cfg)
    assert any(a.reason == "low_yield" for a in alerts)
    assert len(calls) == 1
    assert calls[0]["url"] == "https://api.telegram.org/bott-123/sendMessage"
    assert calls[0]["json"]["chat_id"] == "99"
    assert "low_yield" in calls[0]["json"]["text"]


def test_check_and_alert_silent_when_token_only(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, token="t-123", chat_id=None)
    _seed_low_yield_run(cfg.db_path)

    calls = []
    monkeypatch.setattr(
        "src.watchdog.requests.post",
        lambda *a, **kw: (calls.append(1), _FakeResp())[1],
    )

    alerts = watchdog.check_and_alert(cfg)
    assert alerts  # rule still fires
    assert calls == []  # but no HTTP


def test_check_and_alert_silent_when_chat_only(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, token=None, chat_id="99")
    _seed_low_yield_run(cfg.db_path)

    calls = []
    monkeypatch.setattr(
        "src.watchdog.requests.post",
        lambda *a, **kw: (calls.append(1), _FakeResp())[1],
    )

    watchdog.check_and_alert(cfg)
    assert calls == []


def test_send_test_alert_requires_config(tmp_path):
    ok, detail = watchdog.send_test_alert(_cfg(tmp_path))
    assert ok is False
    assert "not configured" in detail


def test_send_test_alert_happy_path(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, token="abc", chat_id="42")
    captured = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return _FakeResp()

    monkeypatch.setattr("src.watchdog.requests.post", fake_post)
    ok, detail = watchdog.send_test_alert(cfg)
    assert ok is True
    assert detail == "delivered"
    assert captured["url"] == "https://api.telegram.org/botabc/sendMessage"
    assert captured["json"]["chat_id"] == "42"


def test_send_test_alert_surfaces_http_error(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, token="abc", chat_id="42")
    monkeypatch.setattr(
        "src.watchdog.requests.post",
        lambda *a, **kw: _FakeResp(status_code=401, text="Unauthorized"),
    )
    ok, detail = watchdog.send_test_alert(cfg)
    assert ok is False
    assert "401" in detail
    assert "Unauthorized" in detail
