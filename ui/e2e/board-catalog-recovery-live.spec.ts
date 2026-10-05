import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
import { mkdir, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}
async function fixture(request: APIRequestContext) {
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const directory = await mkdtemp(join(tmpdir(), 'torque-board-catalog-')); const group = `Catalog QA ${Date.now()}`; const other = `${group} other`;
  for (const [name, prefix] of [[group, 'first'], [other, 'other']]) {
    await mkdir(join(directory, prefix!), { recursive: true });
    await command(request, { cmd: 'add_group', group: name });
    await command(request, { cmd: 'update_group_settings', group: name, settings: { default_directory: join(directory, prefix!), git_worktree: false, agent_provider: 'generic', agent_boot_command: '/bin/cat' } });
    await command(request, { cmd: 'save_action', group: name, name: `${prefix}/review`, scope: 'project', action: { prompt: '{{ TASK }} Scope={{ SCOPE | default("all") }}' } });
    await command(request, { cmd: 'save_role', group: name, name: `${prefix}-role`, scope: 'project', data: { provider: 'generic', command: '/bin/cat', worktree: false } });
  }
  const ids: string[] = [];
  for (const task of ['Catalog first task', 'Catalog second task']) ids.push(String((await command(request, { cmd: 'board_add_task', group, task, description: 'Saved description', action_name: 'first/review', agent_template: 'first-role' })).task_id));
  await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'board', controlTab: 'mission' } });
  return { directory, group, other, ids };
}
test('Board discovers batch actions first and preserves create options and drafts through catalog timeout and reconnect', async ({ page, request }) => {
  test.setTimeout(75_000); const { directory, group } = await fixture(request); let socket: WebSocketRoute | undefined; let connections = 0; let hold = false; let held = false; let release = () => {}; const reads: Row[] = [];
  await page.routeWebSocket(/\/ws\?/, (route) => { route.connectToServer(); socket = route; connections++; });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd === 'list_actions' || data.cmd === 'list_roles') reads.push(data);
    if (data.cmd === 'list_actions' && hold) { hold = false; held = true; await new Promise<void>((resolve) => { release = resolve; }); await route.fulfill({ json: { ok: true, data: { type: 'actions', group: 'Wrong', actions: [{ name: 'late/action' }] } } }); return; }
    await route.continue();
  });
  const reconnect = async () => { const before = connections; await socket!.close({ code: 1012, reason: 'Catalog recovery' }); await expect.poll(() => connections).toBeGreaterThan(before); await expect(page.getByText('connected', { exact: true })).toBeVisible(); };
  try {
    await page.goto('/'); await expect(page.getByLabel('Catalog first task, Backlog', { exact: true })).toBeVisible(); expect(reads).toHaveLength(0);
    await page.getByLabel('Catalog first task, Backlog', { exact: true }).click(); await page.getByLabel('Catalog second task, Backlog', { exact: true }).click({ modifiers: ['Shift'] });
    await page.getByRole('button', { name: 'Batch edit', exact: true }).click(); const batch = page.getByRole('dialog', { name: 'Batch edit tasks', exact: true });
    await batch.getByRole('combobox', { name: 'Action', exact: true }).selectOption('first/review'); expect(reads).toEqual([{ cmd: 'list_actions', group }]);
    await batch.getByRole('textbox', { name: 'Add label', exact: true }).fill('Reviewed batch label'); await reconnect(); await expect(batch.getByRole('combobox', { name: 'Action', exact: true })).toHaveValue('first/review'); await expect(batch.getByRole('textbox', { name: 'Add label', exact: true })).toHaveValue('Reviewed batch label');
    await batch.getByRole('button', { name: 'Cancel', exact: true }).click();
    await page.getByRole('button', { name: '＋ New task', exact: true }).click(); const dialog = page.getByRole('dialog', { name: 'Create task', exact: true });
    await dialog.getByRole('combobox', { name: 'Action', exact: true }).selectOption('first/review'); await dialog.getByRole('textbox', { name: 'SCOPE', exact: true }).fill('Reviewed scope');
    await dialog.getByRole('combobox', { name: 'Worker role', exact: true }).selectOption('first-role');
    const title = dialog.getByRole('textbox', { name: 'Title', exact: true }); await title.fill('Catalog recovery task'); await title.focus(); await title.evaluate((node: HTMLInputElement) => { node.dataset.owner = 'retained'; node.setSelectionRange(2, 8); });
    hold = true; await reconnect(); await expect.poll(() => held).toBe(true); await expect(dialog.getByRole('alert')).toContainText('timed out', { timeout: 20_000 });
    await expect(title).toHaveAttribute('data-owner', 'retained'); await expect(title).toHaveValue('Catalog recovery task'); await expect(title).toBeFocused(); expect(await title.evaluate((node: HTMLInputElement) => [node.selectionStart, node.selectionEnd])).toEqual([2, 8]);
    await expect(dialog.getByRole('combobox', { name: 'Action', exact: true })).toHaveValue('first/review'); await expect(dialog.getByRole('textbox', { name: 'SCOPE', exact: true })).toHaveValue('Reviewed scope');
    await page.setViewportSize({ width: 760, height: 600 }); await page.screenshot({ path: test.info().outputPath('board-create-catalog-timeout.png') });
    await dialog.getByRole('button', { name: 'Retry task options', exact: true }).click(); await expect(dialog.getByRole('alert')).toHaveCount(0); release(); await expect(dialog.getByRole('option', { name: 'late/action', exact: true })).toHaveCount(0);
    await dialog.getByRole('button', { name: 'Create task', exact: true }).click(); await expect(dialog).toHaveCount(0);
    const snapshot = await command(request, { cmd: 'get_state' }); const created = Object.values(snapshot.board_tasks as Record<string, Row>).find((task) => task.task === 'Catalog recovery task')!; expect(created).toBeTruthy();
    const saved = (await command(request, { cmd: 'task_detail', id: created.id })).task as Row; expect(saved.action_name).toBe('first/review'); expect(saved.agent_template).toBe('first-role'); expect(saved.action_vars).toEqual({ SCOPE: 'Reviewed scope' });
    const count = reads.length; await reconnect(); expect(reads).toHaveLength(count);
  } finally { release(); await rm(directory, { recursive: true, force: true }); }
});
test('Board task editing follows its target group and retries wrong-group catalogs without changing saved selections', async ({ page, request }) => {
  test.setTimeout(45_000); const { directory, other, ids } = await fixture(request); let wrong = true; const reads: Row[] = []; let socket: WebSocketRoute | undefined; let connections = 0;
  await page.routeWebSocket(/\/ws\?/, (route) => { route.connectToServer(); socket = route; connections++; });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd === 'list_actions' || data.cmd === 'list_roles') reads.push(data);
    if (wrong && data.cmd === 'list_actions' && data.group === other) { wrong = false; await route.fulfill({ json: { ok: true, data: { type: 'actions', group: 'Wrong', actions: [{ name: 'wrong/action' }] } } }); return; }
    await route.continue();
  });
  try {
    await page.goto('/'); await page.getByLabel('Catalog first task, Backlog', { exact: true }).dblclick(); const dialog = page.getByRole('dialog', { name: 'Catalog first task', exact: true });
    await dialog.getByRole('combobox', { name: 'Action', exact: true }).selectOption('first/review'); const title = dialog.getByRole('textbox', { name: 'Title', exact: true }); await title.fill('Reviewed target group');
    await dialog.getByRole('combobox', { name: 'Group', exact: true }).selectOption(other); await expect(dialog.getByRole('alert')).toContainText('unrelated or invalid response'); await expect(dialog.getByRole('option', { name: 'wrong/action', exact: true })).toHaveCount(0);
    await dialog.getByRole('button', { name: 'Retry task options', exact: true }).click(); await expect(dialog.getByRole('alert')).toHaveCount(0); await expect(dialog.getByRole('option', { name: 'other/review', exact: true })).toHaveCount(1);
    await expect(dialog.getByRole('option', { name: 'first/review (unavailable)', exact: true })).toHaveCount(1); await expect(dialog.getByRole('combobox', { name: 'Worker role', exact: true })).toHaveValue('first-role'); await expect(title).toHaveValue('Reviewed target group');
    await dialog.getByRole('tab', { name: 'Evidence', exact: true }).click(); const count = reads.length; const before = connections; await socket!.close({ code: 1012, reason: 'Hidden execution catalog' }); await expect.poll(() => connections).toBeGreaterThan(before); await expect(page.getByText('connected', { exact: true })).toBeVisible(); expect(reads).toHaveLength(count);
    await dialog.getByRole('tab', { name: 'Execution', exact: true }).click(); await expect.poll(() => reads.length).toBe(count + 2); expect(reads.slice(-2).map((row) => row.group)).toEqual([other, other]);
    await dialog.getByRole('combobox', { name: 'Action', exact: true }).selectOption('other/review'); await dialog.getByRole('combobox', { name: 'Worker role', exact: true }).selectOption('other-role'); await dialog.getByRole('textbox', { name: 'SCOPE', exact: true }).fill('Other group scope');
    await dialog.getByRole('button', { name: 'Save task', exact: true }).click(); await expect(dialog).toHaveCount(0); const saved = (await command(request, { cmd: 'task_detail', id: ids[0] })).task as Row;
    expect(saved).toMatchObject({ group: other, task: 'Reviewed target group', action_name: 'other/review', agent_template: 'other-role', action_vars: { SCOPE: 'Other group scope' } });
  } finally { await rm(directory, { recursive: true, force: true }); }
});
