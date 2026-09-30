import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) { const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row }; expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data; }
test('selected Architect peer reads follow matching deltas, retain selection through reconnect and honor empty results without duplicate messages', async ({ page, request }) => {
  test.setTimeout(60_000); page.setDefaultTimeout(12_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime; expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Architect messages ${Date.now()}`; const tasks: string[] = []; let id = ''; let socket: WebSocketRoute | undefined; let connections = 0; let pendingOps: Row[] = []; const reads: Row[] = []; const outbound: string[] = [];
  let threads: Row[] = []; let pairs: Record<string, Row> = {}; let messages: Row[] = [];
  // Read-only peer fixtures use the server's logical-inbox and canonical-pair
  // shapes. The daemon, WS sequence, reconnect and mounted UI remain real.
  await page.route('**/api/cmd', async (route) => { const data = route.request().postDataJSON() as Row; outbound.push(String(data.cmd)); if (data.cmd === 'architect_peer_inbox') { reads.push(data); await route.fulfill({ json: { ok: true, data: { type: 'architect_peer_inbox', architect_id: id, detail: 'full', threads, threads_total: threads.length } } }); return; } await route.continue(); });
  await page.routeWebSocket(/\/ws\?/, (client) => { socket = client; connections++; const server = client.connectToServer(); server.onMessage((raw) => { const frame = JSON.parse(String(raw)) as Row;
    if (frame.type === 'state') { frame.agent_peer_threads = pairs; const agents = frame.agents as Record<string, Row>; if (agents[id]) agents[id] = { ...agents[id], mcp_messages: messages }; }
    if (frame.type === 'delta' && pendingOps.length) { frame.ops = [...frame.ops as Row[], ...pendingOps]; pendingOps = []; }
    client.send(JSON.stringify(frame));
  }); client.onMessage((raw) => { const frame = JSON.parse(String(raw)) as Row; if (typeof frame.cmd === 'string') outbound.push(frame.cmd); server.send(raw); }); });
  const trigger = async (ops: Row[]) => { pendingOps = ops; tasks.push(String((await command(request, { cmd: 'board_add_task', group, task: `Peer fixture tick ${tasks.length}`, lane: 'Backlog' })).task_id)); await expect.poll(() => pendingOps.length).toBe(0); };
  try {
    await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: '/private/tmp', agent_provider: 'generic', agent_boot_command: '/bin/cat', default_agent_template: '', git_worktree: false, notifications: false } });
    id = String((await command(request, { cmd: 'add_architect', group, name: 'Peer Architect QA', provider: 'generic', command: '/bin/cat', directory: '/private/tmp' })).id);
    const first = { id: 'first-message', thread_id: 'logical-first', message: 'First peer history', sender_id: 'peer-a', recipient_id: id, sender_kind: 'architect', recipient_kind: 'architect', timestamp: 20 };
    const selected = { ...first, id: 'selected-message', thread_id: 'logical-selected', sender_id: 'peer-b', message: 'Selected peer history', timestamp: 10 };
    messages = [first, selected]; threads = [{ thread_id: 'logical-first', peer_name: 'First peer', last_message_at: 20, messages: [first] }, { thread_id: 'logical-selected', peer_name: 'Selected peer', last_message_at: 10, messages: [selected] }];
    pairs = { first: { thread_id: 'first', participant_ids: [id, 'peer-a'], messages: [first] }, selected: { thread_id: 'selected', participant_ids: [id, 'peer-b'], messages: [selected] } };
    await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_select_agent', id }); await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'agents', controlTab: 'mission' } });
    await page.goto('/'); await page.getByRole('tab', { name: 'Activity', exact: true }).click(); const panel = page.getByRole('region', { name: 'Activity for Peer Architect QA', exact: true }); await panel.getByRole('tab', { name: 'Peer chat', exact: true }).click();
    const button = panel.getByRole('button', { name: /^Selected peer/ }); await button.click(); await button.focus(); const count = reads.length;
    await trigger([{ op: 'agent_peer_thread_upsert', thread: { thread_id: 'unrelated', participant_ids: ['x', 'y'], messages: [] } }]); expect(reads).toHaveLength(count);
    const newer = { ...selected, id: 'new-message', timestamp: 30, message: 'Live selected peer history' }; threads = [threads[0]!, { ...threads[1], last_message_at: 30, messages: [selected, newer] }]; pairs.selected = { ...pairs.selected, messages: [selected, newer] };
    await trigger([{ op: 'agent_peer_thread_upsert', thread: pairs.selected }]); await expect(panel.getByText(newer.message, { exact: true })).toHaveCount(2); await expect(button).toHaveAttribute('aria-pressed', 'true'); await expect(button).toBeFocused(); expect(reads).toHaveLength(count + 1);
    const before = connections; await socket!.close({ code: 1012, reason: 'Architect peer refresh' }); await expect.poll(() => connections).toBeGreaterThan(before); await expect(panel.getByText('Refreshing activity…', { exact: true })).toHaveCount(0); await expect(button).toHaveAttribute('aria-pressed', 'true');
    await panel.getByRole('tab', { name: 'Messages', exact: true }).click(); await expect(panel.getByText('Refreshing activity…', { exact: true })).toHaveCount(0); await expect(panel.locator('summary').filter({ has: page.getByText(selected.message, { exact: true }) })).toHaveCount(1); await expect(panel.locator('summary').filter({ hasText: first.message })).toHaveCount(1); await expect(panel.locator('summary').filter({ hasText: newer.message })).toHaveCount(1);
    await panel.getByRole('tab', { name: 'Peer chat', exact: true }).click(); await expect(button).toHaveAttribute('aria-pressed', 'true'); await page.screenshot({ path: test.info().outputPath('architect-peer-messages.png'), animations: 'disabled' });
    threads = []; await panel.getByRole('button', { name: 'Refresh', exact: true }).click(); await expect(panel.getByText('No peer threads', { exact: true })).toBeVisible();
    await panel.getByRole('tab', { name: 'Events', exact: true }).click(); const hidden = reads.length; await trigger([{ op: 'agent_peer_thread_remove', thread_id: 'selected' }]); expect(reads).toHaveLength(hidden); expect(outbound).not.toContain('architect_peer_message');
    await writeFile(test.info().outputPath('architect-peer-evidence.json'), JSON.stringify({ reads, outbound, fixturePairs: pairs }, null, 2));
  } finally { for (const task of tasks) await command(request, { cmd: 'board_remove_task', id: task }); await command(request, { cmd: 'remove_group', group }); }
});
