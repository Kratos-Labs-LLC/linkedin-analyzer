import { Empty, fmtNumber, fmtTs, PageHeader, Panel, Pill } from '@/components/ui';
import { listRuns } from '@/lib/db';

export const dynamic = 'force-dynamic';

export default function RunsPage() {
  const runs = listRuns(100);

  return (
    <>
      <PageHeader title="Run history" subtitle={`${runs.length} runs.`} />
      <Panel>
        {runs.length === 0 ? (
          <Empty>No runs yet.</Empty>
        ) : (
          <div className="-mx-5">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left border-b border-border">
                  {['ID', 'Date', 'Status', 'Started', 'Ended', 'Posts', 'Creators', 'Errors'].map(
                    (h) => (
                      <th key={h} className="label py-2 px-4 font-medium">
                        {h}
                      </th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => {
                  let errors: Array<Record<string, unknown>> | null = null;
                  if (r.errors_json) {
                    try {
                      errors = JSON.parse(r.errors_json);
                    } catch {
                      errors = null;
                    }
                  }
                  const variant =
                    r.status === 'success'
                      ? 'success'
                      : r.status === 'partial'
                        ? 'partial'
                        : r.status === 'auth_error'
                          ? 'auth_error'
                          : 'failed';
                  return (
                    <tr key={r.id} className="border-b border-border/60 hover:bg-panel-2/50">
                      <td className="py-2.5 px-4 tabular text-muted">{r.id}</td>
                      <td className="py-2.5 px-4 tabular">{r.run_date}</td>
                      <td className="py-2.5 px-4">
                        <Pill variant={variant}>{r.status}</Pill>
                      </td>
                      <td className="py-2.5 px-4 tabular text-xs text-muted">
                        {fmtTs(r.started_at)}
                      </td>
                      <td className="py-2.5 px-4 tabular text-xs text-muted">
                        {fmtTs(r.ended_at)}
                      </td>
                      <td className="py-2.5 px-4 tabular">{fmtNumber(r.posts_collected)}</td>
                      <td className="py-2.5 px-4 tabular">{fmtNumber(r.creators_scraped)}</td>
                      <td className="py-2.5 px-4">
                        {errors && errors.length > 0 ? (
                          <details className="text-xs">
                            <summary className="text-rose cursor-pointer">{errors.length}</summary>
                            <pre className="mt-2 p-2 bg-bg rounded text-[11px] overflow-x-auto max-w-md">
                              {JSON.stringify(errors, null, 2)}
                            </pre>
                          </details>
                        ) : (
                          <span className="text-muted">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </>
  );
}
