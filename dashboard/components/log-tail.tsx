'use client';

import { useEffect, useRef, useState } from 'react';

export function LogTail({
  logPath,
  initial,
  poll,
}: {
  logPath: string;
  initial: string;
  poll: boolean;
}) {
  const [content, setContent] = useState(initial);
  const [running, setRunning] = useState(poll);
  const preRef = useRef<HTMLPreElement | null>(null);

  useEffect(() => {
    if (!running) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function tick() {
      try {
        const res = await fetch(`/api/job/log?path=${encodeURIComponent(logPath)}`, {
          cache: 'no-store',
        });
        if (!res.ok) return;
        const text = await res.text();
        const stillRunning = res.headers.get('x-job-running') === '1';
        if (cancelled) return;
        setContent(text);
        if (!stillRunning) {
          setRunning(false);
          return;
        }
        timer = setTimeout(tick, 2000);
      } catch {
        // network blip — retry next interval
        if (!cancelled) timer = setTimeout(tick, 2000);
      }
    }

    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [logPath, running]);

  useEffect(() => {
    if (preRef.current) preRef.current.scrollTop = preRef.current.scrollHeight;
  }, [content]);

  return (
    <pre
      ref={preRef}
      className="panel text-xs max-h-96 overflow-auto p-4 font-mono whitespace-pre-wrap break-words text-text/90"
    >
      {content || <span className="text-dim">(empty)</span>}
    </pre>
  );
}
