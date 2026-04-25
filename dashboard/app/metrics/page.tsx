import fs from 'node:fs';
import path from 'node:path';

import {
  Empty,
  fmtNumber,
  fmtScore,
  fmtTs,
  PageHeader,
  Panel,
  Pill,
  Stat,
} from '@/components/ui';
import { OUTPUT_DIR } from '@/lib/paths';

export const dynamic = 'force-dynamic';

type Metrics = {
  generated_at?: string;
  collection?: {
    total_posts: number;
    active_creators: number;
    creators_with_20_posts: number;
    snapshots: number;
    avg_engagement_score: number | null;
    first_collected_at: string | null;
  };
  coverage?: {
    posts_with_features: number;
    profiles_with_features: number;
    posts_with_growth_7d: number;
    feature_coverage_pct: number;
    growth_coverage_pct: number;
  };
  storage_bytes?: {
    raw_html_in_posts: number;
    raw_html_in_profiles: number;
  };
  outputs?: Record<string, { present: boolean; size_bytes?: number }>;
  recent_runs?: Array<{
    id: number;
    run_date: string;
    status: string;
    posts_collected: number | null;
    creators_scraped: number | null;
    ended_at: string | null;
  }>;
};

function readMetrics(): Metrics | null {
  const p = path.join(OUTPUT_DIR, 'metrics.json');
  try {
    return JSON.parse(fs.readFileSync(p, 'utf8')) as Metrics;
  } catch {
    return null;
  }
}

function fmtBytes(n: number | undefined): string {
  if (!n) return '0 B';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export default function MetricsPage() {
  const m = readMetrics();
  if (!m) {
    return (
      <>
        <PageHeader title="Metrics" />
        <Empty>
          No <code>output/metrics.json</code> yet. Run the analyzer:{' '}
          <code>python scripts/run_analysis.py …</code>.
        </Empty>
      </>
    );
  }

  const c = m.collection!;
  const cov = m.coverage!;
  return (
    <>
      <PageHeader
        title="Metrics"
        subtitle={
          m.generated_at ? `generated ${fmtTs(m.generated_at)}` : 'last run snapshot'
        }
      />

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        <Stat label="Total posts" value={fmtNumber(c.total_posts)} />
        <Stat label="Active creators" value={fmtNumber(c.active_creators)} />
        <Stat label="Snapshots" value={fmtNumber(c.snapshots)} />
        <Stat label="Avg engagement" value={fmtScore(c.avg_engagement_score)} />
      </div>

      <Panel title="Coverage" className="mb-6">
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
          <Stat
            label="Feature extraction"
            value={`${cov.feature_coverage_pct}%`}
            hint={`${cov.posts_with_features} / ${c.total_posts} posts`}
          />
          <Stat
            label="Growth attribution"
            value={`${cov.growth_coverage_pct}%`}
            hint={`${cov.posts_with_growth_7d} / ${c.total_posts} posts`}
          />
          <Stat
            label="Profile features"
            value={fmtNumber(cov.profiles_with_features)}
            hint={`of ${c.active_creators} active creators`}
          />
        </div>
      </Panel>

      <Panel title="Storage" className="mb-6">
        <ul className="text-sm space-y-1">
          <li>
            <span className="label">raw_html in posts:</span>{' '}
            <span className="tabular">{fmtBytes(m.storage_bytes?.raw_html_in_posts)}</span>
          </li>
          <li>
            <span className="label">raw_html in profiles:</span>{' '}
            <span className="tabular">{fmtBytes(m.storage_bytes?.raw_html_in_profiles)}</span>
          </li>
        </ul>
      </Panel>

      <Panel title="Output artifacts" className="mb-6">
        <ul className="text-sm space-y-1.5 -mx-5">
          {Object.entries(m.outputs ?? {}).map(([name, info]) => (
            <li
              key={name}
              className="flex items-center justify-between px-5 py-1"
            >
              <code className="text-muted">{name}</code>
              <div className="flex items-center gap-3">
                {info.present ? (
                  <Pill variant="success">present</Pill>
                ) : (
                  <Pill variant="standard">missing</Pill>
                )}
                {info.present && info.size_bytes !== undefined ? (
                  <span className="tabular text-xs text-muted">
                    {fmtBytes(info.size_bytes)}
                  </span>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      </Panel>

      <Panel title="Recent runs">
        {!m.recent_runs?.length ? (
          <Empty>No runs in DB.</Empty>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left border-b border-border">
                {['ID', 'Date', 'Status', 'Posts', 'Creators', 'Ended'].map((h) => (
                  <th key={h} className="label py-2 font-medium">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {m.recent_runs.map((r) => (
                <tr key={r.id} className="border-b border-border/60">
                  <td className="py-2 tabular text-muted">{r.id}</td>
                  <td className="py-2 tabular">{r.run_date}</td>
                  <td className="py-2">
                    <Pill variant={r.status as never}>{r.status}</Pill>
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
    </>
  );
}
