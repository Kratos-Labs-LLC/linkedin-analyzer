import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

describe('telegram', () => {
  const originalEnv = { ...process.env };
  const originalFetch = global.fetch;

  beforeEach(() => {
    process.env = { ...originalEnv };
    delete process.env.TELEGRAM_BOT_TOKEN;
    delete process.env.TELEGRAM_CHAT_ID;
  });

  afterEach(() => {
    process.env = { ...originalEnv };
    global.fetch = originalFetch;
  });

  it('sendTelegram refuses when not configured', async () => {
    vi.resetModules();
    const { sendTelegram } = await import('@/lib/telegram');
    const r = await sendTelegram('hi');
    expect(r.ok).toBe(false);
    expect(r.detail).toMatch(/not configured/);
  });

  it('sendTelegram posts to the bot API with the right body', async () => {
    process.env.TELEGRAM_BOT_TOKEN = 'abc';
    process.env.TELEGRAM_CHAT_ID = '42';
    const calls: { url: RequestInfo | URL; body: unknown }[] = [];
    global.fetch = vi.fn(async (url, init) => {
      calls.push({
        url,
        body: init?.body ? JSON.parse(String(init.body)) : null,
      });
      return new Response('', { status: 200 });
    }) as typeof fetch;
    vi.resetModules();
    const { sendTelegram } = await import('@/lib/telegram');
    const r = await sendTelegram('hello');
    expect(r.ok).toBe(true);
    expect(r.detail).toBe('delivered');
    expect(calls[0].url).toBe('https://api.telegram.org/botabc/sendMessage');
    expect(calls[0].body).toEqual({ chat_id: '42', text: 'hello' });
  });

  it('surfaces non-2xx status in detail', async () => {
    process.env.TELEGRAM_BOT_TOKEN = 'abc';
    process.env.TELEGRAM_CHAT_ID = '42';
    global.fetch = vi.fn(
      async () => new Response('Unauthorized', { status: 401 }),
    ) as typeof fetch;
    vi.resetModules();
    const { sendTelegram } = await import('@/lib/telegram');
    const r = await sendTelegram('hello');
    expect(r.ok).toBe(false);
    expect(r.detail).toMatch(/401/);
  });

  it('telegramConfigured reflects env', async () => {
    process.env.TELEGRAM_BOT_TOKEN = 't';
    process.env.TELEGRAM_CHAT_ID = 'c';
    vi.resetModules();
    const { telegramConfigured } = await import('@/lib/telegram');
    expect(telegramConfigured()).toBe(true);
  });
});
