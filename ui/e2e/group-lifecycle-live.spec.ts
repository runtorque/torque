import { expect, test, type APIRequestContext } from '@playwright/test';

type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}

test.beforeEach(async ({ request }) => {
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
});

test('group creation, rename, arbitrary ordering and reviewed removal persist and remove owned sessions', async ({ page, request }) => {
  test.setTimeout(60_000); page.setDefaultTimeout(10_000);
  const prefix = `Group UI ${Date.now()}`;
  const first = `${prefix} Alpha`, second = `${prefix} Beta`, third = `${prefix} Gamma`, renamed = `${prefix} Renamed`;
  const owned = new Set([first, second, third, renamed]); const writes: Row[] = []; const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.routeWebSocket(/\/ws\?/, (client) => {
    const server = client.connectToServer();
    client.onMessage((raw) => { const data = JSON.parse(String(raw)) as Row; if (['add_group', 'rename_group', 'move_group', 'remove_group'].includes(String(data.cmd))) writes.push(data); server.send(raw); });
    server.onMessage((raw) => client.send(raw));
  });
  const sessionIds = async () => ((await command(request, { cmd: 'supervisor_sessions_list' })).sessions as Row[]).map((session) => session.session_id);
  const groupState = async () => (await command(request, { cmd: 'get_state' })).groups as Row;
  const persistedOrder = async () => Object.keys(await groupState()).filter((group) => owned.has(group));
  try {
    await page.goto('/'); await expect(page.getByText('connected', { exact: true })).toBeVisible();
    const groups = page.getByRole('region', { name: 'Groups', exact: true });
    for (const name of [first, second, third]) {
      await groups.getByRole('button', { name: 'Add group', exact: true }).click();
      const dialog = page.getByRole('dialog', { name: 'New group', exact: true });
      await dialog.getByRole('textbox', { name: 'Name', exact: true }).fill(name);
      await dialog.getByRole('textbox', { name: 'Default directory', exact: true }).fill('/private/tmp');
      await dialog.getByRole('button', { name: 'Create group', exact: true }).click();
      await expect(groups.getByRole('button', { name, exact: true })).toBeVisible();
      const settings = await command(request, { cmd: 'get_group_settings', group: name });
      expect((settings.settings as Row).default_directory).toBe('/private/tmp');
    }
    const menu = async (group: string, action: string) => {
      await groups.getByRole('button', { name: `${group} group options`, exact: true }).click();
      await page.getByRole('menuitem', { name: action, exact: true }).click();
    };
    const visualOrder = async () => (await groups.locator('[draggable="true"] > button:first-child').allTextContents()).map((name) => name.trim()).filter((name) => owned.has(name));
    await menu(second, 'Rename');
    const rename = page.getByRole('dialog', { name: 'Rename group', exact: true });
    await rename.getByRole('textbox', { name: 'New name', exact: true }).fill(renamed);
    await rename.getByRole('button', { name: 'Rename', exact: true }).click();
    await expect(groups.getByRole('button', { name: renamed, exact: true })).toBeVisible();
    await expect(groups.getByRole('button', { name: second, exact: true })).toHaveCount(0);
    await menu(third, 'Move group…');
    const move = page.getByRole('dialog', { name: 'Move group', exact: true });
    await move.getByRole('combobox', { name: 'Group position', exact: true }).selectOption(renamed);
    await move.getByRole('button', { name: 'Move group', exact: true }).click();
    await expect.poll(persistedOrder).toEqual([first, third, renamed]); await expect.poll(visualOrder).toEqual([first, third, renamed]);
    const source = groups.getByRole('button', { name: renamed, exact: true }).locator('..');
    const target = groups.getByRole('button', { name: first, exact: true }).locator('..');
    await source.dragTo(target);
    await expect.poll(persistedOrder).toEqual([renamed, first, third]); await expect.poll(visualOrder).toEqual([renamed, first, third]);
    await page.reload(); await expect.poll(visualOrder).toEqual([renamed, first, third]);
    await command(request, { cmd: 'update_group_settings', group: renamed, settings: { agent_provider: 'generic', agent_boot_command: '/bin/cat', default_agent_template: '', git_worktree: false, notifications: false } });
    const worker = String((await command(request, { cmd: 'add_worker', group: renamed, name: 'Group removal QA' })).id);
    const terminal = String((await command(request, { cmd: 'add_terminal', group: renamed, parent_id: worker, name: 'Group child QA', command: '/bin/cat', directory: '/private/tmp', shell: '/bin/sh' })).id);
    const before = await command(request, { cmd: 'get_state' });
    expect((before.agents as Row)[worker]).toBeTruthy(); expect((before.agents as Row)[terminal]).toBeTruthy();
    const ownedSessions = [worker, terminal].map((id) => ((before.agents as Row)[id] as Row).session_id);
    expect(ownedSessions.every((id) => typeof id === 'string' && id.length > 0)).toBe(true);
    // Check exact owned sessions; cleanup of a previous scenario may still be in flight.
    await expect.poll(async () => { const ids = await sessionIds(); return ownedSessions.every((id) => ids.includes(id)); }).toBe(true);
    await menu(renamed, 'Remove…');
    const remove = page.getByRole('dialog', { name: `Remove ${renamed}?`, exact: true });
    await expect(remove).toContainText('removes its agents and child terminals and closes their sessions');
    await expect(remove.getByRole('button', { name: 'Cancel', exact: true })).toBeFocused();
    expect(writes.filter((item) => item.cmd === 'remove_group')).toHaveLength(0);
    await page.screenshot({ animations: 'disabled', path: test.info().outputPath('group-removal-confirmation.png') });
    await page.keyboard.press('Escape'); await expect(remove).toHaveCount(0);
    await expect(groups.getByRole('button', { name: `${renamed} group options`, exact: true })).toBeFocused();
    expect((await groupState())[renamed]).toBeTruthy();
    await menu(renamed, 'Remove…'); await remove.getByRole('button', { name: 'Cancel', exact: true }).click();
    expect(writes.filter((item) => item.cmd === 'remove_group')).toHaveLength(0);
    await menu(renamed, 'Remove…'); await remove.getByRole('button', { name: 'Remove', exact: true }).click();
    await expect(groups.getByRole('button', { name: renamed, exact: true })).toHaveCount(0);
    await expect.poll(async () => {
      const state = await command(request, { cmd: 'get_state' });
      return [(state.groups as Row)[renamed], (state.agents as Row)[worker], (state.agents as Row)[terminal]];
    }).toEqual([undefined, undefined, undefined]);
    await expect.poll(async () => { const ids = await sessionIds(); return ownedSessions.some((id) => ids.includes(id)); }).toBe(false);
    expect(writes.filter((item) => item.cmd === 'remove_group')).toEqual([{ cmd: 'remove_group', group: renamed }]);
    await page.reload(); await expect.poll(visualOrder).toEqual([first, third]); expect(errors).toEqual([]);
    await test.info().attach('group-writes.json', { body: JSON.stringify(writes, null, 2), contentType: 'application/json' });
  } finally {
    const existing = await groupState();
    for (const group of owned) if (Object.hasOwn(existing, group)) await command(request, { cmd: 'remove_group', group });
  }
});

