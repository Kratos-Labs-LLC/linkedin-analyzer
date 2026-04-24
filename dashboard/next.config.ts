import path from 'node:path';
import { config as loadEnv } from 'dotenv';
import type { NextConfig } from 'next';

// Load the repo-root .env so server code (Telegram creds, Anthropic key)
// sees the same values the Python side does.
loadEnv({ path: path.resolve(process.cwd(), '..', '.env') });

const nextConfig: NextConfig = {
  // Pin workspace root to the dashboard dir. Otherwise Turbopack walks up
  // and tries to resolve symlinks (e.g. ../.venv/bin/python) that point
  // outside the repo, which crashes the build.
  turbopack: {
    root: path.resolve(process.cwd()),
  },
  // better-sqlite3 is a native addon; don't try to bundle it.
  serverExternalPackages: ['better-sqlite3'],
};

export default nextConfig;
