import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) { const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row }; expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data; }
test('workspace status follows actual tasks and live operational projections while retaining editor drafts and narrow-screen access', async ({ page, request }) => {
  test.setTimeout(60_000); const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime; expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const original = (await command(request, { cmd: 'get_global_settings' })).settings as Row;
  const group = `Status signals ${Date.now()}`; const prefix = `status-${Date.now()}`; const tasks: string[] = []; let ids: string[] = []; let socket: WebSocketRoute | undefined; let connections = 0; let pendingOps: Row[] = []; let used = 91; let metricsEnabled = true;
  const usage = () => ({ five_hour: { available: true, used_percentage: used, resets_at: Date.now() / 1000 + 3600 }, seven_day: { available: true, used_percentage: 35 } });
  const fixtureAgents = () => ({
    [ids[0]!]: { id: ids[0], name: 'Starting receiver', kind: 'worker', cell_type: 'agent', group, status: 'starting', last_event_at: 10, agent_type: 'codex', provider_usage: { five_hour: { available: true, used_percentage: 5 } } },
    [ids[1]!]: { id: ids[1], name: 'Idle receiver', kind: 'worker', cell_type: 'agent', group, status: 'idle', last_event_at: 20, agent_type: 'claude-code', provider_usage: { five_hour: { available: true, used_percentage: 70 } } },
    [ids[2]!]: { id: ids[2], name: 'Error receiver', kind: 'worker', cell_type: 'agent', group, status: 'running', error_message: 'QA review required', needs_attention: true, last_event_at: 30 },
    'status-terminal': { id: 'status-terminal', name: 'Excluded terminal', cell_type: 'terminal', kind: 'terminal', group, status: 'running' },
    'status-dismissed': { id: 'status-dismissed', name: 'Dismissed', cell_type: 'agent', group, status: 'idle', dismissed_at: 1 },
    'account-source': { id: 'account-source', name: 'Account quota source', kind: 'worker', cell_type: 'agent', group: 'Other group', status: 'idle', agent_type: 'codex', last_event_at: 100, provider_usage: usage() },
  });
  const metrics = () => ({ type: 'metrics_tick', enabled: metricsEnabled, perf: { event_loop_lag_ms: { p95: 60 }, proc: { rss_mb: 123, cpu_pct: 80 }, frontend: { render_per_s: 4, render_ms_p95: 3 } } });
  await page.routeWebSocket(/\/ws\?/, (client) => { socket = client; connections++; const server = client.connectToServer(); server.onMessage((raw) => {
    const frame = JSON.parse(String(raw)) as Row;
    if (frame.type === 'state') { frame.agents = { ...frame.agents as Row, ...fixtureAgents() }; frame.operator_notice_summary = { unread_total: 99 }; frame.runtime = { ...frame.runtime as Row, supervisor: { state: 'degraded', connected: false, session_count: 3, last_op_latency_ms: 25, reconnect_count: 4, supervisor_pid: 123 } }; }
    if (frame.type === 'delta') for (const op of frame.ops as Row[]) if (op.op === 'runtime') op.supervisor = { state: 'degraded', connected: false, session_count: 3, last_op_latency_ms: 25, reconnect_count: 4, supervisor_pid: 123 };
    if (frame.type === 'delta' && pendingOps.length) { frame.ops = [...frame.ops as Row[], ...pendingOps]; pendingOps = []; }
    if (frame.type === 'metrics_tick') { client.send(JSON.stringify(metrics())); return; }
    client.send(JSON.stringify(frame)); if (frame.type === 'state') client.send(JSON.stringify(metrics()));
  }); });
  const trigger = async (ops: Row[]) => { pendingOps = ops; tasks.push(String((await command(request, { cmd: 'board_add_task', group, task: `Unassigned status tick ${tasks.length}`, lane: 'Backlog' })).task_id)); await expect.poll(() => pendingOps.length).toBe(0); };
  const footer = page.locator('footer[aria-label="Workspace status"]');
  try {
    await command(request, { cmd: 'add_group', group });
    const synthetic = await (await request.post('/api/profile/synthetic_agents', { data: { group, prefix, count: 3, directory: '/private/tmp', agent_type: 'generic' } })).json() as { ok: boolean; data: { agent_ids: string[] } }; expect(synthetic.ok).toBe(true); ids = synthetic.data.agent_ids;
    const add = async (task: string, extra: Row = {}) => { const id = String((await command(request, { cmd: 'board_add_task', group, task, lane: 'Backlog', ...extra })).task_id); tasks.push(id); return id; };
    const assigned = await add('Assigned status task', { agent_id: ids[0], description: 'Saved status description' }); await add('Unassigned backlog task'); await add('Completed assigned task', { agent_id: ids[0], lane: 'Done' }); const ask = await add('Pending status ask', { labels: ['torque:human'] });
    await command(request, { cmd: 'update_global_settings', settings: { status_bar_visibility: Object.fromEntries(['daemon_status', 'claude_usage', 'codex_usage', 'health', 'workload', 'tasks', 'attention'].map((key) => [key, true])) } });
    await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'board', controlTab: 'mission' } }); await page.goto('/');
    await expect(footer).toContainText('Agents 1 run / 1 idle / 1 err'); await expect(footer.getByRole('button', { name: 'Tasks 1 active' })).toBeVisible(); await expect(footer.getByRole('button', { name: 'Attention 2' })).toBeVisible();
    await expect(footer.getByText('Codex 5h 91% · 7d 35%', { exact: true })).toHaveAttribute('title', /Account quota source/); await expect(footer.getByText('Claude 5h 70%', { exact: true })).toBeVisible(); await expect(footer.getByText('Supervisor degraded')).toHaveAttribute('title', /Sessions: 3/); await expect(footer.getByRole('button', { name: 'Lag 60.0ms · Mem 123MB' })).toHaveAttribute('title', /Frontend renders: 4.0\/s/);
    await page.getByText('Assigned status task', { exact: true }).dblclick(); const dialog = page.getByRole('dialog', { name: 'Assigned status task', exact: true }); const draft = dialog.getByRole('textbox', { name: 'Description', exact: true }); await draft.fill('Retained while status changes'); await draft.focus(); await draft.evaluate((node: HTMLTextAreaElement) => { node.dataset.owner = 'same'; node.setSelectionRange(2, 8); });
    used = 20; await trigger([{ op: 'agent_upsert', ...fixtureAgents()['account-source'] }]); await expect(footer.getByText('Codex 5h 20% · 7d 35%', { exact: true })).toBeAttached(); await expect(draft).toBeFocused(); expect(await draft.evaluate((node: HTMLTextAreaElement) => [node.selectionStart, node.selectionEnd])).toEqual([2, 8]);
    const before = connections; await socket!.close({ code: 1012, reason: 'Workspace status draft reconnect' }); await expect.poll(() => connections).toBeGreaterThan(before); await expect(draft).toHaveValue('Retained while status changes'); await expect(draft).toHaveAttribute('data-owner', 'same'); await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
    const askDetail = (await command(request, { cmd: 'task_detail', id: ask })).task as Row; await command(request, { cmd: 'events_dismiss', id: ask, timestamp: Date.parse(String(askDetail.created_at)) / 1000 }); await expect(footer.getByRole('button', { name: 'Attention 1' })).toBeVisible();
    await footer.getByRole('button', { name: 'Attention 1' }).click(); await expect(page.getByRole('region', { name: 'Attention requests', exact: true })).toBeVisible();
    await footer.getByRole('button', { name: 'Tasks 1 active' }).click(); await expect(page.getByRole('heading', { name: 'Board', exact: true })).toBeVisible();
    await footer.getByRole('button', { name: 'Lag 60.0ms · Mem 123MB' }).click(); await expect(page.getByRole('region', { name: 'Live performance', exact: true })).toBeVisible();
    metricsEnabled = false; socket!.send(JSON.stringify(metrics())); await expect(footer.getByRole('button', { name: 'Metrics off' })).toBeVisible();
    await page.setViewportSize({ width: 760, height: 600 }); await footer.getByText('Codex 5h 20% · 7d 35%', { exact: true }).focus(); await expect(footer.getByText('Codex 5h 20% · 7d 35%', { exact: true })).toBeInViewport(); await footer.getByText('Supervisor degraded').focus(); await expect(footer.getByText('Supervisor degraded')).toBeInViewport();
    await page.screenshot({ path: test.info().outputPath('workspace-status-narrow.png'), animations: 'disabled' }); await page.setViewportSize({ width: 1600, height: 900 }); await page.screenshot({ path: test.info().outputPath('workspace-status-full.png'), animations: 'disabled' });
    await writeFile(test.info().outputPath('workspace-status-evidence.json'), JSON.stringify({ assigned, ask, ids, fixtureAgents: fixtureAgents(), metrics: metrics(), status: await footer.innerText() }, null, 2));
  } finally { for (const id of tasks) await command(request, { cmd: 'board_remove_task', id }); await request.post('/api/profile/synthetic_agents', { data: { group, prefix, count: 0 } }); await command(request, { cmd: 'remove_group', group }); await command(request, { cmd: 'update_global_settings', settings: { status_bar_visibility: original.status_bar_visibility } }); }
});