test('browser daemon controls and native menu entry confirm or cancel without native dialogs', async ({ page }) => {
  const lifecycle = new Set(['restart', 'stop', 'supervisor_restart']); const sent: Row[] = []; const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.routeWebSocket(/\/ws\?/, (client) => {
    const server = client.connectToServer();
    client.onMessage((raw) => {
      const data = JSON.parse(String(raw)) as Row;
      // Never forward lifecycle commands to the QA daemon. Inspect the exact reviewed payload only.
      if (lifecycle.has(String(data.cmd))) { sent.push(data); return; }
      server.send(raw);
    });
    server.onMessage((raw) => client.send(raw));
  });
  await page.goto('/'); await expect(page.getByText('connected', { exact: true })).toBeVisible();
  for (const [menu, title, label, cmd] of [
    ['Restart daemon…', 'Restart Torque daemon?', 'Restart', 'restart'],
    ['Stop daemon…', 'Stop Torque daemon?', 'Stop', 'stop'],
    ['', 'Restart terminal supervisor?', 'Restart', 'supervisor_restart'],
  ] as const) {
    const open = async () => {
      if (menu) { await page.getByRole('button', { name: 'Workspace actions', exact: true }).click(); await page.getByRole('menuitem', { name: menu, exact: true }).click(); }
      else await page.evaluate(() => (window as Window & { restartTerminalSupervisor?: () => void }).restartTerminalSupervisor?.());
    };
    await open(); const dialog = page.getByRole('dialog', { name: title, exact: true }); await expect(dialog).toBeVisible();
    await expect(dialog.getByRole('button', { name: 'Cancel', exact: true })).toBeFocused();
    await page.keyboard.press('Escape'); await expect(dialog).toHaveCount(0); expect(sent.filter((item) => item.cmd === cmd)).toHaveLength(0);
    if (menu) await expect(page.getByRole('button', { name: 'Workspace actions', exact: true })).toBeFocused();
    await open(); await dialog.getByRole('button', { name: 'Cancel', exact: true }).click(); expect(sent.filter((item) => item.cmd === cmd)).toHaveLength(0);
    await open(); await dialog.getByRole('button', { name: label, exact: true }).click(); await expect(dialog).toHaveCount(0);
    await expect.poll(() => sent.filter((item) => item.cmd === cmd)).toEqual([{ cmd }]);
  }
  await page.reload(); await expect(page.getByText('connected', { exact: true })).toBeVisible(); expect(sent).toEqual([{ cmd: 'restart' }, { cmd: 'stop' }, { cmd: 'supervisor_restart' }]); expect(errors).toEqual([]);
});
