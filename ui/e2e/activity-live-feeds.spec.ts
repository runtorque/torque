import { expect, test, type APIRequestContext, type Page, type WebSocketRoute } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data?.type).not.toBe('error'); return result.data;
}
async function setup(request: APIRequestContext, group: string, kind: string, name: string) {
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: '/private/tmp', agent_provider: 'generic', agent_boot_command: '/bin/cat', default_agent_template: '', git_worktree: false, notifications: false } });
  const id = String((await command(request, { cmd: `add_${kind}`, group, name, provider: 'generic', command: '/bin/cat', directory: '/private/tmp' })).id);
  await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_select_agent', id }); await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'agents', controlTab: 'mission' } });
  return id;
}
async function open(page: Page, name: string) { await page.goto('/'); await page.getByRole('tab', { name: 'Activity', exact: true }).click(); return page.getByRole('region', { name: `Activity for ${name}`, exact: true }); }

test('Architect Activity applies actual journal and decision deltas without losing disclosure or fetching unrelated data', async ({ page, request }) => {
  test.setTimeout(60_000); const group = `Architect live ${Date.now()}`; const reads: Row[] = [];
  await page.route('**/api/cmd', async (route) => { const data = route.request().postDataJSON() as Row; if (['architect_journal_read', 'decisions_snapshot'].includes(String(data.cmd))) reads.push(data); await route.continue(); });
  try {
    const id = await setup(request, group, 'architect', 'Live Architect');
    const decision = await command(request, { cmd: 'architect_decision_create', architect_id: id, title: 'Initial live decision', rationale: 'Actual persisted decision' });
    const append = (entry: string) => command(request, { cmd: 'architect_journal_append', architect_id: id, entry_type: 'checkpoint', entry });
    await append('Initial live Architect journal');
    const panel = await open(page, 'Live Architect'); const decisionRow = panel.locator('details').filter({ has: page.getByText('Initial live decision', { exact: true }) });
    await decisionRow.locator('summary').click(); await expect(decisionRow.getByRole('button', { name: 'Accept', exact: true })).toBeVisible();
    const decisionReads = reads.length; await command(request, { cmd: 'architect_decision_update', architect_id: id, id: decision.id, status: 'accepted' });
    await expect(decisionRow.getByRole('button', { name: 'Accept', exact: true })).toHaveCount(0); await expect(decisionRow).toHaveAttribute('open', ''); expect(reads).toHaveLength(decisionReads);
    await command(request, { cmd: 'architect_decision_update', architect_id: id, id: decision.id, archived: true }); await expect(decisionRow).toHaveCount(0); await panel.getByRole('button', { name: 'Show archived (1)', exact: true }).click(); await expect(decisionRow).toBeVisible();
    await panel.getByRole('tab', { name: 'Journal', exact: true }).click(); const initial = panel.locator('details').filter({ has: page.locator('summary').filter({ hasText: 'Initial live Architect journal' }) }); await initial.locator('summary').click(); await initial.locator('summary').focus();
    await expect(panel.getByText('Refreshing activity…', { exact: true })).toHaveCount(0); const journalReads = reads.length; await append('New live Architect journal'); await expect(panel.locator('summary').filter({ hasText: 'New live Architect journal' })).toBeVisible(); await expect(initial).toHaveAttribute('open', ''); await expect(initial.locator('summary')).toBeFocused(); expect(reads).toHaveLength(journalReads);
    await page.screenshot({ path: test.info().outputPath('architect-live-journal.png'), animations: 'disabled' }); await writeFile(test.info().outputPath('architect-live-evidence.json'), JSON.stringify({ reads, decision }, null, 2));
  } finally { await command(request, { cmd: 'remove_group', group }); }
});

