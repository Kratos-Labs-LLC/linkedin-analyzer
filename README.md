# LinkedIn Post Analyzer

Collects ~1,500 LinkedIn posts from 25–30 curated creators over 30 days via a
pre-authenticated burner account, scores them on follower-normalized
engagement, then runs a Claude-powered feature extraction + synthesis
pipeline to produce a new skill `linkedin-high-engagement-writer`.

See the build spec in the repo commit history for business logic details.

## Layout

```
linkedin-analyzer/
├── creators.yaml              # curated creator list (you edit)
├── .env                       # secrets (not checked in)
├── scripts/
│   ├── auth_setup.py          # one-time burner login
│   ├── run_daily.py           # invoked by launchd
│   ├── run_analysis.py        # day-31 analysis
│   ├── run_dashboard.py       # local Flask control panel
│   └── test_telegram.py       # test ping / --discover chat_id
├── com.dugg.linkedin-analyzer.plist
└── src/
    ├── config.py  storage.py  parser.py  collector.py  watchdog.py
    ├── analyzer/
    │   ├── extractor.py  stats.py  synthesizer.py
    └── dashboard/
        ├── app.py  actions.py  creators_yaml.py
        └── templates/*.html
```

Runtime dirs `chrome_profile/`, `db/`, `logs/`, `output/` are created on first
run and gitignored.

## One-time setup (Mac)

```bash
python3 -m venv .venv
source .venv/bin/activate        # activates the venv — prompt now shows (.venv)
pip install -r requirements.txt
playwright install chromium

cp .env.example .env
# edit .env: add ANTHROPIC_API_KEY; optionally TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID

# Edit creators.yaml: 25-30 entries meeting the inclusion criteria in the
# file's header comment. 3-5 anchors, rest standard.

# Burner auth — headed browser, log in, press Enter when done:
python scripts/auth_setup.py
```

**Important:** every new terminal session needs `source .venv/bin/activate`
before running `python scripts/...` — otherwise you'll get
`ModuleNotFoundError: No module named 'requests'` etc. If you prefer, call
`.venv/bin/python scripts/whatever.py` directly without activating.

`auth_setup.py` writes `chrome_profile/.authed` once complete. The collector
refuses to run until that sentinel exists.

## Daily operation (Mac)

Install launchd job (runs every day at 10:00 local, wakes Mac if needed):

```bash
# 1. Edit com.dugg.linkedin-analyzer.plist — replace REPLACE_WITH_ABSOLUTE_PATH_TO_linkedin-analyzer
#    with the absolute path to this repo (3 occurrences).
# 2. Validate & install:
plutil -lint com.dugg.linkedin-analyzer.plist
cp com.dugg.linkedin-analyzer.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.dugg.linkedin-analyzer.plist
```

Energy Saver: turn ON "Wake for network access" so the Mac wakes for the
scheduled run. `caffeinate -i -t 3600` keeps it awake for the scrape window.

Manual run:

```bash
python scripts/run_daily.py
python scripts/run_daily.py --dry-run   # exercise DB loop without Playwright
```

Logs: `logs/YYYY-MM-DD.log` plus `logs/launchd.stdout.log` /
`logs/launchd.stderr.log`.

## Alerts (Telegram)

The watchdog posts alerts to a Telegram bot when:

- Most recent run had status `auth_error`
- 2+ consecutive non-success runs
- Last successful run collected < 20 posts (selectors may have drifted)
- No posts collected in the last 24 hours

With no bot configured, alerts log to stdout only.

**One-time setup:**

1. In Telegram, message `@BotFather` → `/newbot` → follow prompts → copy the
   HTTP API token it gives you.
2. Find your bot (the handle @BotFather gave you) and send it any message —
   this is what makes your chat visible to `getUpdates`.
3. Put the token in `.env` as `TELEGRAM_BOT_TOKEN=…`.
4. Discover your chat_id:
   ```bash
   python scripts/test_telegram.py --discover
   ```
   Copy the number from the output and set `TELEGRAM_CHAT_ID=…` in `.env`.
5. Verify end-to-end:
   ```bash
   python scripts/test_telegram.py
   ```
   You should see `OK: delivered` and a message in Telegram.

You can also click **Send test Telegram alert** on the dashboard's Actions
page — same round-trip, no terminal needed.

## Session recovery

LinkedIn can invalidate the session (captcha, "new device" challenge,
password change). Signs: `auth_error` in `runs` table, `/login` or
`/checkpoint` in logs.

Fix:

```bash
python scripts/auth_setup.py   # solve the challenge, press Enter
```

## Day 31 — analysis

Preconditions enforced by `run_analysis.py`:

- ≥ 30 days since first collected post
- ≥ 800 posts collected
- ≥ 15 creators with ≥ 20 posts each

Override with `--force`. The synthesizer needs the existing
`leadmagnet-post-writer/SKILL.md` as context.

```bash
python scripts/run_analysis.py \
  --leadmagnet-skill-path ~/.claude/skills/leadmagnet-post-writer/SKILL.md
```

Outputs to `output/`:

- `stats.json` — feature-engagement correlations
- `top_posts.md` / `bottom_posts.md` — top/bottom 50 posts verbatim
- `linkedin-high-engagement-writer/SKILL.md` — the generated skill (review, edit, install to `~/.claude/skills/`)

## Cost expectations

- Feature extraction (~300 posts × Sonnet with prompt caching): ~$2
- Synthesis (single Opus call, ~30k input tokens): ~$1
- **Total day-31 cost: under $10** (spec budgets $30 as safety margin)

## Model notes

- Extractor uses `claude-sonnet-4-5` (the spec's "claude-sonnet-4" is
  outdated — 4.5 is the current Sonnet, similar price, better quality).
- Synthesizer uses `claude-opus-4-7` per spec.

## Dashboard

A local Flask control panel exposes every knob in one place: view collection
status, edit `creators.yaml`, browse posts, inspect run history, launch
collection or day-31 analysis jobs, tail their logs, and view the generated
SKILL.md.

```bash
python scripts/run_dashboard.py          # http://127.0.0.1:8765
python scripts/run_dashboard.py --port 9000
```

The dashboard has **no authentication** and can edit files, spawn
subprocesses, and read all collected posts. `run_dashboard.py` refuses to
bind to anything other than 127.0.0.1 — keep it that way.

Routes:

- `/` — overview: totals, readiness gates, recent runs, current job
- `/creators` — add / remove creators, toggle anchor ↔ standard (writes `creators.yaml`)
- `/posts` — filterable post browser, click through for full text + extracted features
- `/runs` — last 100 run records with error details
- `/analysis` — day-31 readiness, launch form (supply `leadmagnet-post-writer/SKILL.md` path)
- `/skill` — view generated SKILL.md, stats.json, top/bottom posts
- `/actions` — launch daily or dry-run, tail current job log, cancel

## Development

```bash
pytest tests/              # storage + parser + stats + dashboard tests
python scripts/run_daily.py --dry-run
python scripts/run_dashboard.py
```

## Non-goals

No dashboard, no comment scraping, no home-feed scraping, no multi-platform
support, no stats significance tests. See §2 of the spec.
