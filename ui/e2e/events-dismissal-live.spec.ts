import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}

test('Events dismisses only reviewed attention, persists through reconnect, and resurfaces a newer agent alert', async ({ page, request }) => {
  test.setTimeout(60_000); page.setDefaultTimeout(12_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Attention dismissal ${Date.now()}`; const reviewed = `Reviewed blocker ${Date.now()}`; const writes: Row[] = []; let ask = '';
  let socket: WebSocketRoute | undefined; let connections = 0;
  await page.routeWebSocket(/\/ws\?/, (client) => {
    socket = client; connections++; const server = client.connectToServer();
    client.onMessage((raw) => { const data = JSON.parse(String(raw)) as Row; if (data.cmd === 'events_dismiss') writes.push(data); server.send(raw); });
    server.onMessage((raw) => client.send(raw));
  });
  const state = () => command(request, { cmd: 'get_state' });
  try {
    await command(request, { cmd: 'add_group', group });
    await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: '/private/tmp', default_agent_template: '', agent_provider: 'generic', agent_boot_command: '/bin/cat', git_worktree: false, notifications: false } });
    const worker = String((await command(request, { cmd: 'add_worker', group, name: 'Dismissal Worker' })).id);
    const other = String((await command(request, { cmd: 'add_worker', group, name: 'Retained Worker' })).id);
    await command(request, { cmd: 'ai_report', cell_id: worker, action: 'blocked', message: reviewed });
    await command(request, { cmd: 'ai_report', cell_id: other, action: 'blocked', message: 'Separate blocker' });
    ask = String((await command(request, { cmd: 'board_add_task', group, task: 'Dismissal question', description: 'Keep this task pending', lane: 'Backlog', labels: ['torque:human'] })).task_id);
    const before = await state(); const workerStamp = Number(((before.agents as Row)[worker] as Row).last_event_at);
    const askStamp = Date.parse(String(((before.board_tasks as Row)[ask] as Row).created_at)) / 1000;
    expect((before.agents as Row)[worker]).toMatchObject({ error_message: '', activity_detail: reviewed });
    expect(workerStamp).toBeGreaterThan(0); expect(askStamp).toBeGreaterThan(0);
    await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'activity' } });
    await page.goto('/'); const attention = page.getByRole('region', { name: 'Attention requests', exact: true });
    const workerCard = attention.getByRole('article', { name: 'Attention for Dismissal Worker', exact: true });
    const otherCard = attention.getByRole('article', { name: 'Attention for Retained Worker', exact: true });
    const askCard = attention.getByRole('article', { name: 'Attention for Dismissal question', exact: true });
    await expect(workerCard).toContainText(reviewed); await expect(otherCard).toBeVisible(); await expect(askCard).toBeVisible();
    const search = page.getByRole('textbox', { name: 'Search events', exact: true }); await search.fill(reviewed);
    const history = page.locator('article').filter({ has: page.locator('strong').filter({ hasText: reviewed }) }); await history.click(); await expect(history).toHaveAttribute('aria-current', 'true');
    await workerCard.getByRole('button', { name: 'Dismiss attention for Dismissal Worker', exact: true }).click();
    await expect(workerCard).toHaveCount(0); await expect(otherCard).toBeVisible(); await expect(askCard).toBeVisible();
    await expect(search).toHaveValue(reviewed); await expect(history).toHaveAttribute('aria-current', 'true'); await expect(history.getByRole('button', { name: 'Copy event', exact: true })).toBeVisible();
    await askCard.getByRole('button', { name: 'Dismiss attention for Dismissal question', exact: true }).click(); await expect(askCard).toHaveCount(0);
    const dismissed = await state(); expect(dismissed.events_dismissed_attention).toMatchObject({ [worker]: workerStamp, [ask]: askStamp });
    expect((dismissed.agents as Row)[worker]).toMatchObject({ needs_attention: true }); expect((dismissed.board_tasks as Row)[ask]).toMatchObject({ lane: 'Backlog' });
    const previous = connections; await socket!.close({ code: 1012, reason: 'Persisted attention dismissal' }); await expect.poll(() => connections).toBeGreaterThan(previous);
    await expect(workerCard).toHaveCount(0); await expect(askCard).toHaveCount(0); await expect(otherCard).toBeVisible();
    await page.reload(); await expect(otherCard).toBeVisible(); await expect(workerCard).toHaveCount(0); await expect(askCard).toHaveCount(0);
    await command(request, { cmd: 'ai_report', cell_id: worker, action: 'blocked', message: 'New blocker after dismissal' });
    await expect(workerCard).toContainText('New blocker after dismissal'); await expect(otherCard).toBeVisible(); await expect(askCard).toHaveCount(0);
    const final = await state(); expect(Number(((final.agents as Row)[worker] as Row).last_event_at)).toBeGreaterThan(workerStamp);
    expect(writes).toEqual([{ cmd: 'events_dismiss', id: worker, timestamp: workerStamp }, { cmd: 'events_dismiss', id: ask, timestamp: askStamp }]);
    await page.screenshot({ animations: 'disabled', path: test.info().outputPath('events-new-attention.png') });
    const path = test.info().outputPath('events-dismissal-evidence.json'); await writeFile(path, JSON.stringify({ writes, saved: final.events_dismissed_attention }, null, 2)); await test.info().attach('events-dismissal-evidence', { path, contentType: 'application/json' });
  } finally { if (ask) await command(request, { cmd: 'board_remove_task', id: ask }); await command(request, { cmd: 'remove_group', group }); }
});
