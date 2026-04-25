/**
 * Token bucket rate limiter — bounds spend on Anthropic-backed routes.
 *
 * Persists token state to disk so a process restart doesn't refill the bucket.
 * Survives HMR (via globalThis cache) AND survives `npm run dev` restarts
 * (via the on-disk JSON file at LOGS_DIR/rate_limit.json).
 *
 * In-flight count is intentionally NOT persisted: a process restart by
 * definition has zero requests in flight.
 *
 * Two limits enforced together:
 *   - in-flight: at most `maxInFlight` simultaneous calls.
 *   - rate: at most `capacity` calls per `windowMs`. Refills
 *     `capacity / windowMs` tokens per ms continuously.
 */
import fs from 'node:fs';
import path from 'node:path';

import { LOGS_DIR } from './paths';

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

type PersistedBucket = {
  tokens: number;
  lastRefillAt: number;
  capacity: number;
  windowMs: number;
};

declare global {
  // eslint-disable-next-line no-var
  var __linkedinAnalyzerRateBuckets: Map<string, Bucket> | undefined;
  // eslint-disable-next-line no-var
  var __linkedinAnalyzerRateLimitPath: string | undefined;
}

function statePath(): string {
  return globalThis.__linkedinAnalyzerRateLimitPath ?? path.join(LOGS_DIR, 'rate_limit.json');
}

function loadFromDisk(): Record<string, PersistedBucket> {
  try {
    const raw = fs.readFileSync(statePath(), 'utf8');
    return JSON.parse(raw) as Record<string, PersistedBucket>;
  } catch {
    return {};
  }
}

function saveToDisk(map: Map<string, Bucket>) {
  const payload: Record<string, PersistedBucket> = {};
  for (const [k, b] of map) {
    payload[k] = {
      tokens: b.tokens,
      lastRefillAt: b.lastRefillAt,
      capacity: b.capacity,
      windowMs: b.capacity / b.refillPerMs,
    };
  }
  const file = statePath();
  fs.mkdirSync(path.dirname(file), { recursive: true });
  // Atomic write: temp + rename. A crash mid-write leaves the previous
  // file intact rather than a half-written one.
  const tmp = `${file}.${process.pid}.${Date.now()}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(payload));
  fs.renameSync(tmp, file);
}

function getBucket(
  key: string,
  config: { capacity: number; windowMs: number; maxInFlight: number; now: number },
): { bucket: Bucket; map: Map<string, Bucket> } {
  const map =
    globalThis.__linkedinAnalyzerRateBuckets ||
    (globalThis.__linkedinAnalyzerRateBuckets = new Map());

  // First lookup of this key in this process: try to hydrate from disk.
  if (!map.has(key)) {
    const persisted = loadFromDisk()[key];
    const refillPerMs = config.capacity / config.windowMs;
    if (persisted && persisted.capacity === config.capacity) {
      map.set(key, {
        tokens: persisted.tokens,
        capacity: config.capacity,
        refillPerMs,
        lastRefillAt: persisted.lastRefillAt,
        inFlight: 0, // never persisted — a fresh process has 0 in flight
        maxInFlight: config.maxInFlight,
      });
    } else {
      map.set(key, {
        tokens: config.capacity,
        capacity: config.capacity,
        refillPerMs,
        lastRefillAt: config.now,
        inFlight: 0,
        maxInFlight: config.maxInFlight,
      });
    }
  }
  return { bucket: map.get(key)!, map };
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
  /** Skip disk persistence — used by unit tests. */
  noPersist?: boolean;
}): RateLimitDecision {
  const now = (opts.now ?? Date.now)();
  const { bucket: b, map } = getBucket(opts.key, { ...opts, now });
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
  if (!opts.noPersist) {
    try {
      saveToDisk(map);
    } catch {
      // Persistence failures don't block the request — they just mean a
      // restart resets to full bucket. Log nothing; this is a personal tool.
    }
  }

  let released = false;
  return {
    allowed: true,
    release: () => {
      if (released) return;
      released = true;
      b.inFlight = Math.max(0, b.inFlight - 1);
      // No disk write on release — inFlight isn't persisted anyway.
    },
  };
}

// Test-only: clear all buckets and remove the persisted file.
export function _resetForTesting(opts?: { customStatePath?: string }) {
  globalThis.__linkedinAnalyzerRateBuckets = undefined;
  if (opts?.customStatePath !== undefined) {
    globalThis.__linkedinAnalyzerRateLimitPath = opts.customStatePath;
  }
  try {
    fs.unlinkSync(statePath());
  } catch {
    // already gone
  }
}
