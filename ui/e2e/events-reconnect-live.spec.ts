import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}
test('Event Activity reads only while visible and refreshes real history without losing filters or selection', async ({ page, request }) => {
  test.setTimeout(45_000); page.setDefaultTimeout(10_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Events reconnect ${Date.now()}`; const marker = `qa-event-${Date.now()}`; let id = '';
  const reads: Row[] = []; let socket: WebSocketRoute | undefined; let connections = 0; let eventPages = 0;
  await page.routeWebSocket(/\/ws\?/, (client) => {
    socket = client; connections++; const server = client.connectToServer();
    client.onMessage((raw) => { const data = JSON.parse(String(raw)) as Row; if (data.cmd === 'get_events') reads.push(data); server.send(raw); });
    server.onMessage((raw) => { if ((JSON.parse(String(raw)) as Row).type === 'events_page') eventPages++; client.send(raw); });
  });
  try {
    await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: '/private/tmp', agent_provider: 'generic', agent_boot_command: '/bin/cat', default_agent_template: '', git_worktree: false, notifications: false } });
    id = String((await command(request, { cmd: 'add_worker', group, name: 'Event QA Worker' })).id);
    await command(request, { cmd: 'ai_report', cell_id: id, action: 'blocked', message: `${marker} original` });
    await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'board', controlTab: 'activity' } });
    await page.goto('/'); await expect(page.getByRole('button', { name: '＋ New task', exact: true })).toBeVisible(); expect(reads).toHaveLength(0);
    await page.getByRole('button', { name: /◎ Control/ }).click(); await expect.poll(() => eventPages).toBe(1); expect(reads).toHaveLength(1);
    const search = page.getByLabel('Search events', { exact: true }); await search.fill(marker);
    const kind = page.getByRole('combobox', { name: 'Kind', exact: true }); await kind.selectOption('agent_blocked');
    const event = page.locator('article').filter({ has: page.locator('strong').filter({ hasText: `${marker} original` }) });
    await event.click(); await expect(event).toHaveAttribute('aria-current', 'true');
    await search.focus(); await search.evaluate((node: HTMLInputElement) => { node.setSelectionRange(1, 6); node.dataset.eventAnchor = 'retained'; });
    const before = connections; await socket!.close({ code: 1012, reason: 'Visible Event reconnect' });
    await command(request, { cmd: 'ai_report', cell_id: id, action: 'blocked', message: `${marker} reconnected` });
    await expect.poll(() => connections).toBeGreaterThan(before); await expect.poll(() => eventPages).toBe(2); expect(reads).toHaveLength(2);
    await expect(page.locator('strong').filter({ hasText: `${marker} reconnected` })).toBeVisible(); await expect(event).toHaveAttribute('aria-current', 'true');
    await expect(search).toBeFocused(); await expect(search).toHaveValue(marker); await expect(search).toHaveAttribute('data-event-anchor', 'retained'); expect(await search.evaluate((node: HTMLInputElement) => [node.selectionStart, node.selectionEnd])).toEqual([1, 6]); await expect(kind).toHaveValue('agent_blocked');
    await page.screenshot({ animations: 'disabled', path: test.info().outputPath('events-reconnected.png') });
    await page.getByRole('button', { name: /▦ Board/ }).click(); const hidden = connections; await socket!.close({ code: 1012, reason: 'Hidden Event reconnect' }); await expect.poll(() => connections).toBeGreaterThan(hidden);
    await expect(page.getByRole('button', { name: '＋ New task', exact: true })).toBeVisible(); expect(reads).toHaveLength(2); await expect(search).toHaveCount(0);
  } finally { if (id) { await command(request, { cmd: 'remove_agent', id }); await command(request, { cmd: 'purge_agent_now', id }); } }
});
