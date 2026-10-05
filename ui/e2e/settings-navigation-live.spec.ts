import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
test('settings exits preserve drafts, scope and pending save results across navigation and reconnect', async ({ page, request }) => {
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.profile).not.toBe('default'); expect(runtime.port).not.toBe(18932);
  const group = `Protected settings ${Date.now()}`; const other = `${group} other`;
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'add_group', group: other });
  await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: '/private/tmp', max_agents: 3 } });
  await command(request, { cmd: 'update_group_settings', group: other, settings: { default_directory: '/private/tmp/other', max_agents: 4 } });
  await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'settings' } });
  const writes: Row[] = []; const navigation: Row[] = []; let socket: WebSocketRoute | undefined; let connections = 0;
  page.on('websocket', (connection) => connection.on('framesent', (event) => {
    const data = JSON.parse(String(event.payload)) as Row; if (data.cmd === 'ui_select_group') navigation.push(data);
  }));
  await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections++; });
  let release = () => {}; let held = false; let hold = false; let refuse = true;
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (/^(update_|engineer_update_)/.test(String(data.cmd))) writes.push(data);
    if (data.cmd === 'update_group_settings' && hold) {
      held = true; await new Promise<void>((resolve) => { release = resolve; });
      if (refuse) { await route.fulfill({ json: { ok: false, error: 'Navigation fixture save refused' } }); return; }
    }
    await route.continue();
  });
  try {
    await page.goto('/'); const directory = page.getByRole('textbox', { name: 'Default directory', exact: true });
    await expect(directory).toHaveValue('/private/tmp'); await directory.fill('/private/tmp/retained-draft');
    await directory.evaluate((node: HTMLInputElement) => { node.dataset.guardAnchor = 'same-node'; node.setSelectionRange(3, 11); });
    await page.getByRole('button', { name: 'Help', exact: true }).click();
    let dialog = page.getByRole('dialog', { name: 'Discard settings changes?' }); await expect(dialog).toBeVisible(); await expect(dialog.getByRole('button', { name: 'Keep editing' })).toBeFocused();
    expect(writes).toHaveLength(0); await page.screenshot({ animations: 'disabled', path: test.info().outputPath('settings-discard-review.png') });
    await dialog.getByRole('button', { name: 'Keep editing' }).click(); await expect(directory).toBeFocused(); await expect(directory).toHaveAttribute('data-guard-anchor', 'same-node');
    expect(await directory.evaluate((node: HTMLInputElement) => [node.selectionStart, node.selectionEnd])).toEqual([3, 11]);
    await page.getByRole('button', { name: other, exact: true }).click(); await dialog.getByRole('button', { name: 'Keep editing' }).click(); expect(navigation).toHaveLength(0);
    await page.getByRole('button', { name: 'Save changes', exact: true }).focus(); await page.keyboard.press('b'); await dialog.getByRole('button', { name: 'Keep editing' }).click();
    await page.evaluate(() => (window as Window & { openLogViewer?: () => void }).openLogViewer!()); await dialog.getByRole('button', { name: 'Keep editing' }).click();
    await page.getByRole('link', { name: 'Classic UI', exact: true }).click(); await dialog.getByRole('button', { name: 'Keep editing' }).click(); await expect(directory).toHaveValue('/private/tmp/retained-draft');
    await page.getByRole('button', { name: /Search commands/ }).click(); await page.getByRole('combobox', { name: 'Search commands' }).fill('Open Board'); await page.getByRole('option', { name: 'Open Board', exact: true }).click();
    await expect(page.getByRole('dialog', { name: 'Command palette' })).toBeHidden(); await dialog.getByRole('button', { name: 'Keep editing' }).click();
    const before = connections; await socket!.close({ code: 1012, reason: 'Protected draft reconnect' }); await expect.poll(() => connections).toBeGreaterThan(before);
    await expect(directory).toHaveAttribute('data-guard-anchor', 'same-node'); await expect(directory).toHaveValue('/private/tmp/retained-draft'); expect(writes).toHaveLength(0);
    // An external group selection must not silently replace the settings scope.
    await command(request, { cmd: 'ui_select_group', group: other }); await expect(page.getByText(`Settings for ${group} remain open.`, { exact: false })).toBeVisible(); await expect(directory).toHaveValue('/private/tmp/retained-draft');
    hold = true; await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect.poll(() => held).toBe(true);
    await page.getByRole('button', { name: /▦ Board/ }).click(); dialog = page.getByRole('dialog', { name: 'Settings save in progress' });
    await expect(dialog).toBeVisible(); await expect(dialog.getByRole('button', { name: 'Discard changes' })).toHaveCount(0); expect(writes.at(-1)?.group).toBe(group);
    await page.screenshot({ animations: 'disabled', path: test.info().outputPath('settings-save-held.png') }); release();
    dialog = page.getByRole('dialog', { name: 'Discard settings changes?' }); await dialog.getByRole('button', { name: 'Keep editing' }).click(); await expect(page.getByRole('alert')).toContainText('Navigation fixture save refused'); await expect(directory).toHaveValue('/private/tmp/retained-draft');
    refuse = false; held = false; await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect.poll(() => held).toBe(true);
    await page.getByRole('button', { name: /▦ Board/ }).click(); await expect(page.getByRole('dialog', { name: 'Settings save in progress' })).toBeVisible(); release();
    dialog = page.getByRole('dialog', { name: 'Leave Settings?' }); await expect(dialog).toBeVisible(); expect(writes).toHaveLength(2); await dialog.getByRole('button', { name: 'Keep editing' }).click();
    await expect(directory).toHaveAttribute('data-guard-anchor', 'same-node'); expect((await command(request, { cmd: 'get_group_settings', group })).settings).toMatchObject({ default_directory: '/private/tmp/retained-draft' });
    expect((await command(request, { cmd: 'get_group_settings', group: other })).settings).toMatchObject({ default_directory: '/private/tmp/other' });
    await page.getByRole('button', { name: `Switch to ${other}`, exact: true }).click(); await expect(directory).toHaveValue('/private/tmp/other');
    await directory.fill('/private/tmp/discard-me'); await page.getByRole('button', { name: group, exact: true }).click(); await page.getByRole('button', { name: 'Discard changes', exact: true }).click();
    await expect(directory).toHaveValue('/private/tmp/retained-draft'); expect(navigation).toHaveLength(1); expect(navigation[0]?.group).toBe(group); expect(writes).toHaveLength(2);
    await page.getByRole('button', { name: 'Mission Control', exact: true }).click(); await expect(page.getByRole('heading', { name: 'Workspace settings' })).toBeHidden(); await expect(page.getByRole('dialog')).toHaveCount(0);
  } finally { release(); }
});
