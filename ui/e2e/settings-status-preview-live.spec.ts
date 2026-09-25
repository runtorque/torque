import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}
test('status preview follows unsaved visibility through reconnect, refusal, save, reload and reset', async ({ page, request }) => {
  test.setTimeout(60_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const original = (await command(request, { cmd: 'get_global_settings' })).settings as Row;
  const keys = ['daemon_status', 'deploy', 'health', 'workload', 'tasks', 'attention', 'claude_usage', 'codex_usage'];
  const empty = Object.fromEntries(keys.map((key) => [key, false])); const all = Object.fromEntries(keys.map((key) => [key, true]));
  const writes: Row[] = []; let refuse = false; let socket: WebSocketRoute | undefined; let connections = 0;
  await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections++; });
  await page.route('**/api/cmd', async (route) => { const data = route.request().postDataJSON() as Row; if (data.cmd === 'update_global_settings') { writes.push(data); if (refuse) { refuse = false; await route.fulfill({ json: { ok: false, error: 'QA status visibility refused' } }); return; } } await route.continue(); });
  const open = async () => { await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click(); };
  const section = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Status bar', exact: true }) }).last();
  const preview = page.getByRole('group', { name: 'Status bar preview', exact: true }); const footer = page.locator('footer[aria-label="Workspace status"]');
  const save = async () => { await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible(); };
  try {
    await command(request, { cmd: 'update_global_settings', settings: { status_bar_visibility: { ...empty, tasks: true } } });
    await page.goto('/'); await open(); await expect(preview.getByRole('listitem')).toHaveCount(1); await expect(preview).toContainText('Tasks 4 active');
    for (const key of keys) await section.getByRole('checkbox', { name: key.replaceAll('_', ' '), exact: true }).check();
    await expect(preview.getByRole('listitem')).toHaveCount(9); await expect(footer).toContainText('Tasks'); await expect(footer).not.toContainText('Claude'); expect(writes).toHaveLength(0);
    expect(((await command(request, { cmd: 'get_global_settings' })).settings as Row).status_bar_visibility).toEqual({ ...empty, tasks: true });
    const focus = section.getByRole('checkbox', { name: 'codex usage', exact: true }); await focus.focus(); await focus.evaluate((input) => input.setAttribute('data-retained', 'yes'));
    const before = connections; await socket!.close({ code: 1012, reason: 'Status preview draft reconnect' }); await expect.poll(() => connections).toBeGreaterThan(before);
    await expect(focus).toBeFocused(); await expect(focus).toHaveAttribute('data-retained', 'yes'); await expect(preview.getByRole('listitem')).toHaveCount(9); expect(writes).toHaveLength(0);
    await preview.scrollIntoViewIfNeeded(); await page.screenshot({ path: test.info().outputPath('status-preview.png') });
    await page.setViewportSize({ width: 390, height: 780 }); await preview.scrollIntoViewIfNeeded(); const bounds = await preview.boundingBox(); expect(bounds!.x).toBeGreaterThanOrEqual(0); expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390); await page.screenshot({ path: test.info().outputPath('status-preview-narrow.png') }); await page.setViewportSize({ width: 1280, height: 720 });
    refuse = true; await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByRole('alert')).toContainText('QA status visibility refused'); await expect(preview.getByRole('listitem')).toHaveCount(9); await expect(footer).not.toContainText('Claude'); await save();
    expect(writes).toHaveLength(2); expect(writes[1]).toEqual({ cmd: 'update_global_settings', settings: { status_bar_visibility: all } }); await expect(footer).toContainText('Claude');
    await page.reload(); await open(); await expect(preview.getByRole('listitem')).toHaveCount(9);
    for (const key of keys) await section.getByRole('checkbox', { name: key.replaceAll('_', ' '), exact: true }).uncheck();
    await expect(preview).toContainText('No optional items selected'); await expect(footer).toContainText('Claude'); await save(); await expect(footer).toHaveText('');
    expect(((await command(request, { cmd: 'get_global_settings' })).settings as Row).status_bar_visibility).toEqual(empty);
    await page.reload(); await open(); await expect(preview).toContainText('No optional items selected');
    const count = writes.length; await page.getByRole('button', { name: 'Reset global defaults', exact: true }).click(); await expect(preview.getByRole('listitem')).toHaveText(['Deploy +2', 'Tasks 4 active', 'Attention 1']); expect(writes).toHaveLength(count); await expect(footer).toHaveText('');
  } finally { await command(request, { cmd: 'update_global_settings', settings: { status_bar_visibility: original.status_bar_visibility } }); }
});
