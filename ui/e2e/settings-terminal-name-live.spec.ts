import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}

test('Saved terminal name prefixes suggest unused names and preserve reviewed names through recovery', async ({ page, request }) => {
  test.setTimeout(60_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const stamp = Date.now(); const group = `Terminal names ${stamp}`; const peerGroup = `Terminal names peer ${stamp}`; const prefix = `Console ${stamp}`;
  const ids: string[] = []; const launches: Row[] = []; let refuse = false; let socket: WebSocketRoute | undefined; let connections = 0;
  for (const name of [group, peerGroup]) {
    await command(request, { cmd: 'add_group', group: name });
    await command(request, { cmd: 'update_group_settings', group: name, settings: { default_directory: '/private/tmp', terminal_boot_command: '/bin/cat', terminal_shell: '/bin/sh' } });
  }
  await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'settings' } });
  await page.routeWebSocket(/\/ws\?/, (client) => { socket = client; connections++; client.connectToServer(); });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd !== 'add_terminal') { await route.continue(); return; }
    launches.push(data);
    if (refuse) { refuse = false; await route.fulfill({ json: { ok: false, error: 'QA terminal creation refused' } }); return; }
    const response = await route.fetch(); const body = await response.json() as { data: Row };
    if (typeof body.data.id === 'string') ids.push(body.data.id);
    await route.fulfill({ response });
  });
  const open = async () => {
    await page.getByRole('button', { name: /⌁ Agents/ }).click();
    await page.getByRole('button', { name: 'Create agent or terminal' }).click();
    await page.getByRole('menuitem', { name: 'New Terminal…' }).click();
    return page.getByRole('dialog', { name: 'New terminal' });
  };
  try {
    for (const [index, target] of [group, peerGroup].entries()) ids.push(String((await command(request, { cmd: 'add_terminal', group: target, name: `${prefix} ${index + 1}` })).id));
    await page.goto('/');
    await page.getByRole('searchbox', { name: 'Search settings' }).fill('Terminal name prefix');
    await page.getByRole('button', { name: /^Terminal name prefix — / }).click();
    await page.getByLabel('Terminal name prefix', { exact: true }).fill(prefix);
    await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible();
    await page.reload(); await expect(page.getByLabel('Terminal name prefix', { exact: true })).toHaveValue(prefix);
    const first = await open(); await expect(first.getByLabel('Name', { exact: true })).toHaveValue(`${prefix} 3`);
    await first.getByRole('button', { name: 'Create terminal', exact: true }).click(); await expect(first).toHaveCount(0);
    expect(launches[0]?.name).toBe(`${prefix} 3`); await expect(page.getByRole('treeitem', { name: new RegExp(`^${prefix} 3,`) })).toBeVisible();

    const second = await open(); const name = second.getByLabel('Name', { exact: true }); await expect(name).toHaveValue(`${prefix} 4`);
    await name.fill('Custom terminal name'); await name.focus(); await name.evaluate((node) => { const input = node as HTMLInputElement; input.setSelectionRange(2, 8); input.dataset.retained = 'yes'; });
    await command(request, { cmd: 'update_group_settings', group, settings: { terminal_name_prefix: `${prefix} updated` } });
    const previous = connections; await socket!.close({ code: 1012, reason: 'Terminal name draft acceptance' }); await expect.poll(() => connections).toBeGreaterThan(previous);
    await expect(name).toHaveValue('Custom terminal name'); await expect(name).toBeFocused(); await expect(name).toHaveAttribute('data-retained', 'yes');
    expect(await name.evaluate((node) => [(node as HTMLInputElement).selectionStart, (node as HTMLInputElement).selectionEnd])).toEqual([2, 8]);
    refuse = true; await second.getByRole('button', { name: 'Create terminal', exact: true }).click(); await expect(second.getByRole('alert')).toContainText('QA terminal creation refused');
    await expect(name).toHaveValue('Custom terminal name'); await second.getByRole('button', { name: 'Retry same creation', exact: true }).click(); await expect(second).toHaveCount(0);
    expect(launches[2]).toEqual(launches[1]);
    const third = await open(); await expect(third.getByLabel('Name', { exact: true })).toHaveValue(`${prefix} updated 1`);
    await third.getByLabel('Name', { exact: true }).fill(''); await expect(third.getByRole('button', { name: 'Create terminal', exact: true })).toBeDisabled();
    await page.screenshot({ path: test.info().outputPath('terminal-name-prefix.png') });
    await third.getByRole('button', { name: 'Cancel', exact: true }).click();
    await command(request, { cmd: 'update_group_settings', group, settings: { terminal_name_prefix: '' } });
    const empty = await open(); await expect(empty.getByLabel('Name', { exact: true })).toHaveValue('');
    await empty.getByRole('button', { name: 'Cancel', exact: true }).click();
  } finally {
    for (const id of ids) await command(request, { cmd: 'remove_agent', id });
  }
});
