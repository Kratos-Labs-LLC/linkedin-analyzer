#!/usr/bin/env bash
# Wrapper for the Next.js dashboard. Starts dev server bound to 127.0.0.1:3000.
set -euo pipefail
cd "$(dirname "$0")/../dashboard"
if [ ! -d node_modules ]; then
  echo "Installing dependencies (one-time, compiles better-sqlite3)..."
  npm install
fi
exec npm run dev
