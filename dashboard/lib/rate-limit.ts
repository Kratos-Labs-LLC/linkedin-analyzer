/**
 * Process-local token bucket — bounds spend on Anthropic-backed routes.
 *
 * Single-tenant local tool, so we don't bother with per-IP keys; a single
 * shared bucket suffices. The bucket survives HMR via globalThis (otherwise
 * a code edit in dev would refill the bucket, defeating the point).
 *
 * Two limits enforced together:
 *   - in-flight: at most `maxInFlight` simultaneous calls. Stops a stuck
 *     auto-refresh loop from running 50 calls in parallel.
 *   - rate: at most `capacity` calls per `windowMs`. Token bucket refills
 *     `capacity / windowMs` tokens per ms.
 */

export type RateLimitDecision =
  | { allowed: true; release: () => void }
  | { allowed: false; retryAfterSeconds: number; reason: string };

type Bucket = {
  tokens: number;
  capacity: number;
  refillPerMs: number;
  lastRefillAt: number;
  inFlight: number;
  maxInFlight: number;
};

declare global {
  // eslint-disable-next-line no-var
  var __linkedinAnalyzerRateBuckets: Map<string, Bucket> | undefined;
}

function getBucket(
  key: string,
  config: { capacity: number; windowMs: number; maxInFlight: number; now: number },
): Bucket {
  const map =
    globalThis.__linkedinAnalyzerRateBuckets ||
    (globalThis.__linkedinAnalyzerRateBuckets = new Map());
  let b = map.get(key);
  if (!b) {
    b = {
      tokens: config.capacity,
      capacity: config.capacity,
      refillPerMs: config.capacity / config.windowMs,
      // Anchor on the caller's clock so injected-time tests stay deterministic.
      lastRefillAt: config.now,
      inFlight: 0,
      maxInFlight: config.maxInFlight,
    };
    map.set(key, b);
  }
  return b;
}

function refill(b: Bucket, now: number) {
  const elapsed = now - b.lastRefillAt;
  if (elapsed <= 0) return;
  b.tokens = Math.min(b.capacity, b.tokens + elapsed * b.refillPerMs);
  b.lastRefillAt = now;
}

export function take(opts: {
  key: string;
  capacity: number;
  windowMs: number;
  maxInFlight: number;
  now?: () => number;
}): RateLimitDecision {
  const now = (opts.now ?? Date.now)();
  const b = getBucket(opts.key, { ...opts, now });
  refill(b, now);

  if (b.inFlight >= b.maxInFlight) {
    return {
      allowed: false,
      retryAfterSeconds: 1,
      reason: `at most ${b.maxInFlight} request(s) in flight`,
    };
  }
  if (b.tokens < 1) {
    const tokensNeeded = 1 - b.tokens;
    const msUntilToken = Math.ceil(tokensNeeded / b.refillPerMs);
    return {
      allowed: false,
      retryAfterSeconds: Math.max(1, Math.ceil(msUntilToken / 1000)),
      reason: `rate limit ${b.capacity}/window — try again in ${msUntilToken}ms`,
    };
  }

  b.tokens -= 1;
  b.inFlight += 1;
  let released = false;
  return {
    allowed: true,
    release: () => {
      if (released) return;
      released = true;
      b.inFlight = Math.max(0, b.inFlight - 1);
    },
  };
}

// Test-only: clear all buckets between tests.
export function _resetForTesting() {
  globalThis.__linkedinAnalyzerRateBuckets = undefined;
}
