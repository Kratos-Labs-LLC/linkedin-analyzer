import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { _resetForTesting, take } from '@/lib/rate-limit';

beforeEach(() => _resetForTesting());
afterEach(() => _resetForTesting());

const KEY = 'test';

describe('rate limit token bucket', () => {
  it('first request is allowed', () => {
    const r = take({ key: KEY, capacity: 5, windowMs: 60_000, maxInFlight: 1 });
    expect(r.allowed).toBe(true);
    if (r.allowed) r.release();
  });

  it('rejects when in-flight cap is hit', () => {
    const a = take({ key: KEY, capacity: 5, windowMs: 60_000, maxInFlight: 1 });
    const b = take({ key: KEY, capacity: 5, windowMs: 60_000, maxInFlight: 1 });
    expect(a.allowed).toBe(true);
    expect(b.allowed).toBe(false);
    if (!b.allowed) {
      expect(b.reason).toMatch(/in flight/i);
      expect(b.retryAfterSeconds).toBeGreaterThanOrEqual(1);
    }
    if (a.allowed) a.release();
  });

  it('release lets the next request through', () => {
    const a = take({ key: KEY, capacity: 5, windowMs: 60_000, maxInFlight: 1 });
    expect(a.allowed).toBe(true);
    if (a.allowed) a.release();
    const b = take({ key: KEY, capacity: 5, windowMs: 60_000, maxInFlight: 1 });
    expect(b.allowed).toBe(true);
    if (b.allowed) b.release();
  });

  it('rejects when token bucket empty', () => {
    let now = 0;
    const opts = {
      key: KEY,
      capacity: 3,
      windowMs: 60_000,
      maxInFlight: 10,
      now: () => now,
    };
    for (let i = 0; i < 3; i++) {
      const r = take(opts);
      expect(r.allowed).toBe(true);
      if (r.allowed) r.release();
    }
    const fourth = take(opts);
    expect(fourth.allowed).toBe(false);
    if (!fourth.allowed) {
      expect(fourth.reason).toMatch(/rate limit/i);
    }
  });

  it('refills tokens over time', () => {
    let now = 0;
    const opts = {
      key: KEY,
      capacity: 3,
      windowMs: 60_000,
      maxInFlight: 10,
      now: () => now,
    };
    for (let i = 0; i < 3; i++) {
      const r = take(opts);
      if (r.allowed) r.release();
    }
    expect(take(opts).allowed).toBe(false);

    // Advance 30s — refills 1.5 tokens, enough for one more request.
    now = 30_000;
    const r = take(opts);
    expect(r.allowed).toBe(true);
    if (r.allowed) r.release();
  });

  it('separate keys do not share buckets', () => {
    const opts = (k: string) => ({
      key: k,
      capacity: 1,
      windowMs: 60_000,
      maxInFlight: 1,
    });
    const a = take(opts('A'));
    const b = take(opts('B'));
    expect(a.allowed).toBe(true);
    expect(b.allowed).toBe(true);
    if (a.allowed) a.release();
    if (b.allowed) b.release();
  });

  it('release is idempotent', () => {
    const r = take({ key: KEY, capacity: 5, windowMs: 60_000, maxInFlight: 1 });
    expect(r.allowed).toBe(true);
    if (r.allowed) {
      r.release();
      r.release(); // double-release shouldn't free another slot
    }
    const a = take({ key: KEY, capacity: 5, windowMs: 60_000, maxInFlight: 1 });
    const b = take({ key: KEY, capacity: 5, windowMs: 60_000, maxInFlight: 1 });
    expect(a.allowed).toBe(true);
    expect(b.allowed).toBe(false); // would be true if double-release leaked
    if (a.allowed) a.release();
  });
});
