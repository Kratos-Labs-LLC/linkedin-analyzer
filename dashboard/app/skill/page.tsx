import fs from 'node:fs';
import path from 'node:path';

import { Empty, LinkButton, PageHeader, Panel } from '@/components/ui';
import { OUTPUT_DIR } from '@/lib/paths';

export const dynamic = 'force-dynamic';

function readIf(p: string): string | null {
  try {
    return fs.readFileSync(p, 'utf8');
  } catch {
    return null;
  }
}

export default function SkillPage() {
  const skillPath = path.join(OUTPUT_DIR, 'linkedin-high-engagement-writer', 'SKILL.md');
  const statsPath = path.join(OUTPUT_DIR, 'stats.json');
  const topPath = path.join(OUTPUT_DIR, 'top_posts.md');
  const bottomPath = path.join(OUTPUT_DIR, 'bottom_posts.md');

  const skill = readIf(skillPath);
  const stats = readIf(statsPath);
  const top = readIf(topPath);
  const bottom = readIf(bottomPath);

  if (!skill) {
    return (
      <>
        <PageHeader title="Generated skill" />
        <Empty>
          No generated skill yet. Run the analysis pipeline on the{' '}
          <LinkButton href="/analysis" variant="ghost">
            Analysis page
          </LinkButton>
          .
        </Empty>
      </>
    );
  }

  return (
    <>
      <PageHeader title="Generated skill" subtitle={skillPath} />

      <Panel title="SKILL.md" className="mb-4">
        <pre className="whitespace-pre-wrap break-words text-xs font-mono p-4 bg-bg rounded border border-border max-h-[60vh] overflow-auto">
          {skill}
        </pre>
      </Panel>

      {stats ? (
        <Panel title="stats.json" className="mb-4">
          <details>
            <summary className="text-sm text-muted cursor-pointer">show</summary>
            <pre className="mt-3 whitespace-pre-wrap text-xs font-mono p-4 bg-bg rounded border border-border max-h-96 overflow-auto">
              {stats}
            </pre>
          </details>
        </Panel>
      ) : null}

      {top ? (
        <Panel title="top_posts.md" className="mb-4">
          <details>
            <summary className="text-sm text-muted cursor-pointer">show</summary>
            <pre className="mt-3 whitespace-pre-wrap text-xs font-mono p-4 bg-bg rounded border border-border max-h-96 overflow-auto">
              {top}
            </pre>
          </details>
        </Panel>
      ) : null}

      {bottom ? (
        <Panel title="bottom_posts.md">
          <details>
            <summary className="text-sm text-muted cursor-pointer">show</summary>
            <pre className="mt-3 whitespace-pre-wrap text-xs font-mono p-4 bg-bg rounded border border-border max-h-96 overflow-auto">
              {bottom}
            </pre>
          </details>
        </Panel>
      ) : null}
    </>
  );
}
