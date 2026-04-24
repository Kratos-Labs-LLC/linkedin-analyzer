import { describe, expect, it } from 'vitest';

import { computeEngagementScore } from '@/lib/db';

describe('computeEngagementScore', () => {
  it('matches the Python formula (r + 5c + 10s) / followers', () => {
    // 100 reactions, 10 comments (*5=50), 2 reshares (*10=20) → 170 / 10000 = 0.017
    expect(computeEngagementScore(100, 10, 2, 10_000)).toBeCloseTo(0.017, 6);
  });

  it('returns null when follower count is zero or missing', () => {
    expect(computeEngagementScore(100, 10, 0, 0)).toBeNull();
    expect(computeEngagementScore(100, 10, 0, null)).toBeNull();
  });

  it('returns 0 for a dead post', () => {
    expect(computeEngagementScore(0, 0, 0, 1_000)).toBe(0);
  });
});
