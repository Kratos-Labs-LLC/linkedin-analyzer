# Dashboard

Next.js control panel for the LinkedIn Analyzer. Unauthenticated, local only.

## Stack

- Next.js 16 (App Router) + React 19 + TypeScript
- Tailwind CSS 4 with a custom dark theme (no shadcn, no component library)
- `better-sqlite3` — reads the SQLite DB at `../db/analyzer.db`
- `js-yaml` — round-trips `../creators.yaml`
- `dotenv` — loads `../.env` so Telegram / Anthropic creds are shared with the Python side
- `sonner` — toasts
- Server Actions for every mutation, 2s-polled log tail for live output

## Run

```bash
npm install        # first time — compiles better-sqlite3 native addon
npm run dev        # http://127.0.0.1:3000
```

or from the repo root:

```bash
bash scripts/run_dashboard.sh
```

Both `dev` and `start` scripts pin `-H 127.0.0.1` — do not change that.

## Test + build

```bash
npm run test       # Vitest (creators-yaml round-trip, engagement score, Telegram)
npm run build      # type check + production compile
```

## What talks to what

- **Reads from DB**: every page is a Server Component that calls `lib/db.ts` directly.
- **Writes to DB**: only `lib/actions.ts::*` server actions, after a creators.yaml edit triggers `syncCreatorsFromYaml`. The Python collector also writes — both can coexist because writes are short and SQLite locks handle serialization.
- **Spawns Python**: `lib/jobs.ts::launch` spawns `../.venv/bin/python scripts/run_daily.py` (or `run_analysis.py`) as a detached child, tees stdout+stderr to `../logs/web-<kind>-<ts>.log`, records state at `../logs/current_job.json`. SIGTERM to the pgid cancels.
- **Telegram**: `lib/telegram.ts` POSTs to the bot API using env vars loaded from `../.env`.

## File map

```
dashboard/
├── app/                # routes — all Server Components except log-tail
│   ├── layout.tsx
│   ├── page.tsx        # overview
│   ├── creators/
│   ├── posts/
│   ├── runs/
│   ├── analysis/
│   ├── skill/
│   ├── actions/
│   └── api/job/log/    # GET log tail (path-restricted)
├── components/
│   ├── sidebar.tsx     # client: nav state
│   ├── log-tail.tsx    # client: 2s polling
│   ├── action-form.tsx # client: server action + toast wrapper
│   ├── sparkline.tsx   # server: SVG chart
│   └── ui.tsx          # server: hand-rolled primitives (Panel, Button, Pill…)
└── lib/
    ├── paths.ts        # REPO_ROOT + derived
    ├── db.ts           # better-sqlite3 singleton + typed queries
    ├── creators-yaml.ts
    ├── jobs.ts         # spawn/cancel/tail
    ├── telegram.ts
    ├── readiness.ts    # day-31 gate logic
    └── actions.ts      # 'use server' mutations
```

## Notes

- `next.config.ts` pins `turbopack.root` to the dashboard dir. Without this, Turbopack infers the repo root via adjacent lockfiles and follows the `.venv` symlinks out of the filesystem root, crashing the build.
- `better-sqlite3` is declared in `serverExternalPackages` so Next doesn't try to bundle the native module.
