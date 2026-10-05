import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
import { mkdtemp, mkdir, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const response = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(response.ok, response.error).toBe(true); expect(response.data?.type).not.toBe('error'); return response.data;
}
test.beforeEach(async ({ request }) => {
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
});

test('Engineer Activity applies real question/note/journal updates and rejects a stale reconnect read without losing drafts', async ({ page, request }) => {
  test.setTimeout(75_000); page.setDefaultTimeout(10_000);
  const group = `Activity live ${Date.now()}`; const writes: Row[] = []; const reads: Row[] = [];
  let socket: WebSocketRoute | undefined; let connections = 0; let holdNext = false; let release: (() => void) | undefined;
  const settings = async () => (await command(request, { cmd: 'get_group_settings', group })).engineer_settings as Row;
  await page.routeWebSocket(/\/ws\?/, (client) => {
    socket = client; connections++; const server = client.connectToServer();
    client.onMessage((raw) => { const data = JSON.parse(String(raw)) as Row; if (String(data.cmd).startsWith('engineer_')) writes.push(data); server.send(raw); });
    server.onMessage((raw) => client.send(raw));
  });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (['engineer_journal_snapshot', 'engineer_session_map_read', 'get_group_settings'].includes(String(data.cmd))) reads.push(data);
    if (data.cmd === 'engineer_journal_snapshot' && holdNext) {
      holdNext = false; const response = await route.fetch(); const body = await response.body();
      await new Promise<void>((resolve) => { release = resolve; });
      await route.fulfill({ response, body }); return;
    }
    await route.continue();
  });
  try {
    await command(request, { cmd: 'add_group', group });
    await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: '/private/tmp', git_worktree: false, notifications: false } });
    const engineer = String((await command(request, { cmd: 'add_engineer', group, name: 'Activity Engineer', command: '/bin/cat', provider: 'generic', directory: '/private/tmp' })).id);
    const other = String((await command(request, { cmd: 'add_engineer', group, name: 'Other Activity Engineer', command: '/bin/cat', provider: 'generic', directory: '/private/tmp' })).id);
    const append = (entry: string, author = engineer) => command(request, { cmd: 'engineer_journal_append', group, author_cell_id: author, entry_type: 'checkpoint', entry });
    const initial = await append('Initial retained journal entry');
    await command(request, { cmd: 'engineer_ask', group, engineer_id: engineer, question: 'First live question' });
    await command(request, { cmd: 'engineer_note', group, engineer_id: engineer, message: 'First live note' });
    await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_select_agent', id: engineer });
    await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'agents', controlTab: 'mission' } });
    await page.goto('/'); await page.getByRole('tab', { name: 'Activity', exact: true }).click();
    const panel = page.getByRole('region', { name: 'Activity for Activity Engineer', exact: true });
    const answer = panel.getByPlaceholder('Answer and resume delivery'); const journal = panel.getByRole('region', { name: 'engineer journal', exact: true });
    await expect(panel.getByText('First live question', { exact: true })).toBeVisible(); await expect(panel.getByText('First live note', { exact: true })).toBeVisible();
    await expect(panel.getByText('Refreshing activity…', { exact: true })).toHaveCount(0);
    const initialSummary = journal.locator('summary').filter({ hasText: 'Initial retained journal entry' }); await initialSummary.click();
    await answer.fill('Keep this reviewed answer'); await answer.evaluate((node: HTMLTextAreaElement) => node.setSelectionRange(2, 8));
    const before = reads.length; await append('New selected journal entry'); await append('Other author journal entry', other);
    await expect(journal.locator('summary').filter({ hasText: 'New selected journal entry' })).toBeVisible(); await expect(journal.getByText('Other author journal entry', { exact: true })).toHaveCount(0);
    await expect(answer).toHaveValue('Keep this reviewed answer'); await expect(answer).toBeFocused(); expect(await answer.evaluate((node: HTMLTextAreaElement) => [node.selectionStart, node.selectionEnd])).toEqual([2, 8]);
    await expect(initialSummary.locator('..')).toHaveAttribute('open', ''); expect(reads).toHaveLength(before);
    await panel.getByRole('button', { name: 'Dismiss note', exact: true }).click(); await expect(panel.getByRole('button', { name: 'Dismiss note', exact: true })).toHaveCount(0); await expect(journal.locator('summary').filter({ hasText: 'First live note' })).toBeVisible(); expect((await settings()).pending_note).toBe(''); await expect(answer).toHaveValue('Keep this reviewed answer');
    await panel.getByRole('button', { name: 'Dismiss', exact: true }).click(); await expect(answer).toHaveCount(0); expect((await settings()).pending_question).toBe('');
    await command(request, { cmd: 'engineer_ask', group, engineer_id: engineer, question: 'Reply to this live question' }); await expect(answer).toBeVisible(); await answer.fill('Local fixture reply');
    await panel.getByRole('button', { name: 'Reply', exact: true }).click(); await expect(answer).toHaveCount(0); expect((await settings()).pending_question).toBe('');
    await command(request, { cmd: 'engineer_ask', group, engineer_id: engineer, question: 'Retain the reconnect draft' }); await answer.fill('Draft through journal reconciliation'); await answer.focus();
    holdNext = true; const previous = connections; await socket!.close({ code: 1012, reason: 'Activity journal retained refresh' }); await expect.poll(() => connections).toBeGreaterThan(previous); await expect.poll(() => Boolean(release)).toBe(true);
    await expect(initialSummary.locator('..')).toHaveAttribute('open', ''); await expect(answer).toHaveValue('Draft through journal reconciliation'); await expect(answer).toBeFocused();
    await append('Journal appended during stalled refresh');
    await command(request, { cmd: 'engineer_journal_delete', group, author_cell_id: engineer, entry_id: String(initial.id) });
    await expect(initialSummary).toHaveCount(0); await expect(journal.locator('summary').filter({ hasText: 'Journal appended during stalled refresh' })).toBeVisible();
    release!(); release = undefined; await expect(panel.getByText('Refreshing activity…', { exact: true })).toHaveCount(0);
    await expect(initialSummary).toHaveCount(0); await expect(answer).toHaveValue('Draft through journal reconciliation');
    const remove = journal.locator('details').filter({ has: page.locator('summary').filter({ hasText: 'New selected journal entry' }) }); await remove.locator('summary').click(); await remove.getByRole('button', { name: 'Delete', exact: true }).click(); await expect(remove).toHaveCount(0);
    const stored = (await command(request, { cmd: 'engineer_journal_read', group, author_cell_id: engineer, tail: 200 })).entries as Row[];
    expect(stored.some((entry) => entry.entry === 'New selected journal entry' || entry.id === initial.id)).toBe(false);
    expect(writes).toEqual(expect.arrayContaining([{ cmd: 'engineer_dismiss_note', group, engineer_id: engineer }, { cmd: 'engineer_resume', group, engineer_id: engineer }, { cmd: 'engineer_reply', group, answer: 'Local fixture reply' }]));
    await page.screenshot({ path: test.info().outputPath('engineer-activity-live.png'), animations: 'disabled' });
    await writeFile(test.info().outputPath('engineer-activity-evidence.json'), JSON.stringify({ writes, reads, stored }, null, 2));
    await page.reload(); await page.getByRole('tab', { name: 'Activity', exact: true }).click(); await expect(journal.locator('summary').filter({ hasText: 'Journal appended during stalled refresh' })).toBeVisible(); await expect(initialSummary).toHaveCount(0);
  } finally { release?.(); await command(request, { cmd: 'remove_group', group }); }
});

