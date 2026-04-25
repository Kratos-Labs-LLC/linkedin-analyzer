import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const ORIGINAL_ENV = { ...process.env };
const ORIGINAL_FETCH = global.fetch;

afterEach(() => {
  process.env = { ...ORIGINAL_ENV };
  global.fetch = ORIGINAL_FETCH;
  vi.resetModules();
});

beforeEach(() => {
  process.env.ANTHROPIC_API_KEY = 'test-key';
});

function fakeContent(text: string) {
  return new Response(JSON.stringify({ content: [{ type: 'text', text }] }), {
    status: 200,
  });
}

describe('callAnthropic', () => {
  it('refuses without ANTHROPIC_API_KEY', async () => {
    delete process.env.ANTHROPIC_API_KEY;
    const { callAnthropic } = await import('@/lib/anthropic');
    await expect(
      callAnthropic({
        model: 'm',
        maxTokens: 10,
        messages: [{ role: 'user', content: 'hi' }],
      }),
    ).rejects.toThrow(/ANTHROPIC_API_KEY/);
  });

  it('POSTs the right body and parses content blocks', async () => {
    const captured: { url?: RequestInfo | URL; init?: RequestInit } = {};
    global.fetch = vi.fn(async (url, init) => {
      captured.url = url;
      captured.init = init;
      return fakeContent('hello world');
    }) as typeof fetch;

    const { callAnthropic } = await import('@/lib/anthropic');
    const out = await callAnthropic({
      model: 'claude-sonnet-4-5',
      maxTokens: 100,
      temperature: 0.4,
      messages: [{ role: 'user', content: 'say hi' }],
    });

    expect(out).toBe('hello world');
    expect(captured.url).toBe('https://api.anthropic.com/v1/messages');
    const headers = captured.init?.headers as Record<string, string>;
    expect(headers['x-api-key']).toBe('test-key');
    expect(headers['anthropic-version']).toBe('2023-06-01');
    const body = JSON.parse(String(captured.init?.body));
    expect(body.model).toBe('claude-sonnet-4-5');
    expect(body.temperature).toBe(0.4);
  });

  it('surfaces non-2xx status with snippet', async () => {
    global.fetch = vi.fn(
      async () => new Response('rate limited', { status: 429 }),
    ) as typeof fetch;
    const { callAnthropic } = await import('@/lib/anthropic');
    await expect(
      callAnthropic({
        model: 'm',
        maxTokens: 10,
        messages: [{ role: 'user', content: 'x' }],
      }),
    ).rejects.toThrow(/429/);
  });

  it('joins multiple text blocks and ignores non-text', async () => {
    global.fetch = vi.fn(async () =>
      new Response(
        JSON.stringify({
          content: [
            { type: 'text', text: 'a' },
            { type: 'tool_use', input: {} },
            { type: 'text', text: 'b' },
          ],
        }),
        { status: 200 },
      ),
    ) as typeof fetch;
    const { callAnthropic } = await import('@/lib/anthropic');
    const out = await callAnthropic({
      model: 'm',
      maxTokens: 10,
      messages: [{ role: 'user', content: 'x' }],
    });
    expect(out).toBe('ab');
  });
});

describe('parseVerdict', () => {
  it('parses valid JSON', async () => {
    const { parseVerdict } = await import('@/lib/anthropic');
    const v = parseVerdict('{"winner": "A", "reasoning": "ok"}');
    expect(v.winner).toBe('A');
    expect(v.reasoning).toBe('ok');
  });

  it('strips ```json fences', async () => {
    const { parseVerdict } = await import('@/lib/anthropic');
    const v = parseVerdict('```json\n{"winner": "B", "reasoning": ""}\n```');
    expect(v.winner).toBe('B');
  });

  it('falls back to tie on bad winner', async () => {
    const { parseVerdict } = await import('@/lib/anthropic');
    expect(parseVerdict('{"winner":"Z"}').winner).toBe('tie');
  });

  it('falls back to tie on unparseable input', async () => {
    const { parseVerdict } = await import('@/lib/anthropic');
    const v = parseVerdict('not json');
    expect(v.winner).toBe('tie');
    expect(v.reasoning).toMatch(/unparseable/);
  });
});

describe('statsSummary', () => {
  it('handles missing stats', async () => {
    const { statsSummary } = await import('@/lib/anthropic');
    expect(statsSummary(null)).toMatch(/no stats\.json/);
  });

  it('handles unparseable stats', async () => {
    const { statsSummary } = await import('@/lib/anthropic');
    expect(statsSummary('{not json')).toMatch(/could not be parsed/);
  });

  it('compacts categorical features to top-ranked values', async () => {
    const stats = {
      n_posts_analyzed: 100,
      categorical_features: {
        hook_type: {
          question: { rank: 2, mean_engagement: 0.01 },
          bold_claim: { rank: 1, mean_engagement: 0.02 },
        },
      },
      numeric_features: {
        total_word_count: {
          correlation: -0.2,
          top_quartile_mean: 180,
          bottom_quartile_mean: 320,
        },
      },
    };
    const { statsSummary } = await import('@/lib/anthropic');
    const out = JSON.parse(statsSummary(JSON.stringify(stats)));
    expect(out.top_per_categorical.hook_type).toBe('bold_claim');
    expect(out.numeric_signals.total_word_count.correlation_with_engagement).toBe(-0.2);
    expect(out.n_posts_analyzed).toBe(100);
  });
});

describe('draftWithSkill', () => {
  it('uses Sonnet with cache-controlled skill content as system', async () => {
    let captured: any = null;
    global.fetch = vi.fn(async (url, init) => {
      captured = JSON.parse(String((init as RequestInit).body));
      return fakeContent('drafted post');
    }) as typeof fetch;
    const { draftWithSkill } = await import('@/lib/anthropic');
    const out = await draftWithSkill('SKILL CONTENT', 'topic prompt');
    expect(out).toBe('drafted post');
    expect(captured.model).toMatch(/sonnet/);
    expect(Array.isArray(captured.system)).toBe(true);
    expect(captured.system[0].text).toBe('SKILL CONTENT');
    expect(captured.system[0].cache_control).toEqual({ type: 'ephemeral' });
    expect(String(captured.messages[0].content)).toContain('topic prompt');
  });
});

describe('judgePair', () => {
  it('uses Opus and returns parsed verdict', async () => {
    let captured: any = null;
    global.fetch = vi.fn(async (url, init) => {
      captured = JSON.parse(String((init as RequestInit).body));
      return fakeContent('{"winner": "B", "reasoning": "stronger hook"}');
    }) as typeof fetch;
    const { judgePair } = await import('@/lib/anthropic');
    const v = await judgePair({
      prompt: 'p',
      draftA: 'A draft',
      draftB: 'B draft',
      statsSummary: '{}',
    });
    expect(v.winner).toBe('B');
    expect(v.reasoning).toBe('stronger hook');
    expect(captured.model).toMatch(/opus/);
    expect(captured.temperature).toBe(0);
    const userMsg = captured.messages[0].content;
    expect(userMsg).toContain('A draft');
    expect(userMsg).toContain('B draft');
  });
});
