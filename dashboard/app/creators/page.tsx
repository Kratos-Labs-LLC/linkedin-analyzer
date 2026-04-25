import { ActionForm } from '@/components/action-form';
import {
  Button,
  Checkbox,
  Empty,
  fmtNumber,
  fmtTs,
  Input,
  Label,
  PageHeader,
  Panel,
  Pill,
} from '@/components/ui';
import {
  addCreatorAction,
  removeCreatorAction,
  toggleAnchorAction,
} from '@/lib/actions';
import { listCreatorsWithCounts } from '@/lib/db';
import { CREATORS_YAML_PATH } from '@/lib/paths';

export const dynamic = 'force-dynamic';

export default function CreatorsPage() {
  const creators = listCreatorsWithCounts();

  return (
    <>
      <PageHeader
        title="Creators"
        subtitle={`Edits are written to ${CREATORS_YAML_PATH} and synced to the DB.`}
      />

      <Panel title="Add creator" className="mb-6">
        <ActionForm action={addCreatorAction}>
          <div className="grid grid-cols-1 md:grid-cols-[1fr_1fr_auto_auto] gap-3 items-end">
            <div>
              <Label htmlFor="url">LinkedIn URL</Label>
              <Input
                id="url"
                name="url"
                type="url"
                required
                placeholder="https://linkedin.com/in/…"
              />
            </div>
            <div>
              <Label htmlFor="name">Display name</Label>
              <Input id="name" name="name" required />
            </div>
            <div className="self-end pb-1.5">
              <Checkbox name="anchor" label="anchor (weight 1.5)" />
            </div>
            <Button type="submit" variant="primary">
              Add
            </Button>
          </div>
        </ActionForm>
      </Panel>

      <Panel title={`${creators.length} creators`}>
        {creators.length === 0 ? (
          <Empty>No creators yet. Add one above.</Empty>
        ) : (
          <div className="-mx-5">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left border-b border-border">
                  {['Name', 'URL', 'Tier', 'Posts', 'Followers', 'Last scraped', 'Active', ''].map(
                    (h) => (
                      <th key={h} className="label py-2 px-4 font-medium">
                        {h}
                      </th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody>
                {creators.map((c) => (
                  <tr key={c.id} className="border-b border-border/60 hover:bg-panel-2/50">
                    <td className="py-2.5 px-4">
                      <a href={`/creators/${c.id}`} className="hover:text-accent">
                        {c.display_name ?? '—'}
                      </a>
                    </td>
                    <td className="py-2.5 px-4 text-xs">
                      <a
                        href={c.linkedin_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-accent hover:underline"
                      >
                        {c.linkedin_url}
                      </a>
                    </td>
                    <td className="py-2.5 px-4">
                      <Pill variant={c.weight >= 1.5 ? 'anchor' : 'standard'}>
                        {c.weight >= 1.5 ? 'anchor' : 'standard'}
                      </Pill>
                    </td>
                    <td className="py-2.5 px-4 tabular">{fmtNumber(c.post_count)}</td>
                    <td className="py-2.5 px-4 tabular">
                      {fmtNumber(c.current_follower_count)}
                    </td>
                    <td className="py-2.5 px-4 text-muted tabular text-xs">
                      {fmtTs(c.last_scraped_at)}
                    </td>
                    <td className="py-2.5 px-4">
                      <Pill variant={c.active ? 'active' : 'inactive'}>
                        {c.active ? 'active' : 'inactive'}
                      </Pill>
                    </td>
                    <td className="py-2.5 px-4">
                      <div className="flex gap-1.5">
                        <ActionForm action={toggleAnchorAction}>
                          <input type="hidden" name="url" value={c.linkedin_url} />
                          <input
                            type="hidden"
                            name="anchor"
                            value={c.weight >= 1.5 ? '0' : '1'}
                          />
                          <Button type="submit" variant="ghost">
                            {c.weight >= 1.5 ? '→ standard' : '→ anchor'}
                          </Button>
                        </ActionForm>
                        <ActionForm action={removeCreatorAction}>
                          <input type="hidden" name="url" value={c.linkedin_url} />
                          <Button type="submit" variant="danger">
                            Remove
                          </Button>
                        </ActionForm>
                      </div>
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
