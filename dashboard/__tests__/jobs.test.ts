import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// Temp dirs that `@/lib/paths` will be mocked to. Recreated per test.
const hoisted = vi.hoisted(() => ({
  tmp: '' as string,
}));

vi.mock('@/lib/paths', () => ({
  get REPO_ROOT() {
    return hoisted.tmp;
  },
  get LOGS_DIR() {
    return path.join(hoisted.tmp, 'logs');
  },
  get VENV_PYTHON() {
    return '/usr/bin/true';
  },
  get DB_PATH() {
    return path.join(hoisted.tmp, 'db.sqlite');
  },
  get CREATORS_YAML_PATH() {
    return path.join(hoisted.tmp, 'creators.yaml');
  },
  get CHROME_PROFILE_DIR() {
    return path.join(hoisted.tmp, 'chrome_profile');
  },
  get OUTPUT_DIR() {
    return path.join(hoisted.tmp, 'output');
  },
  get AUTH_SENTINEL() {
    return path.join(hoisted.tmp, 'chrome_profile', '.authed');
  },
  ANCHOR_WEIGHT: 1.5,
  STANDARD_WEIGHT: 1.0,
}));

// Mock spawn so launch() doesn't actually run a child. Each test can override.
const spawnMock = vi.fn();
vi.mock('node:child_process', async () => {
  const real = await vi.importActual<typeof import('node:child_process')>('node:child_process');
  return { ...real, spawn: spawnMock };
});

describe('jobs', () => {
  let jobs: typeof import('@/lib/jobs');

  beforeEach(async () => {
    hoisted.tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'jobs-test-'));
    fs.mkdirSync(path.join(hoisted.tmp, 'logs'), { recursive: true });
    spawnMock.mockReset();
    vi.resetModules();
    jobs = await import('@/lib/jobs');
  });

  afterEach(() => {
    fs.rmSync(hoisted.tmp, { recursive: true, force: true });
  });

  it('saveState + loadState round-trip', () => {
    const state: import('@/lib/jobs').JobState = {
      kind: 'daily',
      pid: 1234,
      log_path: '/tmp/x.log',
      started_at: '2026-01-01T00:00:00Z',
      ended_at: null,
      returncode: null,
      args: ['python', 'run_daily.py'],
    };
    jobs.saveState(state);
    expect(jobs.loadState()).toEqual(state);
  });

  it('saveState(null) removes the state file', () => {
    jobs.saveState({
      kind: 'dry_run',
      pid: 1,
      log_path: '/a',
      started_at: 't',
      ended_at: null,
      returncode: null,
      args: [],
    });
    expect(jobs.loadState()).not.toBeNull();
    jobs.saveState(null);
    expect(jobs.loadState()).toBeNull();
  });

  it('loadState returns null when file is corrupt', () => {
    fs.writeFileSync(path.join(hoisted.tmp, 'logs', 'current_job.json'), 'not json{{{');
    expect(jobs.loadState()).toBeNull();
  });

  it('refreshState marks dead pid as ended with rc=-1', () => {
    // PID 99999999 is almost certainly dead
    jobs.saveState({
      kind: 'daily',
      pid: 99_999_999,
      log_path: '/a',
      started_at: '2026-01-01T00:00:00Z',
      ended_at: null,
      returncode: null,
      args: [],
    });
    const s = jobs.refreshState();
    expect(s?.ended_at).not.toBeNull();
    expect(s?.returncode).toBe(-1);
  });

  it('refreshState leaves a live pid running', () => {
    // The current test process is alive
    jobs.saveState({
      kind: 'daily',
      pid: process.pid,
      log_path: '/a',
      started_at: '2026-01-01T00:00:00Z',
      ended_at: null,
      returncode: null,
      args: [],
    });
    const s = jobs.refreshState();
    expect(s?.ended_at).toBeNull();
    expect(s?.returncode).toBeNull();
  });

  it('isRunning reflects state', () => {
    expect(jobs.isRunning()).toBe(false);
    jobs.saveState({
      kind: 'daily',
      pid: process.pid,
      log_path: '/a',
      started_at: 't',
      ended_at: null,
      returncode: null,
      args: [],
    });
    expect(jobs.isRunning()).toBe(true);
  });

  it('cancel() returns false when nothing is running', () => {
    expect(jobs.cancel()).toBe(false);
  });

  it('launch throws if another job is running', () => {
    jobs.saveState({
      kind: 'daily',
      pid: process.pid,
      log_path: '/a',
      started_at: 't',
      ended_at: null,
      returncode: null,
      args: [],
    });
    expect(() => jobs.launch('daily')).toThrow(/already running/);
  });

  it('launch throws with a helpful message if the venv python is missing', () => {
    // Mock VENV_PYTHON getter returns /usr/bin/true which DOES exist.
    // Override by stubbing fs.existsSync briefly.
    const realExists = fs.existsSync;
    vi.spyOn(fs, 'existsSync').mockImplementation((p) =>
      String(p).endsWith('true') ? false : realExists(p),
    );
    expect(() => jobs.launch('daily')).toThrow(/venv missing/);
    vi.restoreAllMocks();
  });

  it('launch spawns with expected argv and writes state', () => {
    const fakeChild: Partial<import('node:child_process').ChildProcess> = {
      pid: 4242,
      unref: vi.fn(),
      on: vi.fn(),
    };
    spawnMock.mockReturnValue(fakeChild);

    const state = jobs.launch('daily');
    expect(state.pid).toBe(4242);
    expect(state.kind).toBe('daily');
    expect(state.args[0]).toBe('/usr/bin/true');
    expect(state.args[1]).toMatch(/scripts\/run_daily\.py$/);
    expect(state.log_path.startsWith(path.join(hoisted.tmp, 'logs', 'web-daily-'))).toBe(true);

    const call = spawnMock.mock.calls[0];
    expect(call[0]).toBe('/usr/bin/true');
    expect(call[2].detached).toBe(true);
    expect(call[2].cwd).toBe(hoisted.tmp);

    // State file persisted
    const persisted = jobs.loadState();
    expect(persisted?.pid).toBe(4242);
  });

  it('launch forwards extra args (dry_run variant)', () => {
    spawnMock.mockReturnValue({ pid: 7, unref: vi.fn(), on: vi.fn() });
    const state = jobs.launch('dry_run');
    expect(state.args.at(-1)).toBe('--dry-run');
  });

  it('tailLog returns empty string when file missing', () => {
    expect(jobs.tailLog('/nonexistent/path')).toBe('');
  });

  it('tailLog returns last maxBytes of file contents', () => {
    const p = path.join(hoisted.tmp, 'logs', 'big.log');
    fs.writeFileSync(p, 'A'.repeat(100) + 'B'.repeat(50));
    const out = jobs.tailLog(p, 50);
    expect(out).toHaveLength(50);
    expect(out).toBe('B'.repeat(50));
  });

  it('clearState is idempotent', () => {
    jobs.clearState();
    jobs.saveState({
      kind: 'daily',
      pid: 1,
      log_path: '/x',
      started_at: 't',
      ended_at: null,
      returncode: null,
      args: [],
    });
    jobs.clearState();
    expect(jobs.loadState()).toBeNull();
    jobs.clearState();
  });
});
