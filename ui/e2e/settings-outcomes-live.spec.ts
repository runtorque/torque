import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) { const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row }; expect(result.ok, result.error).toBe(true); return result.data; }
async function prepare(request: APIRequestContext) {
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime; expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Settings outcomes ${Date.now()}`; await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: '/private/tmp' } });
  await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'settings' } }); return group;
}
test('Settings recovers bounded initial and reconnect reads and rejects an unrelated save acknowledgement', async ({ page, request }) => {
  test.setTimeout(65_000); const group = await prepare(request); let hold = true; let release = () => {}; let held = 0; let invalid = true; let socket: WebSocketRoute | undefined; let connections = 0;
  await page.routeWebSocket(/\/ws\?/, (route) => { route.connectToServer(); socket = route; connections++; });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd === 'get_global_settings' && hold) { const response = await route.fetch(); held++; await new Promise<void>((resolve) => { release = resolve; }); await route.fulfill({ response }); return; }
    if (data.cmd === 'update_group_settings' && invalid) { await route.fulfill({ json: { ok: true, data: { type: 'mission_control_summary', group, sections: {} } } }); return; }
    await route.continue();
  });
  try {
    await page.goto('/'); await expect.poll(() => held).toBe(1); await expect(page.getByText('Loading settings', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Retry settings', exact: true })).toBeVisible({ timeout: 20_000 });
    hold = false; await page.getByRole('button', { name: 'Retry settings', exact: true }).click(); const directory = page.getByRole('textbox', { name: 'Default directory', exact: true }); await expect(directory).toHaveValue('/private/tmp'); release();
    await directory.fill('/private/tmp/retained-draft'); await directory.evaluate((node: HTMLInputElement) => { node.dataset.settingsAnchor = 'same'; node.setSelectionRange(2, 7); });
    hold = true; const before = connections; await socket!.close({ code: 1012, reason: 'Settings refresh timeout' }); await expect.poll(() => connections).toBeGreaterThan(before); await expect.poll(() => held).toBe(2);
    await expect(page.getByRole('alert')).toContainText('Settings refresh timed out', { timeout: 20_000 }); await expect(directory).toBeFocused(); await expect(directory).toHaveAttribute('data-settings-anchor', 'same'); expect(await directory.evaluate((node: HTMLInputElement) => [node.selectionStart, node.selectionEnd])).toEqual([2, 7]);
    hold = false; release(); await page.getByRole('button', { name: 'Retry settings', exact: true }).click(); await expect(page.getByRole('alert')).toHaveCount(0); await expect(directory).toHaveValue('/private/tmp/retained-draft');
    await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByRole('alert')).toContainText('invalid group settings acknowledgement'); await expect(page.getByText('Saved', { exact: true })).toHaveCount(0); await expect(directory).toHaveValue('/private/tmp/retained-draft');
    expect((await command(request, { cmd: 'get_group_settings', group })).settings).toMatchObject({ default_directory: '/private/tmp' });
    invalid = false; await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible(); expect((await command(request, { cmd: 'get_group_settings', group })).settings).toMatchObject({ default_directory: '/private/tmp/retained-draft' });
  } finally { release(); }
});
test('Settings releases a stalled partial save, preserves acknowledged scopes and never replays after reconnect', async ({ page, request }) => {
  test.setTimeout(55_000); const group = await prepare(request); await command(request, { cmd: 'update_global_settings', settings: { xterm_scrollback: 2000 } });
  let hold = true; let held = false; let release = () => {}; let socket: WebSocketRoute | undefined; let connections = 0; const writes: Row[] = [];
  await page.routeWebSocket(/\/ws\?/, (route) => { route.connectToServer(); socket = route; connections++; });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row; if (/^(update_|engineer_update_)/.test(String(data.cmd))) writes.push(data);
    if (data.cmd === 'update_group_settings' && hold) { held = true; await new Promise<void>((resolve) => { release = resolve; }); await route.fulfill({ json: { ok: false, error: 'Late obsolete refusal' } }); return; }
    await route.continue();
  });
  try {
    await page.goto('/'); const directory = page.getByRole('textbox', { name: 'Default directory', exact: true }); await directory.fill('/private/tmp/retried'); await page.getByRole('spinbutton', { name: 'Terminal scrollback', exact: true }).fill('6100'); await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect.poll(() => held).toBe(true);
    await page.getByRole('button', { name: 'Help', exact: true }).click(); await expect(page.getByRole('dialog', { name: 'Settings save in progress' })).toBeVisible();
    await expect(page.getByRole('dialog', { name: 'Discard settings changes?' })).toBeVisible({ timeout: 35_000 }); await page.getByRole('button', { name: 'Keep editing', exact: true }).click(); await expect(page.getByRole('alert')).toContainText('outcome is unknown'); await expect(directory).toBeEnabled();
    const before = connections; await socket!.close({ code: 1012, reason: 'Unknown settings outcome' }); await expect.poll(() => connections).toBeGreaterThan(before); await expect(directory).toHaveValue('/private/tmp/retried'); await expect(page.getByRole('spinbutton', { name: 'Terminal scrollback', exact: true })).toHaveValue('6100'); expect(writes).toHaveLength(2);
    hold = false; await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible(); release(); expect(writes.filter((data) => data.cmd === 'update_global_settings')).toHaveLength(1); expect(writes.filter((data) => data.cmd === 'update_group_settings')).toHaveLength(2);
    expect((await command(request, { cmd: 'get_global_settings' })).settings).toMatchObject({ xterm_scrollback: 6100 }); expect((await command(request, { cmd: 'get_group_settings', group })).settings).toMatchObject({ default_directory: '/private/tmp/retried' }); await expect(page.getByRole('alert')).toHaveCount(0);
    await page.reload(); await expect(directory).toHaveValue('/private/tmp/retried'); await expect(page.getByRole('spinbutton', { name: 'Terminal scrollback', exact: true })).toHaveValue('6100');
  } finally { release(); }
});
