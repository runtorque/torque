import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const response = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(response.ok, response.error).toBe(true); return response.data;
}
let createdIds: string[] = [];
test.afterEach(async ({ request }) => { for (const id of createdIds) await command(request, { cmd: 'board_remove_task', id }); createdIds = []; });
test('stale Done suggestion respects age and group, archives one batch and preserves other Board work', async ({ page, request }) => {
  test.setTimeout(60_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: { port: number; profile: string } } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Stale archive ${Date.now()}`; const other = `${group} other`; const baseline = Date.now();
  for (const name of [group, other]) await command(request, { cmd: 'add_group', group: name });
  await command(request, { cmd: 'ui_select_group', group });
  const add = async (task: string, lane: string, scope = group, labels: string[] = []) => { const id = String((await command(request, { cmd: 'board_add_task', group: scope, task, lane, labels })).task_id); createdIds.push(id); return id; };
  const first = await add('First completed task', 'Done'); const second = await add('Second completed task', 'Done');
  const outside = await add('Other group completed', 'Done', other); const archived = await add('Already archived', 'Backlog'); await command(request, { cmd: 'board_archive_task', id: archived });
  const backlog = await add('Keep selected backlog task', 'Backlog');
  for (let i = 0; i < 24; i++) await add(`Keep ready task ${i}`, 'To Do');
  await page.setViewportSize({ width: 1600, height: 900 });
  // Real tasks and batch persistence; only the browser's clock advances.
  await page.clock.setFixedTime(baseline + 6 * 86400000); await page.goto('/'); await page.getByRole('button', { name: /▦ Board/ }).click(); await expect(page.getByText('connected', { exact: true })).toBeVisible(); await expect(page.getByRole('region', { name: 'Inactive completed tasks' })).toHaveCount(0);
  await page.clock.setFixedTime(baseline + 8 * 86400000); await page.reload();
  const suggestion = page.getByRole('region', { name: 'Inactive completed tasks' }); const archive = suggestion.getByRole('button', { name: 'Archive 2 completed tasks inactive for 7+ days', exact: true }); await expect(archive).toBeVisible();
  await page.getByRole('searchbox', { name: 'Search board' }).fill('Second completed'); await expect(page.getByRole('heading', { name: 'First completed task', exact: true })).toHaveCount(0); await expect(archive).toBeVisible(); await expect(suggestion.getByText('Includes filtered tasks in this group.')).toBeVisible(); await page.getByRole('button', { name: 'Clear filters', exact: true }).click();
  await page.getByRole('combobox', { name: 'Visible lane', exact: true }).selectOption('To Do'); await expect(suggestion).toHaveCount(0); await page.getByRole('combobox', { name: 'Visible lane', exact: true }).selectOption(''); await expect(archive).toBeVisible();
  await page.getByRole('button', { name: 'Archive', exact: true }).click(); await expect(suggestion).toHaveCount(0); await page.getByRole('button', { name: 'Active board', exact: true }).click(); await expect(archive).toBeVisible();
  await page.getByRole('heading', { name: 'Keep selected backlog task', exact: true }).click(); const selected = page.locator(`article[data-task-id="${backlog}"]`); await expect(selected).toHaveClass(/taskSelected/);
  await page.locator('#lane-Backlog').getByRole('button', { name: '＋ Add task', exact: true }).click(); const draft = page.getByRole('textbox', { name: 'New task in Backlog', exact: true }); await draft.fill('Unsubmitted task');
  let refuse = true; let release: (() => void) | undefined; const batches: Row[] = []; let socket: WebSocketRoute | undefined; let connections = 0;
  await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections++; });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd !== 'board_archive_tasks') { await route.continue(); return; }
    batches.push(data);
    if (refuse) await route.fulfill({ json: { ok: false, error: 'Injected batch refusal' } });
    else { await new Promise<void>((resolve) => { release = resolve; }); await route.continue(); }
  });
  await archive.click(); await expect(suggestion.getByRole('alert')).toContainText('Injected batch refusal'); await expect(draft).toHaveValue('Unsubmitted task'); await expect(selected).toHaveClass(/taskSelected/);
  expect(batches).toEqual([{ cmd: 'board_archive_tasks', ids: [first, second] }]);
  await page.screenshot({ path: test.info().outputPath('stale-archive-refused.png') });
  refuse = false; await archive.focus(); await archive.press('Enter'); await expect.poll(() => Boolean(release)).toBe(true); await expect(suggestion.getByRole('button', { name: 'Archiving completed tasks…' })).toBeDisabled();
  await draft.focus(); await draft.evaluate((node: HTMLInputElement) => { node.setSelectionRange(2, 7); node.dataset.archiveAnchor = 'original'; }); const ready = page.locator('[data-lane-id="lane:To Do"]'); await ready.evaluate((node) => { node.scrollTop = 180; });
  release!(); await expect(suggestion).toHaveCount(0); await expect(draft).toHaveValue('Unsubmitted task'); await expect(draft).toBeFocused(); await expect(draft).toHaveAttribute('data-archive-anchor', 'original'); expect(await draft.evaluate((node: HTMLInputElement) => [node.selectionStart, node.selectionEnd])).toEqual([2, 7]); expect(await ready.evaluate((node) => node.scrollTop)).toBe(180); await expect(selected).toHaveClass(/taskSelected/); expect(batches).toHaveLength(2);
  for (const id of [first, second, archived]) expect((await command(request, { cmd: 'task_detail', id })).task).toMatchObject({ lane: 'Archived' });
  expect((await command(request, { cmd: 'task_detail', id: outside })).task).toMatchObject({ lane: 'Done' }); expect((await command(request, { cmd: 'task_detail', id: backlog })).task).toMatchObject({ lane: 'Backlog' });
  await page.screenshot({ path: test.info().outputPath('stale-archive-complete.png') });
  // Reconnect on a fresh document keeps the archive projection authoritative.
  await page.reload(); await expect.poll(() => connections).toBeGreaterThan(0); const before = connections; await socket!.close({ code: 1012, reason: 'Archive projection reconnect' }); await expect.poll(() => connections).toBeGreaterThan(before); await expect(suggestion).toHaveCount(0);
  await page.getByRole('button', { name: 'Archive', exact: true }).click(); await expect(page.getByRole('heading', { name: 'First completed task', exact: true })).toBeVisible(); await expect(page.getByRole('heading', { name: 'Second completed task', exact: true })).toBeVisible();
});
