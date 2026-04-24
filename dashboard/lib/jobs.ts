import 'server-only';

import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

import { LOGS_DIR, REPO_ROOT, VENV_PYTHON } from './paths';

export type JobKind = 'daily' | 'dry_run' | 'analysis';

export const JOB_KINDS: readonly JobKind[] = ['daily', 'dry_run', 'analysis'] as const;

export type JobState = {
  kind: JobKind;
  pid: number;
  log_path: string;
  started_at: string;
  ended_at: string | null;
  returncode: number | null;
  args: string[];
};

const STATE_FILE = 'current_job.json';

function statePath() {
  fs.mkdirSync(LOGS_DIR, { recursive: true });
  return path.join(LOGS_DIR, STATE_FILE);
}

export function loadState(): JobState | null {
  const p = statePath();
  if (!fs.existsSync(p)) return null;
  try {
    return JSON.parse(fs.readFileSync(p, 'utf8')) as JobState;
  } catch {
    return null;
  }
}

export function saveState(state: JobState | null) {
  const p = statePath();
  if (state === null) {
    if (fs.existsSync(p)) fs.unlinkSync(p);
    return;
  }
  fs.writeFileSync(p, JSON.stringify(state, null, 2));
}

function pidAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (e) {
    const code = (e as NodeJS.ErrnoException).code;
    if (code === 'ESRCH') return false;
    if (code === 'EPERM') return true;
    return false;
  }
}

export function refreshState(): JobState | null {
  const s = loadState();
  if (!s) return null;
  if (s.ended_at) return s;
  if (!pidAlive(s.pid)) {
    s.ended_at = new Date().toISOString();
    s.returncode = -1; // orphan; we didn't observe exit
    saveState(s);
  }
  return s;
}

export function isRunning(): boolean {
  const s = refreshState();
  return !!s && !s.ended_at;
}

function scriptFor(kind: JobKind): { argv: string[] } {
  if (kind === 'daily') return { argv: [VENV_PYTHON, path.join('scripts', 'run_daily.py')] };
  if (kind === 'dry_run')
    return { argv: [VENV_PYTHON, path.join('scripts', 'run_daily.py'), '--dry-run'] };
  if (kind === 'analysis')
    return { argv: [VENV_PYTHON, path.join('scripts', 'run_analysis.py')] };
  throw new Error(`unknown job kind: ${kind satisfies never}`);
}

export function launch(kind: JobKind, extraArgs: string[] = []): JobState {
  if (isRunning()) throw new Error('Another job is already running.');
  if (!fs.existsSync(VENV_PYTHON)) {
    throw new Error(
      `Python venv missing at ${VENV_PYTHON}. Run \`python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt\` in the repo root.`,
    );
  }

  fs.mkdirSync(LOGS_DIR, { recursive: true });
  const ts = new Date().toISOString().replace(/[-:.]/g, '');
  const logPath = path.join(LOGS_DIR, `web-${kind}-${ts}.log`);

  const { argv } = scriptFor(kind);
  const fullArgs = [...argv.slice(1), ...extraArgs];
  const logFd = fs.openSync(logPath, 'w');

  const child = spawn(argv[0], fullArgs, {
    cwd: REPO_ROOT,
    stdio: ['ignore', logFd, logFd],
    detached: true,
    env: process.env,
  });
  child.unref();
  fs.closeSync(logFd);

  const state: JobState = {
    kind,
    pid: child.pid!,
    log_path: logPath,
    started_at: new Date().toISOString(),
    ended_at: null,
    returncode: null,
    args: [argv[0], ...fullArgs],
  };
  saveState(state);

  // Observe eventual exit and update state.
  child.on('exit', (code) => {
    const latest = loadState();
    if (!latest || latest.pid !== state.pid) return;
    latest.ended_at = new Date().toISOString();
    latest.returncode = code ?? -1;
    saveState(latest);
  });

  return state;
}

export function cancel(): boolean {
  const s = loadState();
  if (!s || s.ended_at) return false;
  try {
    process.kill(-s.pid, 'SIGTERM'); // kill process group
  } catch {
    try {
      process.kill(s.pid, 'SIGTERM');
    } catch {
      // already gone
    }
  }
  refreshState();
  return true;
}

export function clearState() {
  saveState(null);
}

export function tailLog(logPath: string, maxBytes = 64 * 1024): string {
  if (!fs.existsSync(logPath)) return '';
  const { size } = fs.statSync(logPath);
  const start = Math.max(0, size - maxBytes);
  const fd = fs.openSync(logPath, 'r');
  try {
    const buf = Buffer.alloc(size - start);
    fs.readSync(fd, buf, 0, buf.length, start);
    return buf.toString('utf8');
  } finally {
    fs.closeSync(fd);
  }
}
