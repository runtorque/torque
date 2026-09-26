import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}
async function prepare(request: APIRequestContext, name: string) {
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `${name} ${Date.now()}`; await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'board', controlTab: 'mission' } }); return group;
}
test('Board initial detail and reconnect recover from actual deadlines without replacing the task draft', async ({ page, request }) => {
  test.setTimeout(90_000); const group = await prepare(request, 'Board detail recovery');
  const id = String((await command(request, { cmd: 'board_add_task', group, task: 'Recover initial detail', description: 'Saved description', lane: 'Backlog' })).task_id);
  let hold = true; let held = 0; let release = () => {}; let wrong = true; let reads = 0; let connections = 0; let socket: WebSocketRoute | undefined;
  await page.routeWebSocket(/\/ws\?/, (route) => { route.connectToServer(); socket = route; connections++; });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd === 'task_detail' && data.id === id) {
      reads++;
      if (hold) { hold = false; const response = await route.fetch(); held++; await new Promise<void>((resolve) => { release = resolve; }); await route.fulfill({ response }); return; }
      if (wrong) { wrong = false; await route.fulfill({ json: { ok: true, data: { type: 'task_detail', id: 'wrong-task', task: { id: 'wrong-task', group, description: 'Unrelated detail' } } } }); return; }
    }
    await route.continue();
  });
  try {
    await page.goto('/'); await page.getByText('Recover initial detail', { exact: true }).dblclick();
    const dialog = page.getByRole('dialog', { name: 'Recover initial detail', exact: true });
    await expect(dialog.getByText('Loading task', { exact: true })).toBeVisible(); await expect(dialog.getByRole('alert')).toContainText('timed out', { timeout: 20_000 });
    await expect(dialog.getByText('Task details unavailable', { exact: true })).toBeVisible(); await page.screenshot({ path: test.info().outputPath('board-initial-detail-timeout.png') });
    await dialog.getByRole('button', { name: 'Retry task details', exact: true }).click(); await expect(dialog.getByRole('alert')).toContainText('did not match'); await expect(dialog.getByRole('textbox', { name: 'Description', exact: true })).toHaveCount(0);
    await dialog.getByRole('button', { name: 'Retry task details', exact: true }).click(); const draft = dialog.getByRole('textbox', { name: 'Description', exact: true }); await expect(draft).toHaveValue('Saved description');
    await draft.fill('Keep this recovered draft'); await draft.focus(); await draft.evaluate((node: HTMLTextAreaElement) => { node.dataset.owner = 'retained'; node.setSelectionRange(2, 9); }); release();
    await expect(draft).toHaveValue('Keep this recovered draft'); await expect(dialog.getByRole('alert')).toHaveCount(0);
    hold = true; const before = connections; await socket!.close({ code: 1012, reason: 'Board detail reconnect recovery' }); await expect.poll(() => connections).toBeGreaterThan(before); await expect.poll(() => held).toBe(2);
    await expect(dialog.getByRole('alert')).toContainText('timed out', { timeout: 20_000 }); await expect(draft).toHaveAttribute('data-owner', 'retained'); await expect(draft).toHaveValue('Keep this recovered draft'); await expect(draft).toBeFocused();
    expect(await draft.evaluate((node: HTMLTextAreaElement) => [node.selectionStart, node.selectionEnd])).toEqual([2, 9]);
    await expect(dialog.getByRole('button', { name: 'Save task', exact: true })).toBeInViewport(); await expect(dialog.getByRole('button', { name: 'Cancel', exact: true })).toBeInViewport();
    await page.setViewportSize({ width: 760, height: 600 }); await expect(dialog.getByRole('button', { name: 'Save task', exact: true })).toBeInViewport(); await expect(dialog.getByRole('button', { name: 'Cancel', exact: true })).toBeInViewport();
    await page.screenshot({ path: test.info().outputPath('board-reconnect-detail-timeout.png') });
    await dialog.getByRole('button', { name: 'Retry task details', exact: true }).click(); await expect(dialog.getByRole('alert')).toHaveCount(0); release(); await expect(draft).toHaveAttribute('data-owner', 'retained'); await expect(draft).toHaveValue('Keep this recovered draft');
    expect(reads).toBe(5); await dialog.getByRole('button', { name: 'Save task', exact: true }).click(); await expect(dialog).toHaveCount(0); expect(((await command(request, { cmd: 'task_detail', id })).task as Row).description).toBe('Keep this recovered draft');
  } finally { release(); }
});
test('Board archive reports a real unknown outcome and retries the current eligible selection without reconnect replay', async ({ page, request }) => {
  test.setTimeout(75_000); const group = await prepare(request, 'Board archive recovery'); const baseline = Date.now();
  const add = async (task: string, lane: string) => String((await command(request, { cmd: 'board_add_task', group, task, lane })).task_id);
  const first = await add('Archive eligible one', 'Done'); const second = await add('Archive eligible two', 'Done'); await add('Keep backlog', 'Backlog');
  let hold = true; let held = false; let release = () => {}; let socket: WebSocketRoute | undefined; let connections = 0; const writes: Row[] = [];
  await page.routeWebSocket(/\/ws\?/, (route) => { route.connectToServer(); socket = route; connections++; });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd === 'board_archive_tasks') { writes.push(data); if (hold) { hold = false; held = true; await new Promise<void>((resolve) => { release = resolve; }); await route.fulfill({ json: { ok: true, data: { type: 'toast', level: 'success', message: 'Obsolete archive acknowledgement' } } }); return; } }
    await route.continue();
  });
  try {
    await page.clock.setFixedTime(baseline + 8 * 86400000); await page.goto('/');
    const suggestion = page.getByRole('region', { name: 'Inactive completed tasks' }); await suggestion.getByRole('button', { name: /Archive 2 completed/ }).click(); await expect.poll(() => held).toBe(true);
    await page.locator('#lane-Backlog').getByRole('button', { name: '＋ Add task', exact: true }).click(); const draft = page.getByRole('textbox', { name: 'New task in Backlog', exact: true }); await draft.fill('Retain inline task draft'); await draft.focus(); await draft.evaluate((node: HTMLInputElement) => { node.dataset.owner = 'same'; node.setSelectionRange(2, 8); });
    await expect(suggestion.getByRole('alert')).toContainText('outcome is unknown', { timeout: 35_000 }); await expect(draft).toHaveValue('Retain inline task draft'); await expect(draft).toBeFocused(); await expect(draft).toHaveAttribute('data-owner', 'same'); expect(await draft.evaluate((node: HTMLInputElement) => [node.selectionStart, node.selectionEnd])).toEqual([2, 8]);
    release(); await expect(page.getByText('Obsolete archive acknowledgement', { exact: true })).toHaveCount(0); await page.screenshot({ path: test.info().outputPath('board-archive-unknown-outcome.png') });
    const before = connections; await socket!.close({ code: 1012, reason: 'Archive must not replay' }); await expect.poll(() => connections).toBeGreaterThan(before); expect(writes).toHaveLength(1); await expect(draft).toHaveValue('Retain inline task draft');
    await command(request, { cmd: 'board_update_task', id: second, lane: 'To Do' }); await suggestion.getByRole('button', { name: /Archive 1 completed/ }).click(); await expect(suggestion).toHaveCount(0);
    expect(writes).toEqual([{ cmd: 'board_archive_tasks', ids: [first, second] }, { cmd: 'board_archive_tasks', ids: [first] }]);
    expect(((await command(request, { cmd: 'task_detail', id: first })).task as Row).lane).toBe('Archived'); expect(((await command(request, { cmd: 'task_detail', id: second })).task as Row).lane).toBe('To Do'); await expect(draft).toHaveValue('Retain inline task draft');
  } finally { release(); }
});
