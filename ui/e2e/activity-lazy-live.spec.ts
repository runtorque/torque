import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const response = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(response.ok, response.error).toBe(true); expect(response.data.type).not.toBe('error'); return response.data;
}
test('Activity reads only the visible role tab and retains MCP reading state through reconnect and retry', async ({ page, request }) => {
  test.setTimeout(90_000); page.setDefaultTimeout(15_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Activity loading ${Date.now()}`; const created: string[] = [];
  const reads: Row[] = []; const scoped = new Set(['get_cell_events', 'mcp_calls', 'get_agent_history_detail', 'agent_class_list', 'agent_class_status', 'agent_class_audit', 'architect_journal_read', 'decisions_snapshot', 'architect_peer_inbox', 'engineer_journal_snapshot', 'engineer_session_map_read', 'get_group_settings']);
  let capture = false; let refusal = false; let socket: WebSocketRoute | undefined; let connections = 0;
  await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections++; });
  await page.route('**/api/cmd', async (route) => { const data = route.request().postDataJSON() as Row; if (capture && scoped.has(String(data.cmd))) reads.push(data); if (data.cmd === 'mcp_calls' && refusal) { refusal = false; await route.fulfill({ json: { ok: false, error: 'QA Activity refresh refused' } }); } else await route.continue(); });
  const reconnect = async () => { const before = connections; await socket!.close({ code: 1012, reason: 'Activity read continuity' }); await expect.poll(() => connections).toBeGreaterThan(before); };
  const kinds = (from: number) => reads.slice(from).map((entry) => String(entry.cmd));
  const settled = async () => { await expect(page.getByText('Refreshing activity…', { exact: true })).toHaveCount(0); };
  try {
    await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: '/private/tmp', git_worktree: false } }); await command(request, { cmd: 'ui_select_group', group });
    const frame = await command(request, { cmd: 'add_agent', group, name: 'Lazy Worker', provider: 'generic', command: '/bin/cat', directory: '/private/tmp', shell: '/bin/sh', worktree: false });
    const worker = String(Object.values(frame.agents as Record<string, Row>).find((row) => row.group === group && row.name === 'Lazy Worker')!.id); created.push(worker);
    const engineer = String((await command(request, { cmd: 'add_engineer', group, name: 'Lazy Engineer', provider: 'generic', command: '/bin/cat', directory: '/private/tmp', worktree: false })).id); created.push(engineer);
    const architect = String((await command(request, { cmd: 'add_architect', group, name: 'Lazy Architect', provider: 'generic', command: '/bin/cat', directory: '/private/tmp', worktree: false })).id); created.push(architect);
    const prefix = `mcp__torque__qa_activity_${Date.now()}_`;
    for (let index = 0; index < 45; index++) {
      const response = await request.post('/events', { headers: { 'X-Torque-Cell-Id': worker }, data: { event_id: `${prefix}${index}`, hook_event_name: 'PostToolUse', tool_name: `${prefix}${index}`, tool_input: { index }, tool_response: { ok: true } } }); expect(response.status()).toBe(200);
    }
    await expect.poll(async () => ((await command(request, { cmd: 'mcp_calls', cell_id: worker, tool_name_pattern: `${prefix}%`, limit: 100 })).calls as Row[]).length).toBe(45);
    await command(request, { cmd: 'ui_select_agent', id: worker }); await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'agents', controlTab: 'mission' } });
    await page.goto('/'); await page.locator(`[role="treeitem"][data-agent-id="${worker}"]`).click();
    capture = true; await page.getByRole('tablist', { name: 'View for Lazy Worker' }).getByRole('tab', { name: 'Activity' }).click(); await expect.poll(() => reads.length).toBeGreaterThan(0); await settled(); expect(kinds(0)).toEqual(['get_cell_events']);
    let start = reads.length; await page.getByRole('region', { name: 'Activity for Lazy Worker' }).getByRole('tab', { name: 'MCP', exact: true }).click(); await expect.poll(() => reads.length).toBeGreaterThan(start); await settled(); expect(new Set(kinds(start))).toEqual(new Set(['mcp_calls']));
    const activity = page.getByRole('region', { name: 'Activity for Lazy Worker' });
    await activity.locator('[data-activity-scroll]').evaluate((node) => { node.scrollTop = node.scrollHeight; });
    await expect.poll(() => Math.max(...reads.filter((entry) => entry.cmd === 'mcp_calls').map((entry) => Number(entry.limit)))).toBeGreaterThanOrEqual(40); await settled();
    const call = activity.locator('details').filter({ has: page.getByText(`${prefix}44`, { exact: true }) }); await call.locator('summary').click(); await expect(call).toHaveAttribute('open', '');
    const tool = activity.getByLabel('Tool contains'); await tool.fill('unfinished-filter'); await tool.focus(); await tool.evaluate((node) => { const input = node as HTMLInputElement; input.setSelectionRange(2, 8); input.dataset.retained = 'yes'; });
    const limit = reads.filter((entry) => entry.cmd === 'mcp_calls').at(-1)!.limit; start = reads.length; refusal = true; await reconnect(); await expect(activity.getByRole('alert')).toContainText('QA Activity refresh refused');
    expect(kinds(start)).toEqual(['mcp_calls']); expect(reads.at(-1)).toMatchObject({ cell_id: worker, limit, tool_name_pattern: 'mcp__torque__%' }); await expect(call).toHaveAttribute('open', ''); await expect(tool).toBeFocused(); await expect(tool).toHaveAttribute('data-retained', 'yes'); expect(await tool.evaluate((node) => [(node as HTMLInputElement).selectionStart, (node as HTMLInputElement).selectionEnd])).toEqual([2, 8]);
    await activity.getByRole('button', { name: 'Retry activity' }).click(); await expect(activity.getByRole('alert')).toHaveCount(0); await settled(); await expect(call).toHaveAttribute('open', ''); await expect(tool).toHaveValue('unfinished-filter');
    await activity.getByRole('button', { name: 'Apply', exact: true }).click(); await settled(); await expect(activity.getByText('No matching MCP calls', { exact: true })).toBeVisible(); expect(reads.at(-1)).toMatchObject({ tool_name_pattern: '*unfinished-filter*', limit: 20 });
    start = reads.length; await activity.getByRole('tab', { name: 'History', exact: true }).click(); await expect.poll(() => reads.length).toBeGreaterThan(start); await settled(); expect(kinds(start)).toEqual(['get_agent_history_detail']);
    start = reads.length; await activity.getByRole('tab', { name: 'Agent Class', exact: true }).click(); await expect.poll(() => reads.length).toBeGreaterThan(start); await settled(); expect(kinds(start).sort()).toEqual(['agent_class_audit', 'agent_class_list', 'agent_class_status']);
    start = reads.length; await page.locator(`[role="treeitem"][data-agent-id="${engineer}"]`).click(); await expect(page.getByRole('region', { name: 'Activity for Lazy Engineer' })).toBeVisible(); await settled(); expect(kinds(start).sort()).toEqual(['engineer_journal_snapshot', 'engineer_session_map_read', 'get_group_settings']);
    start = reads.length; await page.getByRole('region', { name: 'Activity for Lazy Engineer' }).getByRole('tab', { name: 'Queued', exact: true }).click(); await reconnect(); await page.waitForTimeout(300); expect(kinds(start)).toEqual([]);
    start = reads.length; await page.locator(`[role="treeitem"][data-agent-id="${architect}"]`).click(); await expect(page.getByRole('region', { name: 'Activity for Lazy Architect' })).toBeVisible(); await settled(); expect(kinds(start)).toEqual(['decisions_snapshot']);
    const architectPanel = page.getByRole('region', { name: 'Activity for Lazy Architect' }); start = reads.length; await architectPanel.getByRole('tab', { name: 'Journal', exact: true }).click(); await settled(); expect(kinds(start)).toEqual(['architect_journal_read']);
    start = reads.length; await architectPanel.getByRole('tab', { name: 'Peer chat', exact: true }).click(); await settled(); expect(kinds(start)).toEqual(['architect_peer_inbox']); await expect(architectPanel.getByRole('alert')).toHaveCount(0);
    await page.screenshot({ path: test.info().outputPath('activity-lazy.png') });
    await page.getByRole('button', { name: /▦ Board/ }).click(); start = reads.length; await reconnect(); await page.waitForTimeout(500); expect(kinds(start)).toEqual([]);
  } finally { for (const id of created.reverse()) await command(request, { cmd: 'remove_agent', id }); }
});
