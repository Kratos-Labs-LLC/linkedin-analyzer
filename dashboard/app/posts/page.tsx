import {
  Button,
  Empty,
  fmtNumber,
  fmtScore,
  Input,
  Label,
  LinkButton,
  PageHeader,
  Panel,
  Select,
} from '@/components/ui';
import { listActiveCreators, listPosts } from '@/lib/db';

export const dynamic = 'force-dynamic';

type SP = { [k: string]: string | string[] | undefined };

export default async function PostsPage({
  searchParams,
}: {
  searchParams: Promise<SP>;
}) {
  const sp = await searchParams;
  const creatorIdRaw = firstParam(sp.creator_id);
  const minScoreRaw = firstParam(sp.min_score);
  const sort = (firstParam(sp.sort) ?? 'engagement') as 'engagement' | 'date';
  const limit = Math.max(10, Math.min(500, parseInt(firstParam(sp.limit) ?? '100', 10) || 100));
  const creatorId = creatorIdRaw ? parseInt(creatorIdRaw, 10) : undefined;
  const minScore = minScoreRaw ? parseFloat(minScoreRaw) : undefined;

  const creators = listActiveCreators();
  const posts = listPosts({
    creatorId,
    minScore: Number.isFinite(minScore) ? minScore : undefined,
    sort,
    limit,
  });

  return (
    <>
      <PageHeader title="Posts" subtitle={`${posts.length} shown.`} />

      <Panel title="Filter" className="mb-4">
        <form method="get" action="/posts">
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3 items-end">
            <div>
              <Label htmlFor="creator_id">Creator</Label>
              <Select id="creator_id" name="creator_id" defaultValue={creatorIdRaw ?? ''} className="w-full">
                <option value="">Any</option>
                {creators.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.display_name ?? `creator ${c.id}`}
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <Label htmlFor="min_score">Min score</Label>
              <Input
                id="min_score"
                name="min_score"
                type="number"
                step="0.0001"
                defaultValue={minScoreRaw ?? ''}
              />
            </div>
            <div>
              <Label htmlFor="sort">Sort</Label>
              <Select id="sort" name="sort" defaultValue={sort} className="w-full">
                <option value="engagement">Engagement (desc)</option>
                <option value="date">Date collected (desc)</option>
              </Select>
            </div>
            <div>
              <Label htmlFor="limit">Limit</Label>
              <Input
                id="limit"
                name="limit"
                type="number"
                min={10}
                max={500}
                defaultValue={limit}
              />
            </div>
            <Button type="submit" variant="primary">
              Apply
            </Button>
          </div>
        </form>
      </Panel>

      <Panel title={`${posts.length} posts`}>
        {posts.length === 0 ? (
          <Empty>No posts match the filter.</Empty>
        ) : (
          <div className="-mx-5">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left border-b border-border">
                  {['Creator', 'Preview', 'React', 'Comm', 'Resh', 'Followers', 'Score', ''].map(
                    (h) => (
                      <th key={h} className="label py-2 px-3 font-medium">
                        {h}
                      </th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody>
                {posts.map((p) => (
                  <tr key={p.id} className="border-b border-border/60 hover:bg-panel-2/50">
                    <td className="py-2.5 px-3">{p.creator_name ?? '—'}</td>
                    <td className="py-2.5 px-3 max-w-md">
                      <div className="line-clamp-2 text-muted">{p.post_text}</div>
                    </td>
                    <td className="py-2.5 px-3 tabular">{fmtNumber(p.reactions)}</td>
                    <td className="py-2.5 px-3 tabular">{fmtNumber(p.comments)}</td>
                    <td className="py-2.5 px-3 tabular">{fmtNumber(p.reshares)}</td>
                    <td className="py-2.5 px-3 tabular">
                      {fmtNumber(p.follower_count_at_collection)}
                    </td>
                    <td className="py-2.5 px-3 tabular text-accent">
                      {fmtScore(p.engagement_score)}
                    </td>
                    <td className="py-2.5 px-3">
                      <LinkButton href={`/posts/${p.id}`} variant="ghost">
                        View
                      </LinkButton>
                    </td>
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

function firstParam(v: string | string[] | undefined): string | undefined {
  if (Array.isArray(v)) return v[0];
  return v;
}
