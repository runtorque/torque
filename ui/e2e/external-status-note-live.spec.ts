import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}

test('external status includes its optional note and retains both drafts through refresh, reconnect and provider refusal', async ({ page, request }) => {
  test.setTimeout(45_000); page.setDefaultTimeout(10_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `External note ${Date.now()}`; const title = `Status note ${Date.now()}`; let id = '';
  const sent: Row[] = []; let socket: WebSocketRoute | undefined; let connections = 0;
  // Intercept every provider write before it reaches a daemon or hosted service.
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (String(data.cmd).startsWith('external_')) { await route.fulfill({ json: { ok: false, error: 'Unexpected HTTP provider command' } }); return; }
    await route.continue();
  });
  await page.routeWebSocket(/\/ws\?/, (client) => {
    socket = client; connections++; const server = client.connectToServer();
    client.onMessage((raw) => {
      const data = JSON.parse(String(raw)) as Row;
      if (String(data.cmd).startsWith('external_')) {
        sent.push(data);
        client.send(JSON.stringify(sent.length === 1 ? { type: 'error', message: 'Local provider fixture refused this status' } : { type: 'external_status_pushed', task_id: id, message: 'Local status fixture accepted' }));
        return;
      }
      server.send(raw);
    });
    server.onMessage((raw) => client.send(raw));
  });
  try {
    await command(request, { cmd: 'add_group', group });
    await command(request, { cmd: 'update_group_settings', group, settings: { board_sync_enabled: false, board_sync_provider: '' } });
    id = String((await command(request, { cmd: 'board_add_task', group, task: title, lane: 'Backlog', provider: 'qa-local', external_id: 'LOCAL-1', external_url: 'https://example.invalid/LOCAL-1', board_sync: { version: 1, enabled: false } })).task_id);
    await page.goto('/'); await page.getByRole('button', { name: /▦ Board/ }).click(); await page.getByRole('button', { name: group, exact: true }).click();
    await page.getByText(title, { exact: true }).dblclick(); const dialog = page.getByRole('dialog', { name: title, exact: true });
    await dialog.getByRole('tab', { name: 'Integrations', exact: true }).click();
    const status = dialog.getByRole('textbox', { name: 'Push status', exact: true });
    const note = dialog.getByRole('textbox', { name: 'Optional status note', exact: true });
    await expect(status).toHaveValue('Backlog'); await status.fill(' Done '); await note.fill(' Verified locally ');
    await note.focus(); await note.evaluate((node: HTMLInputElement) => { node.setSelectionRange(2, 8); node.dataset.noteIdentity = 'retained'; });
    await command(request, { cmd: 'board_update_task', id, labels: ['external update'] });
    const prior = connections; await socket!.close({ code: 1012, reason: 'Status note reconnect' }); await expect.poll(() => connections).toBeGreaterThan(prior);
    await expect(note).toHaveValue(' Verified locally '); await expect(note).toHaveAttribute('data-note-identity', 'retained'); await expect(note).toBeFocused();
    expect(await note.evaluate((node: HTMLInputElement) => [node.selectionStart, node.selectionEnd])).toEqual([2, 8]); await expect(status).toHaveValue(' Done '); expect(sent).toHaveLength(0);
    await dialog.getByRole('button', { name: 'Push', exact: true }).click();
    await expect(page.getByText('Local provider fixture refused this status', { exact: true })).toBeVisible();
    await expect(note).toHaveValue(' Verified locally '); await expect(status).toHaveValue(' Done ');
    await page.screenshot({ animations: 'disabled', path: test.info().outputPath('external-status-note.png') });
    await dialog.getByRole('button', { name: 'Push', exact: true }).click();
    await expect.poll(() => sent.length).toBe(2);
    const expected = { cmd: 'external_push_task_status', id, status: 'Done', note: 'Verified locally' };
    expect(sent).toEqual([expected, expected]);
    await note.fill(''); await dialog.getByRole('button', { name: 'Push', exact: true }).click();
    await expect.poll(() => sent.length).toBe(3); expect(sent[2]).toEqual({ ...expected, note: '' });
    const saved = (await command(request, { cmd: 'task_detail', id })).task as Row;
    expect(saved.messages).toEqual([]); expect(saved.labels).toEqual(['external update']);
    await test.info().attach('local-provider-requests.json', { body: JSON.stringify(sent, null, 2), contentType: 'application/json' });
  } finally {
    if (id) await command(request, { cmd: 'board_remove_task', id });
    await command(request, { cmd: 'remove_group', group });
  }
});
