import fs from 'node:fs';
import path from 'node:path';
import { NextResponse } from 'next/server';

import { refreshState, tailLog } from '@/lib/jobs';
import { LOGS_DIR } from '@/lib/paths';

export const dynamic = 'force-dynamic';

export async function GET(request: Request) {
  const url = new URL(request.url);
  const requested = url.searchParams.get('path');
  if (!requested) {
    return new NextResponse('missing path param', { status: 400 });
  }

  // Security: constrain to LOGS_DIR. Resolve symlinks on both sides so a
  // symlink planted in logs/ cannot escape the prefix check.
  fs.mkdirSync(LOGS_DIR, { recursive: true });
  const logsReal = fs.realpathSync(path.resolve(LOGS_DIR));
  let resolved = path.resolve(requested);
  try {
    if (fs.existsSync(resolved)) resolved = fs.realpathSync(resolved);
  } catch {
    // realpath can fail on race; fall back to resolved path
  }
  if (!resolved.startsWith(logsReal + path.sep)) {
    return new NextResponse('path not allowed', { status: 403 });
  }

  const text = tailLog(resolved);
  const state = refreshState();
  const running = !!state && !state.ended_at && state.log_path === requested;

  return new NextResponse(text, {
    status: 200,
    headers: {
      'content-type': 'text/plain; charset=utf-8',
      'x-job-running': running ? '1' : '0',
    },
  });
}
