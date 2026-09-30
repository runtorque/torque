import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
import { mkdtemp, mkdir, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}

test('Board filters, saved views, density and sorting preserve group scope and survive reload and reconnect', async ({ page, request }) => {
  test.setTimeout(90_000); page.setDefaultTimeout(12_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const directory = await realpath(await mkdtemp(join(tmpdir(), 'torque-board-preferences-'))); await mkdir(join(directory, '.torque', 'actions'), { recursive: true });
  const group = `Board preferences ${Date.now()}`, other = `${group} Other`; const tasks: string[] = []; const writes: Row[] = [];
  let socket: WebSocketRoute | undefined; let connections = 0;
  await page.routeWebSocket(/\/ws\?/, (client) => {
    socket = client; connections++; const server = client.connectToServer();
    client.onMessage((raw) => { const data = JSON.parse(String(raw)) as Row; if (String(data.cmd).startsWith('board_set_') || data.cmd === 'board_reorder_task') writes.push(data); server.send(raw); });
    server.onMessage((raw) => client.send(raw));
  });
  const state = () => command(request, { cmd: 'get_state' });
  try {
    for (const name of [group, other]) {
      await command(request, { cmd: 'add_group', group: name });
      await command(request, { cmd: 'update_group_settings', group: name, settings: { default_directory: directory, default_agent_template: '', agent_provider: 'generic', agent_boot_command: '/bin/cat', git_worktree: false, notifications: false } });
    }
    for (const name of ['qa/one', 'qa/two']) await command(request, { cmd: 'save_action', group, scope: 'project', name, action: { prompt: 'Local preferences {{ TASK }}' } });
    const agent = String((await command(request, { cmd: 'add_worker', group, name: 'Preference Worker' })).id);
    const add = async (title: string, target: string, extra: Row = {}) => { const id = String((await command(request, { cmd: 'board_add_task', group: target, task: title, lane: 'Backlog', description: `${title} description`, ...extra })).task_id); tasks.push(id); return id; };
    const first = await add('Preference Alpha', group, { labels: ['urgent', 'review'], action_name: 'qa/one', agent_id: agent, scheduled_at: new Date(Date.now() + 172800000).toISOString() });
    const second = await add('Preference Beta', group, { labels: ['urgent'], action_name: 'qa/two', scheduled_at: new Date(Date.now() + 86400000).toISOString() });
    const third = await add('Preference Gamma', group, { labels: ['review'], action_name: 'qa/one' });
    const outside = await add('Outside preference', other);
    await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'board', controlTab: 'mission' } });
    await page.goto('/');
    const card = (id: string) => page.locator(`article[data-task-id="${id}"]`);
    const order = () => page.locator('[data-lane-id="lane:Backlog"] article[data-task-id]').evaluateAll((nodes) => nodes.map((node) => node.getAttribute('data-task-id')));
    await expect(card(first)).toBeVisible(); await expect.poll(order).toEqual([first, second, third]);
    for (const density of ['compact', 'normal', 'detailed']) {
      await page.getByRole('button', { name: /^Density:/ }).click(); await page.getByRole('menuitem', { name: density, exact: true }).click();
      if (density === 'normal') await expect(card(first)).not.toHaveClass(/density_(compact|detailed)/);
      else await expect(card(first)).toHaveClass(new RegExp(`density_${density}`));
      // Density changes card geometry; compact snapshots intentionally omit description bodies.
      if (density !== 'normal') await expect(card(first)).toHaveCSS('padding-top', density === 'compact' ? '6px' : '11px');
      expect(((await state()).board_card_density_by_group as Row)[group]).toBe(density);
    }
    const views = async () => { await page.getByRole('button', { name: /^Views/ }).click(); await page.getByRole('menuitem', { name: 'Filters and saved views…', exact: true }).click(); return page.getByRole('dialog', { name: 'Board views and filters', exact: true }); };
    const dialog = await views();
    for (const name of ['urgent', 'qa/one', 'Preference Worker', 'healthy']) await dialog.getByRole('checkbox', { name, exact: true }).check();
    const viewName = dialog.getByRole('textbox', { name: 'Saved view name', exact: true }); await viewName.fill('Urgent assigned'); await viewName.focus(); await viewName.evaluate((node: HTMLInputElement) => node.setSelectionRange(1, 6));
    const prior = connections; await socket!.close({ code: 1012, reason: 'Board view draft reconnect' }); await expect.poll(() => connections).toBeGreaterThan(prior);
    await expect(viewName).toHaveValue('Urgent assigned'); await expect(viewName).toBeFocused(); expect(await viewName.evaluate((node: HTMLInputElement) => [node.selectionStart, node.selectionEnd])).toEqual([1, 6]);
    for (const name of ['urgent', 'qa/one', 'Preference Worker', 'healthy']) await expect(dialog.getByRole('checkbox', { name, exact: true })).toBeChecked();
    await dialog.getByRole('button', { name: 'Save current filters', exact: true }).click(); await expect(dialog.getByRole('button', { name: 'Delete saved view Urgent assigned', exact: true })).toBeVisible();
    await dialog.getByRole('button', { name: 'Done', exact: true }).click(); await expect(dialog).toHaveCount(0); await expect.poll(order).toEqual([first]);
    const expectedFilters = { filter_labels: ['urgent'], filter_actions: ['qa/one'], filter_agents: [agent], filter_health: ['healthy'], search_query: '', quick_view: '' };
    await expect.poll(async () => ((await state()).board_filters_by_group as Row)[group]).toEqual(expectedFilters);
    expect(((await state()).board_saved_views_by_group as Row)[group]).toEqual([{ name: 'Urgent assigned', ...expectedFilters }]);
    await page.getByRole('button', { name: other, exact: true }).click(); await expect(card(outside)).toBeVisible(); await expect(page.getByRole('button', { name: 'Density: normal', exact: true })).toBeVisible();
    await page.getByRole('button', { name: group, exact: true }).click(); await expect.poll(order).toEqual([first]); await expect(page.getByRole('button', { name: 'Density: detailed', exact: true })).toBeVisible();
    await page.reload(); await expect.poll(order).toEqual([first]);
    await page.getByRole('button', { name: 'Clear filters', exact: true }).click(); await expect.poll(order).toEqual([first, second, third]);
    await page.getByRole('button', { name: /^Views/ }).click(); await page.getByRole('menuitem', { name: 'Urgent assigned', exact: true }).click(); await expect.poll(order).toEqual([first]);
    const saved = await views(); await saved.getByRole('button', { name: 'Delete saved view Urgent assigned', exact: true }).click(); await expect(saved.getByRole('button', { name: 'Delete saved view Urgent assigned', exact: true })).toHaveCount(0);
    await saved.getByRole('button', { name: 'Done', exact: true }).click(); await expect(saved).toHaveCount(0); await expect.poll(order).toEqual([first]);
    await page.getByRole('button', { name: 'Clear filters', exact: true }).click();
    for (const [mode, expected] of [['newest', [third, second, first]], ['oldest', [first, second, third]], ['due', [second, first, third]], ['manual', [first, second, third]]] as const) {
      await page.getByRole('button', { name: 'Backlog lane options', exact: true }).click(); await page.getByRole('menuitem', { name: `Sort: ${mode}`, exact: true }).click();
      await expect.poll(order).toEqual([...expected]); await page.reload(); await expect.poll(order).toEqual([...expected]);
      expect((((await state()).board_lane_sorts_by_group as Row)[group] as Row).Backlog).toBe(mode);
    }
    await page.getByRole('button', { name: /^Density:/ }).click(); await page.getByRole('menuitem', { name: 'compact', exact: true }).click();
    await expect(card(third)).toHaveClass(/density_compact/);
    const source = await card(third).boundingBox(), target = await card(first).boundingBox(); expect(source).toBeTruthy(); expect(target).toBeTruthy();
    await page.mouse.move(source!.x + 3, source!.y + source!.height / 2); await page.mouse.down(); await page.mouse.move(target!.x + 3, target!.y + 5, { steps: 15 }); await page.mouse.up();
    await expect.poll(order).toEqual([third, first, second]); await page.reload(); await expect.poll(order).toEqual([third, first, second]);
    expect(writes.some((entry) => entry.cmd === 'board_reorder_task' && entry.id === third)).toBe(true);
    await page.screenshot({ animations: 'disabled', path: test.info().outputPath('board-preferences.png') });
    const path = test.info().outputPath('board-preference-writes.json'); await writeFile(path, JSON.stringify(writes, null, 2)); await test.info().attach('board-preference-writes', { path, contentType: 'application/json' });
  } finally {
    for (const id of tasks) await command(request, { cmd: 'board_remove_task', id });
    for (const name of [group, other]) await command(request, { cmd: 'remove_group', group: name });
    await rm(directory, { recursive: true, force: true });
  }
});
