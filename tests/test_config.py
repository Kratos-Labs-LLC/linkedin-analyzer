import pytest

from src.config import CreatorConfig, load_creators


def test_creator_slug_and_urls():
    c = CreatorConfig("https://linkedin.com/in/some-user", "S", 1.0)
    assert c.slug == "some-user"
    assert c.profile_url.endswith("/some-user/")
    assert c.activity_url.endswith("/some-user/recent-activity/all/")


def test_creator_slug_with_trailing_slash():
    c = CreatorConfig("https://www.linkedin.com/in/user-42/", "U", 1.0)
    assert c.slug == "user-42"


def test_creator_slug_invalid_raises():
    c = CreatorConfig("https://example.com/some-user", "X", 1.0)
    with pytest.raises(ValueError):
        _ = c.slug


def test_load_creators_placeholder_file(tmp_path):
    # The actual creators.yaml ships with placeholder entries; verify it parses.
    from src.config import DEFAULT_CREATORS_PATH
    creators = load_creators(DEFAULT_CREATORS_PATH)
    assert len(creators) >= 2
    anchor_weights = {c.weight for c in creators if "anchor" in c.linkedin_url}
    assert anchor_weights <= {1.5}
