import { notFound } from 'next/navigation';

import {
  fmtNumber,
  fmtScore,
  fmtTs,
  LinkButton,
  PageHeader,
  Panel,
  Pill,
} from '@/components/ui';
import { getPost } from '@/lib/db';

export const dynamic = 'force-dynamic';

export default async function PostDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const postId = parseInt(id, 10);
  if (!Number.isInteger(postId)) notFound();
  const result = getPost(postId);
  if (!result) notFound();
  const { post, features } = result;

  return (
    <>
      <PageHeader
        title={post.creator_name ?? 'Post'}
        subtitle={`Score ${fmtScore(post.engagement_score)} · collected ${fmtTs(post.collected_at)}`}
        action={
          <LinkButton href="/posts" variant="ghost">
            ← Back
          </LinkButton>
        }
      />

      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-6">
        <Meta label="Reactions" value={fmtNumber(post.reactions)} />
        <Meta label="Comments" value={fmtNumber(post.comments)} />
        <Meta label="Reshares" value={fmtNumber(post.reshares)} />
        <Meta label="Followers" value={fmtNumber(post.follower_count_at_collection)} />
        <Meta label="Posted" value={fmtTs(post.post_date)} />
      </div>

      {post.is_repost_with_commentary ? (
        <div className="mb-4">
          <Pill variant="standard">repost + commentary</Pill>
        </div>
      ) : null}

      {post.post_url ? (
        <div className="mb-4">
          <a
            href={post.post_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-accent text-sm hover:underline"
          >
            Open on LinkedIn ↗
          </a>
        </div>
      ) : null}

      <Panel title="Post text" className="mb-6">
        <pre className="whitespace-pre-wrap break-words font-sans text-sm text-text/90 leading-relaxed">
          {post.post_text}
        </pre>
      </Panel>

      {features ? (
        <Panel title="Extracted features">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1.5 text-sm">
            {Object.entries(features).map(([k, v]) => (
              <div
                key={k}
                className="flex items-start justify-between gap-4 border-b border-border/60 py-1.5"
              >
                <div className="text-muted font-mono text-xs">{k}</div>
                <div className="text-right tabular text-sm max-w-xs break-words">
                  {formatFeatureValue(v)}
                </div>
              </div>
            ))}
          </div>
        </Panel>
      ) : (
        <Panel title="Extracted features">
          <p className="text-sm text-muted">
            Not yet extracted. Features populate during day-31 analysis.
          </p>
        </Panel>
      )}
    </>
  );
}

function Meta({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="panel p-3">
      <div className="label">{label}</div>
      <div className="tabular text-base mt-1">{value}</div>
    </div>
  );
}

function formatFeatureValue(v: unknown): string {
  if (v === null) return 'null';
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  if (Array.isArray(v)) return v.join(', ') || '—';
  return String(v);
}
