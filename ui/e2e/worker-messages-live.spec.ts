import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const response = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(response.ok, response.error).toBe(true); expect(response.data.type).not.toBe('error'); return response.data;
}
test('Worker Messages hydrates persisted compact threads and preserves addressed history and reading state through live updates and reconnect', async ({ page, request }) => {
  test.setTimeout(90_000); page.setDefaultTimeout(12_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Worker messages ${Date.now()}`; const tasks: string[] = []; const reads: Row[] = []; let socket: WebSocketRoute | undefined; let connections = 0; let fail = false;
  await page.routeWebSocket(/\/ws\?/, (client) => { socket = client; connections++; client.connectToServer(); });
  await page.route('**/api/cmd', async (route) => { const data = route.request().postDataJSON() as Row; if (data.cmd === 'task_detail') { reads.push(data); if (fail) { await route.fulfill({ json: { ok: false, error: 'Injected message read failure' } }); return; } } await route.continue(); });
  try {
    await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: '/private/tmp', agent_provider: 'generic', agent_boot_command: '/bin/cat', default_agent_template: '', git_worktree: false, notifications: false } });
    const add = async (name: string) => String((await command(request, { cmd: 'add_worker', group, name, provider: 'generic', command: '/bin/cat', directory: '/private/tmp' })).id);
    const worker = await add('Message Worker'); const other = await add('Other Message Worker');
    const id = String((await command(request, { cmd: 'board_add_task', group, task: 'Persisted inline messages', lane: 'Backlog' })).task_id); tasks.push(id);
    const unrelated = String((await command(request, { cmd: 'board_add_task', group, task: 'Other inline messages', lane: 'Backlog' })).task_id); tasks.push(unrelated);
    const earlier = { content: 'Earlier addressed message', timestamp: 1700000000, recipient_agent_id: worker, sender_agent_id: 'fixture-engineer' };
    const wrong = { ...earlier, content: 'Unrelated recipient message', recipient_agent_id: other };
    // Persist history directly; do not send a message or deliver input to another agent.
    await command(request, { cmd: 'board_update_task', id, agent_id: worker, messages_thread: [earlier, wrong] });
    await command(request, { cmd: 'board_update_task', id: unrelated, agent_id: other, messages_thread: [wrong] });
    expect(((await command(request, { cmd: 'task_detail', id })).task as Row).messages_thread).toHaveLength(2);
    await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_select_agent', id: worker }); await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'agents', controlTab: 'mission' } });
    await page.goto('/'); await page.getByRole('tab', { name: 'Activity', exact: true }).click(); const panel = page.getByRole('region', { name: 'Activity for Message Worker', exact: true });
    expect(reads).toHaveLength(0); await panel.getByRole('tab', { name: 'Messages', exact: true }).click();
    const item = panel.locator('details').filter({ has: page.locator('summary').filter({ hasText: earlier.content }) }); await expect(item).toBeVisible(); expect(reads.map((row) => row.id)).toEqual([id]); await expect(panel.getByText(wrong.content, { exact: true })).toHaveCount(0);
    await item.locator('summary').click(); await item.locator('summary').focus(); await item.evaluate((node) => { node.dataset.retained = 'original-message'; });
    await command(request, { cmd: 'board_update_task', id, agent_id: other }); await expect(item).toBeVisible();
    const newer = { ...earlier, content: 'Newer addressed message', timestamp: 1700000001 };
    await command(request, { cmd: 'board_update_task', id, messages_thread: [earlier, wrong, newer] }); await expect(panel.locator('summary').filter({ hasText: newer.content })).toBeVisible(); await expect(item).toHaveAttribute('data-retained', 'original-message'); await expect(item).toHaveAttribute('open', ''); await expect(item.locator('summary')).toBeFocused();
    const before = connections; await socket!.close({ code: 1012, reason: 'Worker message retention' }); await expect.poll(() => connections).toBeGreaterThan(before); await expect(panel.getByText('Refreshing activity…', { exact: true })).toHaveCount(0); await expect(item).toHaveAttribute('open', ''); await expect(item).toHaveAttribute('data-retained', 'original-message');
    const unrelatedCount = reads.length; await command(request, { cmd: 'board_update_task', id: unrelated, messages_thread: [wrong, { ...wrong, timestamp: 1700000002 }] });
    await expect.poll(async () => (((await command(request, { cmd: 'task_detail', id: unrelated })).task as Row).messages_thread as Row[]).length).toBe(2); expect(reads).toHaveLength(unrelatedCount);
    fail = true; await panel.getByRole('button', { name: 'Refresh', exact: true }).click(); await expect(panel.getByRole('alert')).toContainText('Injected message read failure'); await expect(item).toBeVisible(); fail = false; await panel.getByRole('button', { name: 'Retry activity', exact: true }).click(); await expect(panel.getByRole('alert')).toHaveCount(0);
    await panel.getByRole('tab', { name: 'Events', exact: true }).click(); const hidden = reads.length; const last = { ...earlier, timestamp: 1700000003, content: 'Arrived while Messages hidden' }; await command(request, { cmd: 'board_update_task', id, messages_thread: [earlier, wrong, newer, last] });
    await expect.poll(async () => (((await command(request, { cmd: 'task_detail', id })).task as Row).messages_thread as Row[]).length).toBe(4); expect(reads).toHaveLength(hidden);
    await panel.getByRole('tab', { name: 'Messages', exact: true }).click(); await expect(panel.locator('summary').filter({ hasText: last.content })).toBeVisible();
    await page.screenshot({ path: test.info().outputPath('worker-messages.png'), animations: 'disabled' }); await writeFile(test.info().outputPath('worker-messages-evidence.json'), JSON.stringify({ reads, persisted: (await command(request, { cmd: 'task_detail', id })).task }, null, 2));
    await command(request, { cmd: 'board_update_task', id, messages_thread: [] }); await expect(panel.getByText('No messages yet', { exact: true })).toBeVisible();
  } finally { for (const id of tasks) await command(request, { cmd: 'board_remove_task', id }); await command(request, { cmd: 'remove_group', group }); }
});
