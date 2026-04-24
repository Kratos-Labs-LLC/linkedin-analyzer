import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import {
  addCreator,
  flatCreatorsFromYaml,
  readYaml,
  removeCreator,
  setAnchor,
  writeYaml,
} from '@/lib/creators-yaml';

describe('creators-yaml', () => {
  let tmp: string;

  beforeEach(() => {
    tmp = path.join(os.tmpdir(), `creators-${Date.now()}-${Math.random()}.yaml`);
  });

  afterEach(() => {
    if (fs.existsSync(tmp)) fs.unlinkSync(tmp);
  });

  it('round-trips a minimal file', () => {
    writeYaml(
      {
        anchors: [{ url: 'https://linkedin.com/in/a', name: 'A' }],
        standard: [{ url: 'https://linkedin.com/in/b', name: 'B' }],
      },
      tmp,
    );
    const read = readYaml(tmp);
    expect(read.anchors).toHaveLength(1);
    expect(read.standard).toHaveLength(1);
    expect(read.anchors[0].name).toBe('A');
  });

  it('preserves header comment on write', () => {
    writeYaml({ anchors: [], standard: [] }, tmp);
    const raw = fs.readFileSync(tmp, 'utf8');
    expect(raw.startsWith('# Curated LinkedIn creators.')).toBe(true);
  });

  it('addCreator writes to the requested bucket', () => {
    writeYaml({ anchors: [], standard: [] }, tmp);
    addCreator({ url: 'https://linkedin.com/in/x', name: 'X', anchor: true }, tmp);
    const read = readYaml(tmp);
    expect(read.anchors.map((e) => e.url)).toContain('https://linkedin.com/in/x');
    expect(read.standard).toHaveLength(0);
  });

  it('addCreator replaces existing entry with same url', () => {
    writeYaml({ anchors: [], standard: [] }, tmp);
    addCreator({ url: 'https://linkedin.com/in/x', name: 'Old', anchor: false }, tmp);
    addCreator({ url: 'https://linkedin.com/in/x', name: 'New', anchor: false }, tmp);
    const read = readYaml(tmp);
    expect(read.standard).toHaveLength(1);
    expect(read.standard[0].name).toBe('New');
  });

  it('setAnchor moves between buckets', () => {
    writeYaml({ anchors: [], standard: [{ url: 'u', name: 'U' }] }, tmp);
    expect(setAnchor('u', true, tmp)).toBe(true);
    let r = readYaml(tmp);
    expect(r.anchors.map((e) => e.url)).toContain('u');
    expect(r.standard).toHaveLength(0);
    expect(setAnchor('u', false, tmp)).toBe(true);
    r = readYaml(tmp);
    expect(r.standard.map((e) => e.url)).toContain('u');
    expect(r.anchors).toHaveLength(0);
  });

  it('setAnchor returns false when URL not found in source bucket', () => {
    writeYaml({ anchors: [], standard: [] }, tmp);
    expect(setAnchor('missing', true, tmp)).toBe(false);
  });

  it('removeCreator returns true only on actual removal', () => {
    writeYaml({ anchors: [{ url: 'u', name: 'U' }], standard: [] }, tmp);
    expect(removeCreator('u', tmp)).toBe(true);
    expect(removeCreator('u', tmp)).toBe(false);
  });

  it('flatCreatorsFromYaml applies correct weights', () => {
    writeYaml(
      {
        anchors: [{ url: 'a', name: 'A' }],
        standard: [{ url: 'b', name: 'B' }],
      },
      tmp,
    );
    const flat = flatCreatorsFromYaml(tmp);
    expect(flat.find((c) => c.url === 'a')?.weight).toBe(1.5);
    expect(flat.find((c) => c.url === 'b')?.weight).toBe(1.0);
  });
});
