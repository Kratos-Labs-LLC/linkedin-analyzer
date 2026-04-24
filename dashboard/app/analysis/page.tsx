import fs from 'node:fs';
import path from 'node:path';

import { Button, Checkbox, Dot, Input, Label, PageHeader, Panel } from '@/components/ui';
import { runAnalysisAction } from '@/lib/actions';
import { isRunning } from '@/lib/jobs';
import { OUTPUT_DIR } from '@/lib/paths';
import { computeReadiness } from '@/lib/readiness';

export const dynamic = 'force-dynamic';

type SP = { [k: string]: string | string[] | undefined };

export default async function AnalysisPage({
  searchParams,
}: {
  searchParams: Promise<SP>;
}) {
  const sp = await searchParams;
  const errorMsg = typeof sp.error === 'string' ? sp.error : Array.isArray(sp.error) ? sp.error[0] : null;
  const readiness = computeReadiness();
  const ready = readiness.every((g) => g.ok);
  const running = isRunning();

  const statsExists = fs.existsSync(path.join(OUTPUT_DIR, 'stats.json'));
  const skillExists = fs.existsSync(
    path.join(OUTPUT_DIR, 'linkedin-high-engagement-writer', 'SKILL.md'),
  );

  return (
    <>
      <PageHeader
        title="Day-31 analysis"
        subtitle="Feature extraction → stats → skill synthesis."
      />

      {errorMsg ? (
        <div className="mb-6 rounded-md border border-rose/40 bg-rose/10 px-4 py-3 text-sm text-rose">
          {errorMsg}
        </div>
      ) : null}

      <Panel title="Readiness" className="mb-6">
        <ul className="divide-y divide-border -mx-5">
          {readiness.map((g) => (
            <li key={g.label} className="flex items-center justify-between px-5 py-3">
              <div>
                <div className="text-sm">{g.label}</div>
                <div className="text-xs text-muted tabular mt-0.5">{g.actual}</div>
              </div>
              <div className="flex items-center gap-2 text-xs">
                <Dot variant={g.ok ? 'ok' : 'err'} />
                <span className={g.ok ? 'text-green' : 'text-rose'}>
                  {g.ok ? 'ok' : 'not met'}
                </span>
              </div>
            </li>
          ))}
        </ul>
      </Panel>

      <Panel title="Run analysis" className="mb-6">
        <form action={runAnalysisAction} className="space-y-4">
          <div>
            <Label htmlFor="leadmagnet_skill_path">
              Path to existing <code>leadmagnet-post-writer/SKILL.md</code>
            </Label>
            <Input
              id="leadmagnet_skill_path"
              name="leadmagnet_skill_path"
              required
              placeholder="/Users/you/.claude/skills/leadmagnet-post-writer/SKILL.md"
            />
          </div>

          <div className="flex flex-wrap gap-5">
            <Checkbox name="force" label="--force (skip readiness gates)" />
            <Checkbox name="skip_extract" label="--skip-extract" />
            <Checkbox name="skip_synth" label="--skip-synth" />
          </div>

          <div>
            <Button type="submit" variant="primary" disabled={running}>
              {running ? 'Another job running…' : ready ? 'Run analysis' : 'Run anyway (--force)'}
            </Button>
          </div>
        </form>

        <p className="text-xs text-muted mt-4">
          Requires <code>ANTHROPIC_API_KEY</code> in <code>.env</code>. Feature extraction runs
          through ~300 posts (≈30 min, ~$2). Synthesis is a single Opus call (≈1 min, ~$1).
        </p>
      </Panel>

      <Panel title="Outputs">
        <ul className="text-sm space-y-2">
          <li>
            stats.json:{' '}
            {statsExists ? (
              <span className="text-green">present</span>
            ) : (
              <span className="text-muted">not generated yet</span>
            )}
          </li>
          <li>
            SKILL.md:{' '}
            {skillExists ? (
              <>
                <span className="text-green">present</span>{' '}
                <a href="/skill" className="text-accent hover:underline">
                  view →
                </a>
              </>
            ) : (
              <span className="text-muted">not generated yet</span>
            )}
          </li>
        </ul>
      </Panel>
    </>
  );
}
