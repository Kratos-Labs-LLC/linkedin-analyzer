import 'server-only';

export type TelegramResult = { ok: boolean; detail: string };

export function telegramConfigured(): boolean {
  return !!(process.env.TELEGRAM_BOT_TOKEN && process.env.TELEGRAM_CHAT_ID);
}

export async function sendTelegram(text: string): Promise<TelegramResult> {
  const token = process.env.TELEGRAM_BOT_TOKEN;
  const chatId = process.env.TELEGRAM_CHAT_ID;
  if (!token || !chatId) {
    return { ok: false, detail: 'Telegram not configured (set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID).' };
  }

  try {
    const resp = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: chatId, text }),
      signal: AbortSignal.timeout(10_000),
    });
    if (!resp.ok) {
      const snippet = (await resp.text()).slice(0, 200);
      return { ok: false, detail: `HTTP ${resp.status}: ${snippet}` };
    }
    return { ok: true, detail: 'delivered' };
  } catch (e) {
    return { ok: false, detail: `request failed: ${e instanceof Error ? e.message : String(e)}` };
  }
}

export async function sendTestAlert(): Promise<TelegramResult> {
  return sendTelegram(
    `LinkedIn Analyzer test ping.\nIf you're reading this, alerts are wired up correctly.`,
  );
}
