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
│   └── run_analysis.py        # day-31 analysis
├── com.dugg.linkedin-analyzer.plist
└── src/
    ├── config.py  storage.py  parser.py  collector.py  watchdog.py
    └── analyzer/
        ├── extractor.py  stats.py  synthesizer.py
```

Runtime dirs `chrome_profile/`, `db/`, `logs/`, `output/` are created on first
run and gitignored.

## One-time setup (Mac)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

cp .env.example .env
# edit .env: add ANTHROPIC_API_KEY; optionally SLACK_WEBHOOK_URL

# Edit creators.yaml: 25-30 entries meeting the inclusion criteria in the
# file's header comment. 3-5 anchors, rest standard.

# Burner auth — headed browser, log in, press Enter when done:
python scripts/auth_setup.py
```

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

## Alerts

If `SLACK_WEBHOOK_URL` is set in `.env`, the watchdog posts an alert when:

- Most recent run had status `auth_error`
- 2+ consecutive non-success runs
- Last successful run collected < 20 posts (selectors may have drifted)
- No posts collected in the last 24 hours

With no webhook, alerts log to stdout only.

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

## Development

```bash
pytest tests/              # storage + parser + stats unit tests
python scripts/run_daily.py --dry-run
```

## Non-goals

No dashboard, no comment scraping, no home-feed scraping, no multi-platform
support, no stats significance tests. See §2 of the spec.
