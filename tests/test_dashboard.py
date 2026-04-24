from pathlib import Path

import pytest

from src import storage
from src.config import AppConfig, CreatorConfig
from src.dashboard import creators_yaml
from src.dashboard.app import create_app


@pytest.fixture
def cfg(tmp_path: Path, monkeypatch) -> AppConfig:
    # Give the dashboard its own yaml file to edit without touching the repo's.
    yaml_path = tmp_path / "creators.yaml"
    creators_yaml.write(yaml_path, {
        "anchors": [{"url": "https://linkedin.com/in/anchor-one", "name": "Anchor One"}],
        "standard": [{"url": "https://linkedin.com/in/standard-one", "name": "Standard One"}],
    })
    monkeypatch.setattr("src.dashboard.app.DEFAULT_CREATORS_PATH", yaml_path)

    creators = [
        CreatorConfig("https://linkedin.com/in/anchor-one", "Anchor One", 1.5),
        CreatorConfig("https://linkedin.com/in/standard-one", "Standard One", 1.0),
    ]
    return AppConfig(
        anthropic_api_key=None,
        telegram_bot_token=None,
        telegram_chat_id=None,
        headless=True,
        creators=creators,
        db_path=tmp_path / "a.db",
        chrome_profile_dir=tmp_path / "profile",
        logs_dir=tmp_path / "logs",
        output_dir=tmp_path / "out",
    )


@pytest.fixture
def client(cfg):
    app = create_app(cfg)
    app.config.update(TESTING=True)
    storage.init_db(cfg.db_path)
    with storage.connect(cfg.db_path) as conn:
        storage.sync_creators(conn, cfg.creators)
    return app.test_client()


def test_index_renders(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Collection overview" in resp.data


def test_creators_page_lists(client):
    resp = client.get("/creators")
    assert resp.status_code == 200
    assert b"Anchor One" in resp.data
    assert b"Standard One" in resp.data


def test_creators_add_and_remove(client, cfg):
    resp = client.post("/creators/add", data={
        "url": "https://linkedin.com/in/new-person",
        "name": "New Person",
        "anchor": "on",
    }, follow_redirects=True)
    assert resp.status_code == 200
    data = creators_yaml.read(cfg.db_path.parent / "creators.yaml")
    assert any(e["url"] == "https://linkedin.com/in/new-person" for e in data["anchors"])

    resp = client.post("/creators/remove", data={
        "url": "https://linkedin.com/in/new-person",
    }, follow_redirects=True)
    assert resp.status_code == 200
    data = creators_yaml.read(cfg.db_path.parent / "creators.yaml")
    assert not any(e["url"] == "https://linkedin.com/in/new-person" for e in data["anchors"])


def test_creators_toggle_anchor(client, cfg):
    resp = client.post("/creators/toggle_anchor", data={
        "url": "https://linkedin.com/in/standard-one",
        "anchor": "1",
    }, follow_redirects=True)
    assert resp.status_code == 200
    data = creators_yaml.read(cfg.db_path.parent / "creators.yaml")
    assert any(e["url"] == "https://linkedin.com/in/standard-one" for e in data["anchors"])


def test_posts_page_empty(client):
    resp = client.get("/posts")
    assert resp.status_code == 200
    assert b"No posts" in resp.data


def test_runs_page_empty(client):
    resp = client.get("/runs")
    assert resp.status_code == 200
    assert b"No runs" in resp.data


def test_analysis_page_shows_readiness(client):
    resp = client.get("/analysis")
    assert resp.status_code == 200
    assert b"days of collection" in resp.data


def test_skill_page_no_output(client):
    resp = client.get("/skill")
    assert resp.status_code == 200
    assert b"No generated skill yet" in resp.data


def test_actions_page_no_job(client):
    resp = client.get("/actions")
    assert resp.status_code == 200
    assert b"No job launched" in resp.data


def test_post_detail_with_post(client, cfg):
    with storage.connect(cfg.db_path) as conn:
        cid = storage.get_creator_id_by_url(conn, "https://linkedin.com/in/anchor-one")
        storage.insert_post(
            conn,
            post_urn="urn:li:activity:test",
            creator_id=cid,
            post_url="https://linkedin.com/feed/update/urn:li:activity:test",
            post_text="Hello world from a top post.",
            reactions=100,
            comments=10,
            reshares=2,
            follower_count_at_collection=10_000,
            post_date=None,
        )
        post_id = conn.execute("SELECT id FROM posts").fetchone()["id"]

    resp = client.get(f"/posts/{post_id}")
    assert resp.status_code == 200
    assert b"Hello world" in resp.data


def test_post_detail_404(client):
    resp = client.get("/posts/99999")
    assert resp.status_code == 404


def test_creators_yaml_preserves_structure(tmp_path: Path):
    path = tmp_path / "c.yaml"
    creators_yaml.write(path, {
        "anchors": [{"url": "a", "name": "A"}],
        "standard": [{"url": "b", "name": "B"}],
    })
    creators_yaml.add_creator(path, url="c", name="C", anchor=False)
    data = creators_yaml.read(path)
    assert len(data["anchors"]) == 1
    assert len(data["standard"]) == 2

    creators_yaml.set_anchor(path, "c", True)
    data = creators_yaml.read(path)
    assert any(e["url"] == "c" for e in data["anchors"])
    assert not any(e["url"] == "c" for e in data["standard"])

    creators_yaml.remove_creator(path, "a")
    data = creators_yaml.read(path)
    assert not any(e["url"] == "a" for e in data["anchors"])


def test_actions_test_alert_not_configured(client):
    resp = client.post("/actions/test_alert", follow_redirects=True)
    assert resp.status_code == 200
    assert b"Telegram not configured" in resp.data


def test_actions_test_alert_configured(tmp_path: Path, monkeypatch):
    from src.config import AppConfig, CreatorConfig
    from src.dashboard.app import create_app

    cfg = AppConfig(
        anthropic_api_key=None,
        telegram_bot_token="fake-token",
        telegram_chat_id="12345",
        headless=True,
        creators=[CreatorConfig("https://linkedin.com/in/a", "A", 1.0)],
        db_path=tmp_path / "a.db",
        chrome_profile_dir=tmp_path / "profile",
        logs_dir=tmp_path / "logs",
        output_dir=tmp_path / "out",
    )

    class FakeResp:
        status_code = 200
        text = "ok"

    captured = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return FakeResp()

    monkeypatch.setattr("src.watchdog.requests.post", fake_post)
    app = create_app(cfg)
    app.config.update(TESTING=True)

    resp = app.test_client().post("/actions/test_alert", follow_redirects=True)
    assert resp.status_code == 200
    assert b"delivered" in resp.data
    assert captured["url"] == "https://api.telegram.org/botfake-token/sendMessage"
    assert captured["json"]["chat_id"] == "12345"
    assert "test ping" in captured["json"]["text"]
