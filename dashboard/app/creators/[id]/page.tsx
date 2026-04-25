import { notFound } from 'next/navigation';

import {
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
  computeGrowthRatePerWeek,
  getCreator,
  latestProfileSnapshot,
  listProfileSnapshots,
  postsForCreatorByGrowth,
  type ProfileSnapshotRow,
} from '@/lib/db';

export const dynamic = 'force-dynamic';

export default async function CreatorDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const creatorId = parseInt(id, 10);
  if (!Number.isInteger(creatorId)) notFound();
  const creator = getCreator(creatorId);
  if (!creator) notFound();

  const snapshots = listProfileSnapshots(creatorId);
  const latest = latestProfileSnapshot(creatorId);
  const growthRate = computeGrowthRatePerWeek(snapshots);
  const posts = postsForCreatorByGrowth(creatorId, 30);

  return (
    <>
      <PageHeader
        title={creator.display_name ?? 'Creator'}
        subtitle={creator.linkedin_url}
        action={
          <LinkButton href="/creators" variant="ghost">
            ← All creators
          </LinkButton>
        }
      />

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        <Stat
          label="Latest followers"
          value={fmtNumber(latest?.follower_count ?? null)}
        />
        <Stat
          label="Growth / week"
          value={
            growthRate === null ? '—' : `${growthRate >= 0 ? '+' : ''}${growthRate.toFixed(1)}`
          }
          hint={growthRate === null ? 'need ≥2 snapshots' : 'least-squares fit'}
        />
        <Stat label="Snapshots" value={fmtNumber(snapshots.length)} />
        <Stat
          label="Tier"
          value={creator.weight >= 1.5 ? 'anchor' : 'standard'}
          mono={false}
        />
      </div>

      <Panel title="Profile snapshot" subtitle={fmtTs(latest?.snapshot_at)} className="mb-6">
        {latest ? (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-2 text-sm">
            <Row label="Headline" value={latest.headline ?? '—'} />
            <Row label="Role / company" value={[latest.current_role, latest.current_company].filter(Boolean).join(' @ ') || '—'} />
            <Row label="Location" value={latest.location ?? '—'} />
            <Row label="Featured items" value={fmtNumber(latest.featured_count ?? 0)} />
            <Row label="Profile photo" value={latest.has_profile_photo ? 'yes' : 'no'} />
            <Row label="Banner" value={latest.has_banner ? 'yes' : 'no'} />
          </div>
        ) : (
          <Empty>No profile snapshots yet.</Empty>
        )}
        {latest?.about_text ? (
          <details className="mt-4">
            <summary className="text-sm text-muted cursor-pointer">About text</summary>
            <pre className="mt-2 whitespace-pre-wrap text-xs text-text/80 font-sans p-3 bg-bg rounded border border-border max-h-64 overflow-auto">
              {latest.about_text}
            </pre>
          </details>
        ) : null}
      </Panel>

      <Panel title="Follower count over time" className="mb-6">
        {snapshots.length < 2 ? (
          <Empty>Need at least 2 snapshots to draw a trend.</Empty>
        ) : (
          <FollowerSparkline snapshots={snapshots} />
        )}
      </Panel>

      <Panel
        title="Posts ranked by 7-day follower growth"
        subtitle="Highest-pull posts for this creator. growth_7d is the follower delta in the 7 days after the post."
      >
        {posts.length === 0 ? (
          <Empty>No posts captured for this creator yet.</Empty>
        ) : (
          <div className="-mx-5">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left border-b border-border">
                  {['Date', 'Preview', 'Growth 7d', 'React', 'Comm', 'Resh', 'Score'].map((h) => (
                    <th key={h} className="label py-2 px-3 font-medium">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {posts.map((p) => (
                  <tr key={p.id} className="border-b border-border/60 hover:bg-panel-2/50">
                    <td className="py-2.5 px-3 text-xs tabular text-muted">
                      {fmtTs(p.post_date)}
                    </td>
                    <td className="py-2.5 px-3 max-w-md">
                      <div className="line-clamp-2 text-text/85">{p.post_text}</div>
                    </td>
                    <td className="py-2.5 px-3 tabular">
                      {p.growth_7d === null ? (
                        <span className="text-dim">—</span>
                      ) : (
                        <span className={p.growth_7d >= 0 ? 'text-accent' : 'text-rose'}>
                          {p.growth_7d >= 0 ? '+' : ''}
                          {p.growth_7d}
                        </span>
                      )}
                    </td>
                    <td className="py-2.5 px-3 tabular">{fmtNumber(p.reactions)}</td>
                    <td className="py-2.5 px-3 tabular">{fmtNumber(p.comments)}</td>
                    <td className="py-2.5 px-3 tabular">{fmtNumber(p.reshares)}</td>
                    <td className="py-2.5 px-3 tabular">{fmtScore(p.engagement_score)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-border/40 py-1.5">
      <span className="label">{label}</span>
      <span className="text-sm text-right max-w-xs truncate" title={String(value)}>
        {value}
      </span>
    </div>
  );
}

function FollowerSparkline({
  snapshots,
}: {
  snapshots: ProfileSnapshotRow[];
}) {
  const points = snapshots
    .filter((s) => s.follower_count !== null)
    .map((s) => ({
      t: new Date(s.snapshot_at.replace(/Z$/, 'Z')).getTime(),
      f: s.follower_count as number,
    }))
    .filter((p) => Number.isFinite(p.t));
  if (points.length < 2) return <Empty>Need at least 2 snapshots.</Empty>;

  const width = 720;
  const height = 140;
  const padding = 10;
  const minT = Math.min(...points.map((p) => p.t));
  const maxT = Math.max(...points.map((p) => p.t));
  const minF = Math.min(...points.map((p) => p.f));
  const maxF = Math.max(...points.map((p) => p.f));
  const fSpan = Math.max(1, maxF - minF);

  const xy = points.map((p) => [
    padding + ((p.t - minT) / Math.max(1, maxT - minT)) * (width - 2 * padding),
    height - padding - ((p.f - minF) / fSpan) * (height - 2 * padding),
  ]);

  const polyline = xy.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ');

  return (
    <div className="space-y-2">
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full">
        <polyline
          points={polyline}
          fill="none"
          stroke="currentColor"
          strokeWidth={1.5}
          className="text-accent"
        />
        {xy.map(([x, y], i) => (
          <circle key={i} cx={x} cy={y} r={2.5} className="fill-accent" />
        ))}
      </svg>
      <div className="flex items-center justify-between text-xs text-muted tabular">
        <span>{fmtNumber(minF)}</span>
        <span>{fmtNumber(maxF)}</span>
      </div>
    </div>
  );
}
