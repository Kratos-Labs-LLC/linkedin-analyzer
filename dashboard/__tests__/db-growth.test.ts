import { describe, expect, it } from 'vitest';

import {
  computeGrowthRatePerWeek,
  parseSqliteTimestamp,
  type ProfileSnapshotRow,
} from '@/lib/db';

function snap(daysFromBase: number, follower_count: number | null): ProfileSnapshotRow {
  const base = new Date('2026-04-01T00:00:00Z').getTime();
  return {
    id: daysFromBase + 1,
    creator_id: 1,
    snapshot_at: new Date(base + daysFromBase * 86_400_000).toISOString(),
    follower_count,
    headline: null,
    about_text: null,
    current_role: null,
    current_company: null,
    location: null,
    has_profile_photo: null,
    has_banner: null,
    featured_count: null,
  };
}

describe('computeGrowthRatePerWeek', () => {
  it('returns ~100/week for a +100 in 7 days linear trend', () => {
    const rate = computeGrowthRatePerWeek([snap(0, 1000), snap(7, 1100)]);
    expect(rate).not.toBeNull();
    expect(Math.abs((rate as number) - 100)).toBeLessThan(0.5);
  });

  it('returns negative slope when followers shrink', () => {
    const rate = computeGrowthRatePerWeek([snap(0, 1000), snap(7, 950)]);
    expect(rate).not.toBeNull();
    expect(rate as number).toBeLessThan(0);
  });

  it('handles three-point fit', () => {
    const rate = computeGrowthRatePerWeek([
      snap(0, 1000),
      snap(7, 1100),
      snap(14, 1200),
    ]);
    expect(Math.abs((rate as number) - 100)).toBeLessThan(0.5);
  });

  it('returns null for empty input', () => {
    expect(computeGrowthRatePerWeek([])).toBeNull();
  });

  it('returns null for a single snapshot', () => {
    expect(computeGrowthRatePerWeek([snap(0, 1000)])).toBeNull();
  });

  it('drops snapshots with null follower_count', () => {
    const rate = computeGrowthRatePerWeek([
      snap(0, 1000),
      snap(3, null),
      snap(7, 1100),
    ]);
    expect(Math.abs((rate as number) - 100)).toBeLessThan(0.5);
  });

  it('returns null when timestamps collapse', () => {
    const a = snap(0, 1000);
    const b = { ...snap(0, 1100), id: 2 };
    expect(computeGrowthRatePerWeek([a, b])).toBeNull();
  });

  it('matches Python growth rate on SQLite-default timestamps (B1)', () => {
    /**
     * Real-world bug: SQLite's CURRENT_TIMESTAMP emits 'YYYY-MM-DD HH:MM:SS'
     * (space, no Z). new Date() on that string is implementation-defined per
     * ECMA-262 — V8 has historically parsed as local time, Safari as UTC.
     * The Python compute_growth_rate uses datetime.fromisoformat which is
     * deterministic. After the fix, parseSqliteTimestamp normalizes both
     * shapes to a UTC-anchored ISO string before parsing, so the JS result
     * matches Python regardless of the host's timezone.
     */
    const sqliteRows: ProfileSnapshotRow[] = [
      { ...snap(0, 1000), snapshot_at: '2026-04-01 00:00:00' },
      { ...snap(7, 1100), snapshot_at: '2026-04-08 00:00:00' },
    ];
    const isoRows: ProfileSnapshotRow[] = [
      { ...snap(0, 1000), snapshot_at: '2026-04-01T00:00:00Z' },
      { ...snap(7, 1100), snapshot_at: '2026-04-08T00:00:00Z' },
    ];
    const a = computeGrowthRatePerWeek(sqliteRows);
    const b = computeGrowthRatePerWeek(isoRows);
    expect(a).not.toBeNull();
    expect(b).not.toBeNull();
    expect(Math.abs((a as number) - (b as number))).toBeLessThan(0.01);
  });
});

describe('parseSqliteTimestamp', () => {
  it('parses SQLite default format as UTC', () => {
    const t = parseSqliteTimestamp('2026-04-01 00:00:00');
    expect(t).toBe(Date.UTC(2026, 3, 1, 0, 0, 0));
  });

  it('parses ISO with Z marker', () => {
    expect(parseSqliteTimestamp('2026-04-01T00:00:00Z')).toBe(
      Date.UTC(2026, 3, 1, 0, 0, 0),
    );
  });

  it('parses ISO with offset', () => {
    // 2026-04-01T01:00:00+01:00 == 2026-04-01T00:00:00Z
    expect(parseSqliteTimestamp('2026-04-01T01:00:00+01:00')).toBe(
      Date.UTC(2026, 3, 1, 0, 0, 0),
    );
  });

  it('treats naive ISO (no Z, no offset) as UTC', () => {
    expect(parseSqliteTimestamp('2026-04-01T00:00:00')).toBe(
      Date.UTC(2026, 3, 1, 0, 0, 0),
    );
  });
});
