"""Safe read/write of creators.yaml that preserves the anchors/standard split."""
from __future__ import annotations

from pathlib import Path

import yaml


def read(path: Path) -> dict:
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return {
        "anchors": list(data.get("anchors") or []),
        "standard": list(data.get("standard") or []),
    }


def write(path: Path, data: dict) -> None:
    """Write a clean, stable YAML with a header comment preserved."""
    anchors = data.get("anchors") or []
    standard = data.get("standard") or []
    payload = {"anchors": anchors, "standard": standard}
    body = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    header = (
        "# Curated LinkedIn creators. Managed via dashboard or hand-edited.\n"
        "# anchors -> weight 1.5; standard -> weight 1.0.\n\n"
    )
    path.write_text(header + body)


def add_creator(path: Path, *, url: str, name: str, anchor: bool) -> None:
    data = read(path)
    entry = {"url": url, "name": name}
    bucket = "anchors" if anchor else "standard"
    other = "standard" if anchor else "anchors"
    data[other] = [e for e in data[other] if e.get("url") != url]
    data[bucket] = [e for e in data[bucket] if e.get("url") != url] + [entry]
    write(path, data)


def remove_creator(path: Path, url: str) -> bool:
    data = read(path)
    before = len(data["anchors"]) + len(data["standard"])
    data["anchors"] = [e for e in data["anchors"] if e.get("url") != url]
    data["standard"] = [e for e in data["standard"] if e.get("url") != url]
    after = len(data["anchors"]) + len(data["standard"])
    if after < before:
        write(path, data)
        return True
    return False


def set_anchor(path: Path, url: str, anchor: bool) -> bool:
    data = read(path)
    src = "standard" if anchor else "anchors"
    dst = "anchors" if anchor else "standard"
    moved = [e for e in data[src] if e.get("url") == url]
    if not moved:
        return False
    data[src] = [e for e in data[src] if e.get("url") != url]
    data[dst] = [e for e in data[dst] if e.get("url") != url] + moved
    write(path, data)
    return True
