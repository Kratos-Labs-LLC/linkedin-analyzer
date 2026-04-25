'use client';

import { useState } from 'react';

import { Button, Label, Pill } from '@/components/ui';

type DraftResponse = {
  newDraft: string;
  oldDraft: string;
  winner: 'new' | 'old' | 'tie';
  reasoning: string;
};

export function DraftPanel({ ready }: { ready: boolean }) {
  const [prompt, setPrompt] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<DraftResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!prompt.trim()) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await fetch('/api/draft', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ prompt }),
      });
      const data = (await res.json()) as DraftResponse | { error: string };
      if (!res.ok) {
        setError('error' in data ? data.error : `HTTP ${res.status}`);
        return;
      }
      setResult(data as DraftResponse);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  if (!ready) {
    return (
      <p className="text-sm text-muted">
        Draft panel is unavailable until both skills exist. Make sure{' '}
        <code>LEADMAGNET_SKILL_PATH</code> is set in <code>.env</code> and the
        analysis pipeline has produced a new SKILL.md.
      </p>
    );
  }

  return (
    <div className="space-y-4">
      <form onSubmit={handleSubmit} className="space-y-2">
        <Label htmlFor="draft-prompt">Topic prompt</Label>
        <textarea
          id="draft-prompt"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          rows={3}
          maxLength={2000}
          placeholder="e.g. Why hiring a fractional CMO usually fails for sub-$5M agencies."
          className="w-full rounded-md bg-panel-2 border border-border px-3 py-2 text-sm text-text placeholder:text-dim focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent"
        />
        <div className="flex items-center gap-3">
          <Button type="submit" variant="primary" disabled={loading || !prompt.trim()}>
            {loading ? 'Drafting + judging…' : 'Generate'}
          </Button>
          <span className="text-xs text-muted">
            ~3 Claude calls per click (~$0.10).
          </span>
        </div>
      </form>

      {error ? (
        <div className="rounded-md border border-rose/40 bg-rose/10 px-4 py-2 text-sm text-rose">
          {error}
        </div>
      ) : null}

      {result ? <DraftResult result={result} /> : null}
    </div>
  );
}

function DraftResult({ result }: { result: DraftResponse }) {
  const verdict = result.winner;
  const verdictPill =
    verdict === 'new' ? (
      <Pill variant="active">new skill wins</Pill>
    ) : verdict === 'old' ? (
      <Pill variant="anchor">existing skill wins</Pill>
    ) : (
      <Pill variant="standard">tie</Pill>
    );

  return (
    <div className="space-y-4">
      <div className="rounded-md border border-border bg-panel-2 px-4 py-3">
        <div className="flex items-center gap-3 mb-2">
          <span className="label">Verdict</span>
          {verdictPill}
        </div>
        <p className="text-sm text-text/90">{result.reasoning || '(no reasoning provided)'}</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <DraftCard
          label="New skill"
          highlighted={verdict === 'new'}
          text={result.newDraft}
        />
        <DraftCard
          label="Existing skill"
          highlighted={verdict === 'old'}
          text={result.oldDraft}
        />
      </div>
    </div>
  );
}

function DraftCard({
  label,
  text,
  highlighted,
}: {
  label: string;
  text: string;
  highlighted: boolean;
}) {
  return (
    <div
      className={
        highlighted
          ? 'rounded-md border border-accent/60 bg-accent/5 p-3'
          : 'rounded-md border border-border bg-panel p-3'
      }
    >
      <div className="flex items-center justify-between mb-2">
        <span className="label">{label}</span>
        <span className="text-[10px] text-dim tabular">{text.length} chars</span>
      </div>
      <pre className="whitespace-pre-wrap break-words font-sans text-xs leading-relaxed text-text/90">
        {text}
      </pre>
    </div>
  );
}
