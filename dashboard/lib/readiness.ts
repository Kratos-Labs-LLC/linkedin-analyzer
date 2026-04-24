import 'server-only';

import { collectionSummary } from './db';

export const MIN_POSTS = 800;
export const MIN_CREATORS_WITH_20 = 15;
export const MIN_DAYS_ELAPSED = 30;

export type ReadinessGate = {
  label: string;
  actual: string;
  ok: boolean;
};

export function computeReadiness(): ReadinessGate[] {
  const s = collectionSummary();
  let days: number | null = null;
  if (s.first_collected_at) {
    const first = new Date(s.first_collected_at.replace(/Z$/, 'Z'));
    if (!Number.isNaN(first.getTime())) {
      days = Math.floor((Date.now() - first.getTime()) / (1000 * 60 * 60 * 24));
    }
  }

  return [
    {
      label: `≥${MIN_DAYS_ELAPSED} days of collection`,
      actual: `${days ?? 0} days`,
      ok: days !== null && days >= MIN_DAYS_ELAPSED,
    },
    {
      label: `≥${MIN_POSTS} posts`,
      actual: `${s.total_posts} posts`,
      ok: s.total_posts >= MIN_POSTS,
    },
    {
      label: `≥${MIN_CREATORS_WITH_20} creators with ≥20 posts`,
      actual: `${s.creators_with_20_posts} creators`,
      ok: s.creators_with_20_posts >= MIN_CREATORS_WITH_20,
    },
  ];
}
