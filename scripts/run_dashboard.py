#!/usr/bin/env python3
"""Launch the local control-panel Flask app on 127.0.0.1.

Do NOT bind to 0.0.0.0 — the dashboard has no auth and can edit
creators.yaml, launch subprocesses, and view collected posts. Localhost only.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.dashboard.app import create_app  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(
            f"Refusing to bind to {args.host}: dashboard is unauthenticated and "
            "must stay local. Use 127.0.0.1.",
            file=sys.stderr,
        )
        return 2

    logging.basicConfig(level=logging.INFO)
    app = create_app()
    print(f"Dashboard on http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug)
    return 0


if __name__ == "__main__":
    sys.exit(main())
