import { ActionForm } from '@/components/action-form';
import { LogTail } from '@/components/log-tail';
import { Button, Dot, Empty, fmtTs, LinkButton, PageHeader, Panel } from '@/components/ui';
import {
  cancelJobAction,
  clearJobStateAction,
  launchJobAction,
  sendTestAlertAction,
} from '@/lib/actions';
import { isRunning, refreshState, tailLog } from '@/lib/jobs';
import { telegramConfigured } from '@/lib/telegram';

export const dynamic = 'force-dynamic';

export default function ActionsPage() {
  const state = refreshState();
  const running = isRunning();
  const logTail = state ? tailLog(state.log_path) : '';
  const tg = telegramConfigured();

  return (
    <>
      <PageHeader
        title="Actions"
        subtitle="Launch jobs, tail logs, verify alerts. One job at a time."
      />

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-6">
        <Panel title="Launch a job">
          <div className="flex flex-wrap gap-2">
            <ActionForm action={launchJobAction}>
              <input type="hidden" name="kind" value="daily" />
              <Button type="submit" variant="primary" disabled={running}>
                Run daily collection
              </Button>
            </ActionForm>
            <ActionForm action={launchJobAction}>
              <input type="hidden" name="kind" value="dry_run" />
              <Button type="submit" disabled={running}>
                Dry-run (no Playwright)
              </Button>
            </ActionForm>
            <LinkButton href="/analysis" variant="ghost">
              Analysis →
            </LinkButton>
          </div>
          <p className="text-xs text-muted mt-3">
            Output streams to <code>logs/web-*.log</code>. Process detaches; safe to close the tab.
          </p>
        </Panel>

        <Panel title="Telegram">
          {tg ? (
            <p className="text-sm text-muted">
              Bot + chat_id configured in <code>.env</code>.
            </p>
          ) : (
            <p className="text-sm text-muted">
              Not configured. Set <code>TELEGRAM_BOT_TOKEN</code> and{' '}
              <code>TELEGRAM_CHAT_ID</code> in <code>.env</code>, then restart the dashboard. Use{' '}
              <code>python scripts/test_telegram.py --discover</code> to find your chat_id.
            </p>
          )}
          <div className="mt-3">
            <ActionForm action={sendTestAlertAction}>
              <Button type="submit" disabled={!tg}>
                Send test Telegram alert
              </Button>
            </ActionForm>
          </div>
        </Panel>
      </div>

      <Panel title="Current job" className="mb-6">
        {state ? (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <Dot variant={running ? 'running' : 'ok'} />
              <span className="font-medium">{state.kind}</span>
              <span className="text-xs text-muted tabular">pid {state.pid}</span>
              <span className="text-xs text-muted tabular">
                started {fmtTs(state.started_at)}
              </span>
              {state.ended_at && (
                <span className="text-xs text-muted tabular">
                  · ended {fmtTs(state.ended_at)} · rc={state.returncode}
                </span>
              )}
            </div>
            <div className="text-xs text-muted break-all">
              log: <code>{state.log_path}</code>
            </div>
            <div className="flex gap-2">
              {running ? (
                <ActionForm action={cancelJobAction}>
                  <Button type="submit" variant="danger">
                    Cancel (SIGTERM)
                  </Button>
                </ActionForm>
              ) : null}
              <ActionForm action={clearJobStateAction}>
                <Button type="submit" variant="ghost">
                  Clear state
                </Button>
              </ActionForm>
              <LinkButton href="/actions" variant="ghost">
                Refresh
              </LinkButton>
            </div>
          </div>
        ) : (
          <Empty>No job launched from the dashboard yet.</Empty>
        )}
      </Panel>

      {state ? (
        <Panel title="Log tail" subtitle={state.log_path}>
          <LogTail logPath={state.log_path} initial={logTail} poll={running} />
        </Panel>
      ) : null}
    </>
  );
}
