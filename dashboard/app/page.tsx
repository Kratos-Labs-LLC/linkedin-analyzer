import fs from 'node:fs';

import { LogTail } from '@/components/log-tail';
import { Sparkline } from '@/components/sparkline';
import {
  Dot,
  Empty,
  fmtNumber,
  fmtScore,
  fmtTs,
  LinkButton,
  PageHeader,
  Panel,
  Pill,
  Stat,
} from '@/components/ui';
import {
  activeCreatorCount,
  avgEngagement,
  collectionSummary,
  postsPerDay,
  recentRuns,
  topGrowthCreators,
} from '@/lib/db';
import { refreshState, isRunning, tailLog } from '@/lib/jobs';
import { AUTH_SENTINEL } from '@/lib/paths';
import { computeReadiness } from '@/lib/readiness';
import { telegramConfigured } from '@/lib/telegram';

export const dynamic = 'force-dynamic';

export default function Home() {
  const summary = collectionSummary();
  const nCreators = activeCreatorCount();
  const avg = avgEngagement();
  const runs = recentRuns(10);
  const readiness = computeReadiness();
  const job = refreshState();
  const running = isRunning();
  const authed = fs.existsSync(AUTH_SENTINEL);
  const tg = telegramConfigured();
  const spark = postsPerDay(14);

  return (
    <>
      <PageHeader
        title="Overview"
        subtitle="Collection health, day-31 readiness, and quick actions."
        action={
          <LinkButton href="/actions" variant="secondary">
            Open Actions →
          </LinkButton>
        }
      />

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        <Stat label="Total posts" value={fmtNumber(summary.total_posts)} />
        <Stat label="Active creators" value={fmtNumber(nCreators)} />
        <Stat label="Creators ≥20 posts" value={fmtNumber(summary.creators_with_20_posts)} />
        <Stat label="Avg engagement" value={fmtScore(avg)} />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-6">
        <Panel title="Collection pace">
          <Sparkline data={spark} days={14} />
        </Panel>

        <Panel title="Status">
          <div className="space-y-3">
            <Row
              label="Burner authed"
              value={
                authed ? (
                  <span className="text-green">yes</span>
                ) : (
                  <span className="text-rose">run scripts/auth_setup.py</span>
                )
              }
            />
            <Row
              label="Telegram alerts"
              value={
                tg ? (
                  <span className="text-green">configured</span>
                ) : (
                  <span className="text-rose">not configured</span>
                )
              }
            />
            <Row
              label="First collected"
              value={
                <span className="tabular text-xs">{fmtTs(summary.first_collected_at)}</span>
              }
            />
          </div>
        </Panel>

        <Panel title="Current job">
          {job ? (
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <Dot variant={running ? 'running' : 'ok'} />
                <span className="font-medium">{job.kind}</span>
                <span className="text-xs text-muted tabular">pid {job.pid}</span>
              </div>
              <div className="text-xs text-muted tabular">
                started {fmtTs(job.started_at)}
                {job.ended_at && (
                  <>
                    {' '}
                    · ended {fmtTs(job.ended_at)} (rc {job.returncode})
                  </>
                )}
              </div>
              <LinkButton href="/actions" variant="secondary">
                View log
              </LinkButton>
            </div>
          ) : (
            <p className="text-sm text-muted">
              No job has been launched from the dashboard yet.
            </p>
          )}
        </Panel>
      </div>

      <Panel
        title="Top growth-rate creators"
        subtitle="Followers gained per week (linear-fit slope over snapshot series)."
        className="mb-6"
      >
        <TopGrowthList />
      </Panel>

      <Panel title="Day-31 readiness" className="mb-6">
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
                  {g.ok ? 'ready' : 'not yet'}
                </span>
              </div>
            </li>
          ))}
        </ul>
      </Panel>

      <Panel
        title="Recent runs"
        action={
          <LinkButton href="/runs" variant="ghost">
            View all →
          </LinkButton>
        }
      >
        {runs.length === 0 ? (
          <Empty>No runs yet. Launch one from the Actions page.</Empty>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left border-b border-border">
                {['Date', 'Status', 'Posts', 'Creators', 'Ended'].map((h) => (
                  <th key={h} className="label py-2 font-medium">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id} className="border-b border-border/60 hover:bg-panel-2/50">
                  <td className="py-2 tabular">{r.run_date}</td>
                  <td className="py-2">
                    <Pill
                      variant={
                        r.status === 'success'
                          ? 'success'
                          : r.status === 'partial'
                            ? 'partial'
                            : r.status === 'auth_error'
                              ? 'auth_error'
                              : 'failed'
                      }
                    >
                      {r.status}
                    </Pill>
                  </td>
                  <td className="py-2 tabular">{fmtNumber(r.posts_collected)}</td>
                  <td className="py-2 tabular">{fmtNumber(r.creators_scraped)}</td>
                  <td className="py-2 text-muted tabular text-xs">{fmtTs(r.ended_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      {job && (
        <Panel
          title="Job log tail"
          subtitle={job.log_path}
          className="mt-6"
          action={
            <LinkButton href="/actions" variant="ghost">
              Open Actions →
            </LinkButton>
          }
        >
          <LogTail logPath={job.log_path} initial={tailLog(job.log_path)} poll={running} />
        </Panel>
      )}
    </>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between">
      <div className="label">{label}</div>
      <div className="text-sm">{value}</div>
    </div>
  );
}

function TopGrowthList() {
  const rows = topGrowthCreators(5);
  if (rows.length === 0) {
    return (
      <p className="text-sm text-muted">
        No growth data yet — needs at least 2 profile snapshots per creator.
      </p>
    );
  }
  return (
    <ul className="divide-y divide-border -mx-5">
      {rows.map((r) => (
        <li key={r.id} className="flex items-center justify-between px-5 py-3">
          <a href={`/creators/${r.id}`} className="text-sm hover:text-accent">
            {r.display_name ?? `creator ${r.id}`}
          </a>
          <div className="flex items-center gap-4 text-xs tabular text-muted">
            <span>{fmtNumber(r.latest_follower_count)} followers</span>
            <span className="text-accent">
              +{(r.growth_rate_per_week ?? 0).toFixed(1)}/week
            </span>
          </div>
        </li>
      ))}
    </ul>
  );
}
