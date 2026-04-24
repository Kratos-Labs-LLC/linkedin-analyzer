"""Config loader: .env + creators.yaml -> typed dicts."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "db" / "analyzer.db"
DEFAULT_CREATORS_PATH = REPO_ROOT / "creators.yaml"
DEFAULT_CHROME_PROFILE = REPO_ROOT / "chrome_profile"
DEFAULT_LOGS_DIR = REPO_ROOT / "logs"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "output"

ANCHOR_WEIGHT = 1.5
STANDARD_WEIGHT = 1.0

_SLUG_RE = re.compile(r"linkedin\.com/in/([^/?#]+)", re.IGNORECASE)


@dataclass
class CreatorConfig:
    linkedin_url: str
    display_name: str
    weight: float

    @property
    def slug(self) -> str:
        m = _SLUG_RE.search(self.linkedin_url)
        if not m:
            raise ValueError(f"Cannot parse LinkedIn slug from {self.linkedin_url!r}")
        return m.group(1).strip("/")

    @property
    def activity_url(self) -> str:
        return f"https://www.linkedin.com/in/{self.slug}/recent-activity/all/"

    @property
    def profile_url(self) -> str:
        return f"https://www.linkedin.com/in/{self.slug}/"


@dataclass
class AppConfig:
    anthropic_api_key: str | None
    slack_webhook_url: str | None
    headless: bool
    creators: list[CreatorConfig]
    db_path: Path
    chrome_profile_dir: Path
    logs_dir: Path
    output_dir: Path


def load_creators(path: Path = DEFAULT_CREATORS_PATH) -> list[CreatorConfig]:
    with open(path) as f:
        data = yaml.safe_load(f) or {}

    creators: list[CreatorConfig] = []
    for entry in data.get("anchors") or []:
        creators.append(CreatorConfig(entry["url"], entry.get("name", ""), ANCHOR_WEIGHT))
    for entry in data.get("standard") or []:
        creators.append(CreatorConfig(entry["url"], entry.get("name", ""), STANDARD_WEIGHT))

    seen: set[str] = set()
    for c in creators:
        if c.linkedin_url in seen:
            raise ValueError(f"Duplicate creator URL in {path}: {c.linkedin_url}")
        seen.add(c.linkedin_url)

    return creators


def load_config(creators_path: Path = DEFAULT_CREATORS_PATH) -> AppConfig:
    load_dotenv(REPO_ROOT / ".env")
    return AppConfig(
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
        slack_webhook_url=os.environ.get("SLACK_WEBHOOK_URL") or None,
        headless=os.environ.get("HEADLESS", "1") != "0",
        creators=load_creators(creators_path),
        db_path=Path(os.environ.get("ANALYZER_DB", DEFAULT_DB_PATH)),
        chrome_profile_dir=Path(os.environ.get("CHROME_PROFILE", DEFAULT_CHROME_PROFILE)),
        logs_dir=DEFAULT_LOGS_DIR,
        output_dir=DEFAULT_OUTPUT_DIR,
    )
