import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
test('keyboard review, filtered navigation and real dispatch honor saved shortcuts', async ({ page, request }) => {
  test.setTimeout(90_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const original = (await command(request, { cmd: 'get_global_settings' })).settings as Row;
  const group = `Keyboard ${Date.now()}`; const other = `${group} other`;
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'add_group', group: other }); await command(request, { cmd: 'ui_select_group', group });
  const task = String((await command(request, { cmd: 'board_add_task', group, task: 'Keyboard focus fixture', lane: 'Backlog' })).task_id);
  let agentId = '';
  const writes: Row[] = []; let socket: WebSocketRoute | undefined; let connections = 0;
  await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections++; });
  await page.route('**/api/cmd', async (route) => { const data = route.request().postDataJSON() as Row; if (data.cmd === 'update_global_settings') writes.push(data); await route.continue(); });
  const openSettings = async () => { await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click(); };
  const globalKey = async (key: string) => { await page.getByRole('button', { name: /Search commands/ }).focus(); await page.keyboard.press(key); };
  const create = page.getByRole('textbox', { name: 'Create task shortcut', exact: true });
  const board = page.getByRole('textbox', { name: 'Open Board shortcut', exact: true });
  const save = async () => { await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible(); };
  try {
    await command(request, { cmd: 'update_global_settings', settings: { keybindings: {} } });
    await page.goto('/'); await openSettings(); await expect(create).toHaveValue('N');
    await create.press('Tab'); await expect(create).not.toBeFocused(); expect(writes).toHaveLength(0);
    await create.press('Control+k'); await expect(page.getByRole('alert')).toContainText('Open command palette'); await expect(page.getByRole('dialog')).toHaveCount(0);
    await page.getByRole('button', { name: 'Cancel reassignment' }).click(); await expect(create).toBeFocused();
    await create.press('Meta+g'); await expect(page.getByRole('alert')).toContainText('fixed shortcut'); await expect(page.getByRole('button', { name: 'Reassign shortcut' })).toHaveCount(0); await create.press('Escape');
    await create.press('b'); await expect(page.getByRole('alert')).toContainText('Open Board');
    await page.screenshot({ path: test.info().outputPath('shortcut-conflict.png') });
    await page.getByRole('button', { name: 'Cancel reassignment' }).click(); await expect(create).toHaveValue('N'); expect(writes).toHaveLength(0);
    await create.press('b'); await page.getByRole('button', { name: 'Reassign shortcut' }).click(); await expect(create).toHaveValue('B'); await expect(board).toHaveValue('N'); expect(writes).toHaveLength(0);
    await save(); expect(writes).toHaveLength(1);
    expect(((await command(request, { cmd: 'get_global_settings' })).settings as Row).keybindings).toMatchObject({ 'task.create': { key: 'b' }, 'react.panel.board': { key: 'n' } });
    await page.reload(); await openSettings(); await expect(create).toHaveValue('B'); await expect(board).toHaveValue('N');
    await expect(page.getByRole('button', { name: /▦ Board/ })).toContainText('N');
    await globalKey('Control+p'); const panels = page.getByRole('combobox', { name: 'Search panels' }); await expect(panels).toBeVisible();
    await expect(page.getByRole('option', { name: `Open group: ${group}`, exact: true })).toHaveCount(0);
    await expect(page.getByRole('option', { name: 'Open Board', exact: true })).toContainText('N / K');
    await panels.fill('Open Logs'); await panels.press('Enter'); await expect(page.getByRole('heading', { name: 'Torque logs' })).toBeVisible();
    await globalKey('Meta+g'); const groups = page.getByRole('combobox', { name: 'Search groups' }); await expect(groups).toBeVisible();
    await expect(page.getByRole('option', { name: 'New Board task' })).toHaveCount(0); await groups.fill(other); await groups.press('Enter');
    await expect(page.getByText(`Workspace / ${other}`, { exact: true })).toBeVisible();
    await globalKey('Control+g'); await groups.fill(group); await page.getByRole('option', { name: `Open group: ${group}`, exact: true }).click();
    await globalKey('n'); await expect(page.getByRole('heading', { name: 'Board', exact: true })).toBeVisible();
    const card = page.locator(`article[data-task-id="${task}"]`); await card.focus(); await card.press('n'); await expect(page.getByRole('dialog', { name: 'Create task' })).toHaveCount(0);
    await card.press('b'); const dialog = page.getByRole('dialog', { name: 'Create task' }); await expect(dialog).toBeVisible(); await dialog.getByRole('button', { name: 'Close dialog' }).click();
    await openSettings(); await create.locator('xpath=ancestor::article').getByRole('button', { name: 'Reset', exact: true }).click();
    await expect(page.getByRole('alert')).toContainText('Open Board'); await page.getByRole('button', { name: 'Reassign shortcut' }).click(); await expect(create).toHaveValue('N'); await expect(board).toHaveValue('B'); await save();
    expect(((await command(request, { cmd: 'get_global_settings' })).settings as Row).keybindings).toEqual({});
    await create.press('t'); await save();
    const before = connections; await socket!.close({ code: 1012, reason: 'Keyboard settings reconnect' }); await expect.poll(() => connections).toBeGreaterThan(before); await expect(create).toHaveValue('T');
    await page.reload(); await openSettings(); await expect(create).toHaveValue('T');
    await globalKey('k'); await expect(page.getByRole('heading', { name: 'Board', exact: true })).toBeVisible();
    await card.focus(); await card.press('n'); await expect(dialog).toHaveCount(0); await card.press('t'); await expect(dialog).toBeVisible(); await dialog.getByRole('button', { name: 'Close dialog' }).click();
    await page.getByRole('searchbox', { name: 'Search board' }).fill('ordinary text'); await page.keyboard.press('t'); await expect(dialog).toHaveCount(0);
    await globalKey('Meta+p'); await panels.fill('Settings'); await panels.press('Enter'); await expect(create).toHaveValue('T');
    await globalKey('Meta+k'); await page.getByRole('combobox', { name: 'Search commands' }).fill('New Board task'); await expect(page.getByRole('option', { name: 'New Board task' })).toContainText('T');
    await page.screenshot({ path: test.info().outputPath('effective-shortcut-hint.png') });
    await page.keyboard.press('Escape');
    const frame = await command(request, { cmd: 'add_agent', group, name: 'Keyboard worker', provider: 'generic', command: '/bin/cat', directory: '/private/tmp', shell: '/bin/sh', worktree: false });
    agentId = String(Object.values(frame.agents as Record<string, Row>).find((item) => item.group === group && item.name === 'Keyboard worker')!.id);
    await command(request, { cmd: 'remove_agent', id: agentId }); await command(request, { cmd: 'restore_agent', id: agentId });
    await globalKey('c'); const composer = page.getByRole('textbox', { name: 'Message Keyboard worker', exact: true }); await expect(composer).toBeFocused();
    await page.getByRole('tab', { name: 'Activity', exact: true }).click(); await openSettings();
    await page.getByRole('textbox', { name: 'Focus agent composer shortcut', exact: true }).press('Shift+q'); await save();
    await globalKey('c'); await expect(page.getByRole('heading', { name: 'Control Center', exact: true })).toBeVisible();
    await globalKey('Shift+q'); await expect(composer).toBeFocused(); await expect(page.getByRole('tab', { name: 'Live', exact: true })).toHaveAttribute('aria-selected', 'true');

  } finally {
    if (agentId) await command(request, { cmd: 'remove_agent', id: agentId });
    await command(request, { cmd: 'update_global_settings', settings: { keybindings: original.keybindings ?? {} } });
    await command(request, { cmd: 'board_remove_task', id: task });
  }
});
