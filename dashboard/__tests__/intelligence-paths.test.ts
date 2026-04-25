import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const hoisted = vi.hoisted(() => ({
  tmpDir: '',
}));

vi.mock('@/lib/paths', () => ({
  get REPO_ROOT() {
    return hoisted.tmpDir;
  },
  get DB_PATH() {
    return path.join(hoisted.tmpDir, 'db', 'analyzer.db');
  },
  get OUTPUT_DIR() {
    return path.join(hoisted.tmpDir, 'output');
  },
  get LOGS_DIR() {
    return path.join(hoisted.tmpDir, 'logs');
  },
  get CREATORS_YAML_PATH() {
    return path.join(hoisted.tmpDir, 'creators.yaml');
  },
  get CHROME_PROFILE_DIR() {
    return path.join(hoisted.tmpDir, 'chrome_profile');
  },
  get VENV_PYTHON() {
    return '/usr/bin/true';
  },
  get AUTH_SENTINEL() {
    return path.join(hoisted.tmpDir, 'chrome_profile', '.authed');
  },
  get NEW_SKILL_PATH() {
    return path.join(hoisted.tmpDir, 'output', 'new', 'SKILL.md');
  },
  get PROFILE_SKILL_PATH() {
    return path.join(hoisted.tmpDir, 'output', 'profile', 'SKILL.md');
  },
  get PROFILE_AUDIT_PATH() {
    return path.join(hoisted.tmpDir, 'output', 'profile_audit.md');
  },
  ANCHOR_WEIGHT: 1.5,
  STANDARD_WEIGHT: 1.0,
  leadmagnetSkillPath: () => null,
}));

const REAL_SCHEMA = fs.readFileSync(
  path.resolve(__dirname, '..', '..', 'db', 'schema.sql'),
  'utf8',
);

beforeEach(() => {
  hoisted.tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'intel-paths-'));
  fs.mkdirSync(path.join(hoisted.tmpDir, 'db'), { recursive: true });
  fs.writeFileSync(path.join(hoisted.tmpDir, 'db', 'schema.sql'), REAL_SCHEMA);
  fs.mkdirSync(path.join(hoisted.tmpDir, 'output', 'intelligence'), { recursive: true });
});

afterEach(() => {
  fs.rmSync(hoisted.tmpDir, { recursive: true, force: true });
  vi.resetModules();
});

describe('creatorSlug', () => {
  it('produces a stable hyphenated slug with creator id', async () => {
    const { creatorSlug } = await import('@/lib/db');
    expect(creatorSlug({ display_name: 'Alice Smith', id: 7 })).toBe('alice-smith-7');
  });

  it('collapses runs of special chars to single hyphens', async () => {
    const { creatorSlug } = await import('@/lib/db');
    expect(creatorSlug({ display_name: 'Alice  Smith!!', id: 7 })).toBe('alice-smith-7');
  });

  it('falls back to creator-<id> when display_name is empty or null', async () => {
    const { creatorSlug } = await import('@/lib/db');
    expect(creatorSlug({ display_name: null, id: 12 })).toBe('creator-12');
    expect(creatorSlug({ display_name: '', id: 12 })).toBe('creator-12');
    expect(creatorSlug({ display_name: '!!!', id: 12 })).toBe('creator-12');
  });

  it('matches the Python implementation on the same inputs', async () => {
    /**
     * The Python and TS implementations must agree because both sides
     * resolve the same on-disk filename. If this test diverges from
     * src/analyzer/intelligence_runner.py:creator_slug, the dashboard
     * will fail to find files the runner just wrote.
     */
    const { creatorSlug } = await import('@/lib/db');
    expect(creatorSlug({ display_name: 'Dugg Howser', id: 3 })).toBe('dugg-howser-3');
    expect(creatorSlug({ display_name: 'O\'Brien', id: 3 })).toBe('o-brien-3');
  });
});

describe('readIntelligenceArtifacts', () => {
  it('returns nulls when neither file exists', async () => {
    const { readIntelligenceArtifacts } = await import('@/lib/db');
    const out = readIntelligenceArtifacts({ id: 99, display_name: 'Ghost' });
    expect(out.markdown).toBeNull();
    expect(out.packJson).toBeNull();
  });

  it('returns markdown content when the file exists', async () => {
    fs.writeFileSync(
      path.join(hoisted.tmpDir, 'output', 'intelligence', 'alice-7.md'),
      '## TL;DR\n\nbody',
    );
    const { readIntelligenceArtifacts } = await import('@/lib/db');
    const out = readIntelligenceArtifacts({ id: 7, display_name: 'Alice' });
    expect(out.markdown).toBe('## TL;DR\n\nbody');
  });

  it('parses pack.json when present', async () => {
    fs.writeFileSync(
      path.join(hoisted.tmpDir, 'output', 'intelligence', 'alice-7.md'),
      '## TL;DR\n\nbody',
    );
    fs.writeFileSync(
      path.join(hoisted.tmpDir, 'output', 'intelligence', 'alice-7.pack.json'),
      JSON.stringify({ creator: { id: 7, display_name: 'Alice' } }),
    );
    const { readIntelligenceArtifacts } = await import('@/lib/db');
    const out = readIntelligenceArtifacts({ id: 7, display_name: 'Alice' });
    expect(out.packJson).toEqual({ creator: { id: 7, display_name: 'Alice' } });
  });

  it('returns null packJson when the file is invalid JSON', async () => {
    fs.writeFileSync(
      path.join(hoisted.tmpDir, 'output', 'intelligence', 'alice-7.md'),
      '## TL;DR',
    );
    fs.writeFileSync(
      path.join(hoisted.tmpDir, 'output', 'intelligence', 'alice-7.pack.json'),
      'not-json{',
    );
    const { readIntelligenceArtifacts } = await import('@/lib/db');
    const out = readIntelligenceArtifacts({ id: 7, display_name: 'Alice' });
    expect(out.markdown).toBe('## TL;DR');
    expect(out.packJson).toBeNull();
  });
});
