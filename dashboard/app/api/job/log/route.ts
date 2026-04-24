import { NextResponse } from 'next/server';
import path from 'node:path';

import { tailLog } from '@/lib/jobs';
import { LOGS_DIR } from '@/lib/paths';

export const dynamic = 'force-dynamic';

export async function GET(request: Request) {
  const url = new URL(request.url);
  const requested = url.searchParams.get('path');
  if (!requested) {
    return new NextResponse('missing path param', { status: 400 });
  }
  const resolved = path.resolve(requested);
  // Security: only allow reading inside LOGS_DIR
  if (!resolved.startsWith(path.resolve(LOGS_DIR) + path.sep)) {
    return new NextResponse('path not allowed', { status: 403 });
  }
  const text = tailLog(resolved);
  return new NextResponse(text, {
    status: 200,
    headers: { 'content-type': 'text/plain; charset=utf-8' },
  });
}
