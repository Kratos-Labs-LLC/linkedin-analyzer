import { describe, expect, it } from 'vitest';

import { computeGrowthRatePerWeek, type ProfileSnapshotRow } from '@/lib/db';

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
});