test('MCP filters real success/error records, loads older matching calls and applies live scoped calls through reconnect', async ({ page, request }) => {
  test.setTimeout(90_000); page.setDefaultTimeout(12_000); const group = `MCP live ${Date.now()}`; const prefix = `mcp__torque__qa_live_${Date.now()}_`; const reads: Row[] = []; let socket: WebSocketRoute | undefined; let connections = 0;
  await page.routeWebSocket(/\/ws\?/, (client) => { socket = client; connections++; client.connectToServer(); });
  await page.route('**/api/cmd', async (route) => { const data = route.request().postDataJSON() as Row; if (data.cmd === 'mcp_calls') reads.push(data); await route.continue(); });
  try {
    const id = await setup(request, group, 'worker', 'Live MCP Worker');
    const ingest = async (name: string, success: boolean) => { const response = await request.post('/events', { headers: { 'X-Torque-Cell-Id': id }, data: { event_id: `${prefix}${name}`, hook_event_name: 'PostToolUse', tool_name: `${prefix}${name}`, success, tool_input: {}, tool_response: { ok: success } } }); expect(response.status()).toBe(200); };
    await ingest('older_error', false); for (let i = 0; i < 20; i++) await ingest(`success_${i}`, true);
    await expect.poll(async () => ((await command(request, { cmd: 'mcp_calls', cell_id: id, tool_name_pattern: `${prefix}%`, limit: 100 })).calls as Row[]).length).toBe(21);
    const panel = await open(page, 'Live MCP Worker'); await panel.getByRole('tab', { name: 'MCP', exact: true }).click(); await expect(panel.getByText(`${prefix}success_19`, { exact: true })).toBeVisible();
    await panel.getByRole('combobox', { name: /^Outcome/ }).selectOption('error'); await panel.getByRole('button', { name: 'Apply', exact: true }).click();
    await expect(panel.getByText(`${prefix}older_error`, { exact: true })).toBeVisible(); await expect(panel.getByText(`${prefix}success_19`, { exact: true })).toHaveCount(0); expect(Math.max(...reads.map((row) => Number(row.limit)))).toBeGreaterThanOrEqual(40);
    const old = panel.locator('details').filter({ has: page.getByText(`${prefix}older_error`, { exact: true }) }); await old.locator('summary').click();
    const input = panel.getByLabel('Tool contains', { exact: true }); await input.fill('unfinished-filter'); await input.evaluate((node: HTMLInputElement) => node.setSelectionRange(2, 7));
    const count = reads.length; await ingest('live_error', false); await ingest('live_success', true); await expect(panel.getByText(`${prefix}live_error`, { exact: true })).toBeVisible(); await expect(panel.getByText(`${prefix}live_success`, { exact: true })).toHaveCount(0);
    await expect(input).toHaveValue('unfinished-filter'); await expect(input).toBeFocused(); expect(await input.evaluate((node: HTMLInputElement) => [node.selectionStart, node.selectionEnd])).toEqual([2, 7]); await expect(old).toHaveAttribute('open', ''); expect(reads).toHaveLength(count);
    const before = connections; await socket!.close({ code: 1012, reason: 'Live MCP retained filters' }); await expect.poll(() => connections).toBeGreaterThan(before); await expect(panel.getByText('Refreshing activity…', { exact: true })).toHaveCount(0); await expect(old).toHaveAttribute('open', ''); await expect(input).toHaveValue('unfinished-filter'); await expect(panel.getByText(`${prefix}live_error`, { exact: true })).toHaveCount(1); await expect(panel.getByText(`${prefix}live_success`, { exact: true })).toHaveCount(0);
    await page.screenshot({ path: test.info().outputPath('mcp-live-filtered.png'), animations: 'disabled' }); await writeFile(test.info().outputPath('mcp-live-evidence.json'), JSON.stringify({ reads, calls: (await command(request, { cmd: 'mcp_calls', cell_id: id, tool_name_pattern: `${prefix}%`, limit: 100 })).calls }, null, 2));
  } finally { await command(request, { cmd: 'remove_group', group }); }
});

test('Agent Events shows live panel events and refreshes low-level activity only for the visible selected agent', async ({ page, request }) => {
  test.skip(!process.env.TORQUE_NOTIFICATION_EVENT_QA, 'Requires a disposable profile-enabled daemon'); test.setTimeout(60_000);
  const group = `Cell events ${Date.now()}`; const reads: Row[] = [];
  await page.route('**/api/cmd', async (route) => { const data = route.request().postDataJSON() as Row; if (data.cmd === 'get_cell_events') reads.push(data); await route.continue(); });
  try {
    const id = await setup(request, group, 'worker', 'Live Event Worker'); const other = String((await command(request, { cmd: 'add_worker', group, name: 'Other Event Worker' })).id);
    await command(request, { cmd: 'ui_select_agent', id }); const panel = await open(page, 'Live Event Worker'); await expect(panel.getByText('Refreshing activity…', { exact: true })).toHaveCount(0);
    await command(request, { cmd: 'ai_report', cell_id: id, action: 'blocked', message: 'Selected live attention event' }); const live = panel.locator('details').filter({ hasText: 'Selected live attention event' }); await expect(live).toHaveCount(1); await live.locator('summary').click(); await expect(live).toContainText('Selected live attention event');
    const emit = async (cellId: string, detail: string) => { const response = await request.post('/events', { headers: { 'X-Torque-Cell-Id': cellId }, data: { source: 'torque-profile-harness', event_id: `${cellId}-${detail}`, event_type: 'activity_change', data: { activity: 'reading', detail } } }); expect(response.status()).toBe(200); };
    await expect(panel.getByText('Refreshing activity…', { exact: true })).toHaveCount(0); const count = reads.length; await emit(other, 'Unrelated low-level event');
    await expect.poll(async () => (((await command(request, { cmd: 'get_state' })).agents as Record<string, Row>)[other]?.activity_detail)).toBe('Unrelated low-level event'); expect(reads).toHaveLength(count);
    await emit(id, 'Selected low-level event'); await expect(panel.getByText('Selected low-level event', { exact: true })).toHaveCount(1); await expect(live).toHaveAttribute('open', ''); expect(reads.length).toBeGreaterThan(count);
    await panel.getByRole('tab', { name: 'MCP', exact: true }).click(); const hidden = reads.length; await emit(id, 'Hidden low-level event'); await expect.poll(async () => (((await command(request, { cmd: 'get_state' })).agents as Record<string, Row>)[id]?.activity_detail)).toBe('Hidden low-level event'); expect(reads).toHaveLength(hidden);
    await panel.getByRole('tab', { name: 'Events', exact: true }).click(); await expect(panel.getByText('Hidden low-level event', { exact: true })).toHaveCount(1); await page.screenshot({ path: test.info().outputPath('cell-events-live.png'), animations: 'disabled' }); await writeFile(test.info().outputPath('cell-events-evidence.json'), JSON.stringify(reads, null, 2));
  } finally { await command(request, { cmd: 'remove_group', group }); }
});
