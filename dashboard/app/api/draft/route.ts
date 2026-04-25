import fs from 'node:fs';
import path from 'node:path';
import { NextResponse } from 'next/server';

import {
  draftWithSkill,
  judgePair,
  statsSummary,
  type Verdict,
} from '@/lib/anthropic';
import { NEW_SKILL_PATH, OUTPUT_DIR, leadmagnetSkillPath } from '@/lib/paths';
import { take } from '@/lib/rate-limit';

export const dynamic = 'force-dynamic';

// Each /api/draft hit is ~$0.10-0.15 of Anthropic spend (3 calls). Cap at
// 6 calls per minute and at most 1 in flight — bounds a stuck UI auto-refresh
// or runaway script to ~$1/hour worst case. Process-local; resets on restart.
const RATE_CAPACITY = 6;
const RATE_WINDOW_MS = 60_000;
const RATE_MAX_IN_FLIGHT = 1;

type DraftResponse = {
  newDraft: string;
  oldDraft: string;
  winner: 'new' | 'old' | 'tie';
  reasoning: string;
};

export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: 'invalid JSON body' }, { status: 400 });
  }
  const prompt = String((body as { prompt?: unknown })?.prompt ?? '').trim();
  if (!prompt) {
    return NextResponse.json({ error: 'prompt required' }, { status: 400 });
  }
  if (prompt.length > 2_000) {
    return NextResponse.json({ error: 'prompt too long (max 2000 chars)' }, { status: 400 });
  }

  const decision = take({
    key: 'api/draft',
    capacity: RATE_CAPACITY,
    windowMs: RATE_WINDOW_MS,
    maxInFlight: RATE_MAX_IN_FLIGHT,
  });
  if (!decision.allowed) {
    return NextResponse.json(
      { error: `rate limited: ${decision.reason}` },
      {
        status: 429,
        headers: { 'retry-after': String(decision.retryAfterSeconds) },
      },
    );
  }

  const oldPath = leadmagnetSkillPath();
  if (!oldPath) {
    return NextResponse.json(
      {
        error:
          'LEADMAGNET_SKILL_PATH not set in .env. Add it pointing at your leadmagnet-post-writer/SKILL.md and restart.',
      },
      { status: 400 },
    );
  }
  if (!fs.existsSync(oldPath)) {
    return NextResponse.json(
      { error: `LEADMAGNET_SKILL_PATH points at a non-existent file: ${oldPath}` },
      { status: 400 },
    );
  }
  if (!fs.existsSync(NEW_SKILL_PATH)) {
    return NextResponse.json(
      {
        error:
          'New skill not generated yet — run the analysis pipeline first (Analysis → Run analysis).',
      },
      { status: 400 },
    );
  }

  const newSkill = fs.readFileSync(NEW_SKILL_PATH, 'utf8');
  const oldSkill = fs.readFileSync(oldPath, 'utf8');
  const statsPath = path.join(OUTPUT_DIR, 'stats.json');
  const summary = statsSummary(fs.existsSync(statsPath) ? fs.readFileSync(statsPath, 'utf8') : null);

  try {
    const [newDraft, oldDraft] = await Promise.all([
      draftWithSkill(newSkill, prompt),
      draftWithSkill(oldSkill, prompt),
    ]);

    // Randomize A/B for the judge so positional bias doesn't favor either skill.
    const newIsA = Math.random() < 0.5;
    const verdict: Verdict = await judgePair({
      prompt,
      draftA: newIsA ? newDraft : oldDraft,
      draftB: newIsA ? oldDraft : newDraft,
      statsSummary: summary,
    });

    let winner: 'new' | 'old' | 'tie';
    if (verdict.winner === 'tie') winner = 'tie';
    else if ((verdict.winner === 'A' && newIsA) || (verdict.winner === 'B' && !newIsA))
      winner = 'new';
    else winner = 'old';

    const payload: DraftResponse = {
      newDraft,
      oldDraft,
      winner,
      reasoning: verdict.reasoning,
    };
    return NextResponse.json(payload);
  } catch (e) {
    return NextResponse.json(
      { error: e instanceof Error ? e.message : String(e) },
      { status: 500 },
    );
  } finally {
    decision.release();
  }
}
