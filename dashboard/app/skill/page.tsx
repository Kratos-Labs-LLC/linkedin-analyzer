import fs from 'node:fs';
import path from 'node:path';

import { DraftPanel } from '@/components/draft-panel';
import { Empty, LinkButton, PageHeader, Panel, Stat } from '@/components/ui';
import {
  NEW_SKILL_PATH,
  OUTPUT_DIR,
  PROFILE_AUDIT_PATH,
  PROFILE_SKILL_PATH,
  leadmagnetSkillPath,
} from '@/lib/paths';

export const dynamic = 'force-dynamic';

function readIf(p: string): string | null {
  try {
    return fs.readFileSync(p, 'utf8');
  } catch {
    return null;
  }
}

function parseVerdict(
  md: string | null,
): { newWins: number; oldWins: number; ties: number; winRate: number } | null {
  if (!md) return null;
  const newWinsMatch = /new skill wins:\s*\*\*(\d+)\*\*/i.exec(md);
  const oldWinsMatch = /existing skill wins:\s*\*\*(\d+)\*\*/i.exec(md);
  const tiesMatch = /ties:\s*\*\*(\d+)\*\*/i.exec(md);
  if (!newWinsMatch || !oldWinsMatch || !tiesMatch) return null;
  const newWins = Number(newWinsMatch[1]);
  const oldWins = Number(oldWinsMatch[1]);
  const ties = Number(tiesMatch[1]);
  const decided = newWins + oldWins;
  return { newWins, oldWins, ties, winRate: decided ? newWins / decided : 0 };
}

export default function SkillPage() {
  const skillPath = path.join(OUTPUT_DIR, 'linkedin-high-engagement-writer', 'SKILL.md');
  const statsPath = path.join(OUTPUT_DIR, 'stats.json');
  const topPath = path.join(OUTPUT_DIR, 'top_posts.md');
  const bottomPath = path.join(OUTPUT_DIR, 'bottom_posts.md');
  const validationPath = path.join(OUTPUT_DIR, 'validation.md');

  const skill = readIf(skillPath);
  const stats = readIf(statsPath);
  const top = readIf(topPath);
  const bottom = readIf(bottomPath);
  const validation = readIf(validationPath);
  const profileSkill = readIf(PROFILE_SKILL_PATH);
  const profileAudit = readIf(PROFILE_AUDIT_PATH);

  const verdict = parseVerdict(validation);

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

  const oldSkillPath = leadmagnetSkillPath();
  const draftReady = !!oldSkillPath && fs.existsSync(oldSkillPath) && fs.existsSync(NEW_SKILL_PATH);

  return (
    <>
      <PageHeader title="Generated skill" subtitle={skillPath} />

      <Panel
        title="Draft a post"
        subtitle="Side-by-side compare what the new vs existing skill would write for any topic."
        className="mb-4"
      >
        <DraftPanel ready={draftReady} />
      </Panel>

      {validation ? (
        <Panel title="Validation" subtitle="Head-to-head A/B vs the existing skill" className="mb-4">
          {verdict ? (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
              <Stat label="Win-rate (new)" value={`${Math.round(verdict.winRate * 100)}%`} />
              <Stat label="New wins" value={String(verdict.newWins)} />
              <Stat label="Old wins" value={String(verdict.oldWins)} />
              <Stat label="Ties" value={String(verdict.ties)} />
            </div>
          ) : null}
          <details>
            <summary className="text-sm text-muted cursor-pointer">show full report</summary>
            <pre className="mt-3 whitespace-pre-wrap text-xs font-mono p-4 bg-bg rounded border border-border max-h-[60vh] overflow-auto">
              {validation}
            </pre>
          </details>
        </Panel>
      ) : null}

      <Panel
        title="linkedin-high-engagement-writer/SKILL.md"
        subtitle={skillPath}
        className="mb-4"
      >
        <pre className="whitespace-pre-wrap break-words text-xs font-mono p-4 bg-bg rounded border border-border max-h-[60vh] overflow-auto">
          {skill}
        </pre>
      </Panel>

      {profileSkill ? (
        <Panel
          title="linkedin-profile-optimizer/SKILL.md"
          subtitle={PROFILE_SKILL_PATH}
          className="mb-4"
        >
          <pre className="whitespace-pre-wrap break-words text-xs font-mono p-4 bg-bg rounded border border-border max-h-[60vh] overflow-auto">
            {profileSkill}
          </pre>
        </Panel>
      ) : null}

      {profileAudit ? (
        <Panel
          title="Personal profile audit"
          subtitle={`${PROFILE_AUDIT_PATH} — generated when MY_PROFILE_URL is set`}
          className="mb-4"
        >
          <pre className="whitespace-pre-wrap break-words text-xs font-mono p-4 bg-bg rounded border border-border max-h-[60vh] overflow-auto">
            {profileAudit}
          </pre>
        </Panel>
      ) : null}

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
