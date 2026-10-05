import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
import { mkdtemp, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const response = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(response.ok, response.error).toBe(true); expect(response.data.type).not.toBe('error'); return response.data;
}

test('Saved Board defaults govern general task creation while explicit lanes and labels survive reconnect and refusal', async ({ page, request }) => {
  test.setTimeout(90_000); page.setDefaultTimeout(12_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const directory = await realpath(await mkdtemp(join(tmpdir(), 'torque-board-defaults-')));
  const group = `Board defaults ${Date.now()}`; const ids: string[] = []; const writes: Row[] = []; const results: Row[] = [];
  let socket: WebSocketRoute | undefined; let connections = 0; let refuse = false;
  await command(request, { cmd: 'add_group', group });
  await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: directory, board_sync_enabled: false } });
  await command(request, { cmd: 'save_action', group, name: 'qa/default', scope: 'project', action: { prompt: 'QA task: {{ TASK }}', description: 'Board settings acceptance' } });
  await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'settings' } });
  await page.routeWebSocket(/\/ws\?/, (client) => { socket = client; connections++; client.connectToServer(); });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd !== 'board_add_task') { await route.continue(); return; }
    writes.push(data);
    if (refuse) { refuse = false; await route.fulfill({ json: { ok: false, creation_refused: true, error: 'Board default fixture refusal' } }); return; }
    const response = await route.fetch(); const body = await response.json() as { ok: boolean; error?: string; data: Row };
    expect(body.ok, body.error).toBe(true);
    if (typeof body.data.task_id === 'string') ids.push(body.data.task_id); await route.fulfill({ response });
  });
  const reveal = async (label: string) => { await page.getByRole('searchbox', { name: 'Search settings' }).fill(label); await page.getByRole('button', { name: new RegExp(`^${label} — `) }).click(); return page.getByLabel(label, { exact: true }); };
  const save = async () => { await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible(); };
  const openTask = async (title: string) => {
    await page.getByRole('button', { name: /▦ Board/ }).click(); await page.getByRole('button', { name: '＋ New task', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Create task', exact: true });
    await dialog.getByLabel('Title', { exact: true }).fill(title); await expect(dialog.getByRole('combobox', { name: 'Lane', exact: true })).toHaveValue(''); return dialog;
  };
  const verify = async (expected: Row) => {
    const id = ids.at(-1)!; expect(id).toBeTruthy();
    const task = (await command(request, { cmd: 'task_detail', id })).task as Row;
    expect(task).toMatchObject(expected); results.push({ task, submitted: writes.at(-1) });
  };
  try {
    await page.goto('/');
    for (const [label, value] of [['Board default lane', 'To Do'], ['Board default labels', 'qa-default\nneeds-review'], ['Board default action', 'qa/default']] as const) await (await reveal(label)).fill(value);
    await save(); await page.reload();
    await expect(await reveal('Board default lane')).toHaveValue('To Do');
    await expect(await reveal('Board default labels')).toHaveValue('qa-default\nneeds-review');
    await expect(await reveal('Board default action')).toHaveValue('qa/default');
    let dialog = await openTask('Inherited Board defaults');
    await dialog.getByRole('button', { name: 'Create task', exact: true }).click(); await expect(dialog).toHaveCount(0);
    expect(writes.at(-1)).toMatchObject({ lane: '', labels: [], action_name: '' });
    await verify({ lane: 'To Do', labels: ['qa-default', 'needs-review'], action_name: 'qa/default' });
    dialog = await openTask('Explicit Board defaults');
    const lane = dialog.getByRole('combobox', { name: 'Lane', exact: true }); await lane.selectOption('Backlog');
    await dialog.getByLabel('Labels', { exact: true }).fill('explicit-label'); await lane.focus();
    const before = connections; await socket!.close({ code: 1012, reason: 'Explicit task lane draft' });
    await expect.poll(() => connections).toBeGreaterThan(before); await expect(lane).toBeFocused(); await expect(lane).toHaveValue('Backlog');
    refuse = true; await dialog.getByRole('button', { name: 'Create task', exact: true }).click(); await expect(dialog.getByRole('alert')).toContainText('Board default fixture refusal');
    await expect(lane).toHaveValue('Backlog'); await expect(dialog.getByLabel('Labels', { exact: true })).toHaveValue('explicit-label');
    await dialog.getByRole('button', { name: 'Create task', exact: true }).click(); await expect(dialog).toHaveCount(0);
    await verify({ lane: 'Backlog', labels: ['explicit-label'], action_name: 'qa/default' });
    await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click();
    for (const label of ['Board default lane', 'Board default labels', 'Board default action']) await (await reveal(label)).fill('');
    await save(); await page.reload();
    dialog = await openTask('Cleared Board defaults');
    await dialog.getByRole('button', { name: 'Create task', exact: true }).click(); await expect(dialog).toHaveCount(0);
    await verify({ lane: 'Backlog', labels: [], action_name: '' });
    await page.screenshot({ path: test.info().outputPath('board-default-lane.png'), animations: 'disabled' });
    const path = test.info().outputPath('board-defaults-evidence.json'); await writeFile(path, JSON.stringify(results, null, 2)); await test.info().attach('board-defaults-evidence', { path, contentType: 'application/json' });
  } finally { for (const id of ids) await command(request, { cmd: 'board_remove_task', id }); await rm(directory, { recursive: true, force: true }); }
});