test('Engineer Completed reads actual persisted dispatch history after tasks leave the Board and honors the created-agent filter', async ({ page, request }) => {
  test.setTimeout(75_000); page.setDefaultTimeout(10_000);
  const directory = await realpath(await mkdtemp(join(tmpdir(), 'torque-activity-history-'))); const group = `Activity history ${Date.now()}`;
  await mkdir(join(directory, '.torque', 'actions'), { recursive: true });
  try {
    await command(request, { cmd: 'add_group', group });
    await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: directory, default_agent_template: '', agent_provider: 'generic', agent_boot_command: '/bin/cat', git_worktree: false, notifications: false, agent_idle_timeout: 0 } });
    await command(request, { cmd: 'save_action', group, scope: 'project', name: 'qa/delivery', action: { prompt: 'Local delivery history fixture: {{ TASK }}' } });
    const engineer = String((await command(request, { cmd: 'add_engineer', group, name: 'History Engineer', provider: 'generic', command: '/bin/cat', directory })).id);
    const task = String((await command(request, { cmd: 'board_add_task', group, task: 'Persisted historical delivery', lane: 'Backlog', action_name: 'qa/delivery' })).task_id);
    await command(request, { cmd: 'dispatch_task', id: task, create_agent: true, name: 'Historical delivery worker', _engineer_dispatch_group: group, _engineer_dispatch_id: engineer, _created_by_engineer_id: engineer, owner_engineer_id: engineer });
    const worklog = (await command(request, { cmd: 'engineer_journal_snapshot', group, engineer_id: engineer })).engineer_worklog as Record<string, Row[]>;
    expect(worklog[group]).toEqual(expect.arrayContaining([expect.objectContaining({ task_id: task, task_title: 'Persisted historical delivery', agent_owned: true })]));
    await command(request, { cmd: 'board_remove_task', id: task });
    await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_select_agent', id: engineer }); await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'agents', controlTab: 'mission' } });
    await page.goto('/'); await page.getByRole('tab', { name: 'Activity', exact: true }).click(); await page.getByRole('tab', { name: 'Completed', exact: true }).click();
    const panel = page.getByRole('region', { name: 'Completed deliveries', exact: true }); const entry = panel.locator('details').filter({ hasText: 'Persisted historical delivery' });
    await expect(entry.locator('summary')).toBeVisible(); await expect(entry.locator('summary')).toContainText('Not on board'); await entry.locator('summary').click(); await expect(entry).toContainText('Historical delivery worker'); await expect(entry).toContainText(task);
    await command(request, { cmd: 'engineer_update_settings', group, restrict_to_created_agents: true }); await expect(panel).toContainText('Work sent to Engineer-created agents'); await expect(entry).toBeVisible();
    await page.reload(); await page.getByRole('tab', { name: 'Activity', exact: true }).click(); await page.getByRole('tab', { name: 'Completed', exact: true }).click(); await expect(entry.locator('summary')).toBeVisible(); await entry.locator('summary').click(); await expect(entry).toContainText('Historical delivery worker');
    await page.screenshot({ path: test.info().outputPath('engineer-delivery-history.png'), animations: 'disabled' });
    await writeFile(test.info().outputPath('engineer-delivery-history.json'), JSON.stringify({ task, engineer, worklog }, null, 2));
  } finally { await command(request, { cmd: 'remove_group', group }); await rm(directory, { recursive: true, force: true }); }
});
