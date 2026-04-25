import json
import logging
import os
from pathlib import Path

import pytest

from src import log_setup


@pytest.fixture(autouse=True)
def _reset():
    log_setup.reset_for_testing()
    saved = os.environ.pop("JSON_LOGS", None)
    yield
    log_setup.reset_for_testing()
    if saved is not None:
        os.environ["JSON_LOGS"] = saved


def test_text_mode_default(tmp_path: Path, caplog):
    log_setup.setup_logging(log_file=tmp_path / "out.log")
    log = logging.getLogger("test")
    log.info("hello")
    body = (tmp_path / "out.log").read_text()
    # Human-readable line, not JSON
    assert "INFO" in body
    assert "hello" in body
    assert not body.lstrip().startswith("{")


def test_json_mode_via_env_var(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JSON_LOGS", "1")
    log_setup.setup_logging(log_file=tmp_path / "out.log")
    log = logging.getLogger("test.module")
    log.info("payload here")
    body = (tmp_path / "out.log").read_text().strip()
    line = body.splitlines()[-1]
    parsed = json.loads(line)
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "test.module"
    assert parsed["message"] == "payload here"
    assert "ts" in parsed


def test_json_mode_includes_extra_fields(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JSON_LOGS", "1")
    log_setup.setup_logging(log_file=tmp_path / "out.log")
    log = logging.getLogger("x")
    log.info("with extra", extra={"creator_id": 42, "step": "extract"})
    body = (tmp_path / "out.log").read_text().strip().splitlines()
    parsed = json.loads(body[-1])
    assert parsed["creator_id"] == 42
    assert parsed["step"] == "extract"


def test_setup_logging_is_idempotent(tmp_path: Path):
    log_setup.setup_logging(log_file=tmp_path / "out.log")
    log_setup.setup_logging(log_file=tmp_path / "other.log")  # ignored
    log = logging.getLogger("test")
    log.info("x")
    # Only the first file got handlers
    assert (tmp_path / "out.log").exists()
    assert not (tmp_path / "other.log").exists()


def test_json_mode_handles_non_serializable_extra(tmp_path: Path, monkeypatch):
    """Non-JSON-serializable values in extra= should fall back to repr,
    not crash the logger."""
    monkeypatch.setenv("JSON_LOGS", "1")
    log_setup.setup_logging(log_file=tmp_path / "out.log")
    log = logging.getLogger("x")

    class _Weird:
        def __repr__(self):
            return "<Weird>"

    log.info("ok", extra={"thing": _Weird()})
    parsed = json.loads((tmp_path / "out.log").read_text().strip().splitlines()[-1])
    assert parsed["thing"] == "<Weird>"
