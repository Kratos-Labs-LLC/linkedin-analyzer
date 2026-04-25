import 'server-only';

/**
 * Tiny Anthropic Messages API client. Raw fetch — avoids the SDK and its
 * bundling quirks under Next.js. Used for the dashboard draft panel.
 */

const ANTHROPIC_URL = 'https://api.anthropic.com/v1/messages';
const ANTHROPIC_VERSION = '2023-06-01';

type SystemBlock = {
  type: 'text';
  text: string;
  cache_control?: { type: 'ephemeral' };
};

type Message = { role: 'user' | 'assistant'; content: string };

type CallOpts = {
  model: string;
  maxTokens: number;
  system?: SystemBlock[] | string;
  messages: Message[];
  temperature?: number;
};

export async function callAnthropic(opts: CallOpts): Promise<string> {
  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) throw new Error('ANTHROPIC_API_KEY not set in .env');

  const body = {
    model: opts.model,
    max_tokens: opts.maxTokens,
    system: opts.system,
    messages: opts.messages,
    temperature: opts.temperature ?? 0.7,
  };
  const res = await fetch(ANTHROPIC_URL, {
    method: 'POST',
    headers: {
      'x-api-key': apiKey,
      'anthropic-version': ANTHROPIC_VERSION,
      'content-type': 'application/json',
    },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(60_000),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Anthropic API ${res.status}: ${text.slice(0, 300)}`);
  }
  const data = (await res.json()) as { content?: Array<{ type: string; text?: string }> };
  return (data.content || [])
    .filter((b) => b.type === 'text' && typeof b.text === 'string')
    .map((b) => b.text as string)
    .join('')
    .trim();
}

// --- High-level helpers used by /api/draft -------------------------------

const DRAFT_MODEL = 'claude-sonnet-4-5';
const JUDGE_MODEL = 'claude-opus-4-7';

export async function draftWithSkill(skillContent: string, prompt: string): Promise<string> {
  return callAnthropic({
    model: DRAFT_MODEL,
    maxTokens: 1_200,
    temperature: 0.3,
    system: [
      {
        type: 'text',
        text: skillContent,
        cache_control: { type: 'ephemeral' },
      },
    ],
    messages: [
      {
        role: 'user',
        content: `${prompt}\n\nReturn only the post text. No preamble, no markdown fences.`,
      },
    ],
  });
}

const JUDGE_SYSTEM = (
  'You are evaluating two LinkedIn posts (A and B) against an empirical ' +
  'engagement profile. Decide which post better matches the high-engagement ' +
  'patterns. Respond with JSON only: ' +
  '{"winner": "A" | "B" | "tie", "reasoning": "1-2 sentences citing specific ' +
  'features from the profile"}.'
);

export type Verdict = { winner: 'A' | 'B' | 'tie'; reasoning: string };

export async function judgePair(args: {
  prompt: string;
  draftA: string;
  draftB: string;
  statsSummary: string;
}): Promise<Verdict> {
  const userMsg =
    `<engagement_profile>\n${args.statsSummary}\n</engagement_profile>\n\n` +
    `<prompt>${args.prompt}</prompt>\n\n` +
    `<post_a>\n${args.draftA}\n</post_a>\n\n` +
    `<post_b>\n${args.draftB}\n</post_b>\n\n` +
    'Return JSON only.';
  const raw = await callAnthropic({
    model: JUDGE_MODEL,
    maxTokens: 600,
    temperature: 0,
    system: [{ type: 'text', text: JUDGE_SYSTEM }],
    messages: [{ role: 'user', content: userMsg }],
  });
  return parseVerdict(raw);
}

export function parseVerdict(raw: string): Verdict {
  let text = raw.trim();
  if (text.startsWith('```')) {
    text = text.replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/i, '');
  }
  try {
    const data = JSON.parse(text) as { winner?: unknown; reasoning?: unknown };
    const winner =
      data.winner === 'A' || data.winner === 'B' || data.winner === 'tie'
        ? (data.winner as 'A' | 'B' | 'tie')
        : 'tie';
    const reasoning = typeof data.reasoning === 'string' ? data.reasoning : '';
    return { winner, reasoning };
  } catch {
    return { winner: 'tie', reasoning: `unparseable judge output: ${raw.slice(0, 160)}` };
  }
}

// --- Stats summary (mirrors src/analyzer/validator.py:_stats_summary) ----

export function statsSummary(statsJson: string | null): string {
  if (!statsJson) {
    return '(no stats.json — judge on general engagement heuristics)';
  }
  let data: Record<string, unknown>;
  try {
    data = JSON.parse(statsJson);
  } catch {
    return '(stats.json could not be parsed)';
  }

  const summary: Record<string, unknown> = {};
  const cat = (data.categorical_features as Record<string, unknown>) || {};
  const topPer: Record<string, string> = {};
  for (const [feature, raw] of Object.entries(cat)) {
    if (!raw || typeof raw !== 'object') continue;
    const buckets = raw as Record<string, { rank?: number }>;
    const ranked = Object.entries(buckets)
      .filter(([, v]) => typeof v === 'object' && v !== null)
      .sort(([, a], [, b]) => (a.rank ?? 999) - (b.rank ?? 999));
    if (ranked.length) topPer[feature] = ranked[0][0];
  }
  summary.top_per_categorical = topPer;

  const num = (data.numeric_features as Record<string, unknown>) || {};
  summary.numeric_signals = Object.fromEntries(
    Object.entries(num)
      .filter(([, v]) => typeof v === 'object' && v !== null)
      .map(([k, v]) => {
        const obj = v as { correlation?: unknown; top_quartile_mean?: unknown; bottom_quartile_mean?: unknown };
        return [
          k,
          {
            correlation_with_engagement: obj.correlation ?? null,
            top_quartile_mean: obj.top_quartile_mean ?? null,
            bottom_quartile_mean: obj.bottom_quartile_mean ?? null,
          },
        ];
      }),
  );
  summary.proof_elements_frequency = data.proof_elements_frequency ?? {};
  summary.n_posts_analyzed = data.n_posts_analyzed ?? null;
  return JSON.stringify(summary, null, 2);
}
