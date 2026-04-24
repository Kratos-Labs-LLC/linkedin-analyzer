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
  const preRef = useRef<HTMLPreElement | null>(null);

  useEffect(() => {
    if (!poll) return;
    let cancelled = false;

    async function tick() {
      try {
        const res = await fetch(`/api/job/log?path=${encodeURIComponent(logPath)}`, {
          cache: 'no-store',
        });
        if (!res.ok) return;
        const text = await res.text();
        if (!cancelled) setContent(text);
      } catch {
        /* ignore */
      }
    }

    const id = setInterval(tick, 2000);
    void tick();
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [logPath, poll]);

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
