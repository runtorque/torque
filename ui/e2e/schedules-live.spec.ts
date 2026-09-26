import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
import { mkdtemp, rm } from 'node:fs/promises';
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
  const directory = await mkdtemp(join(tmpdir(), 'torque-schedule-e2e-')); const group = `Schedule QA ${Date.now()}`; const other = `${group} other`;
  for (const name of [group, other]) {
    await command(request, { cmd: 'add_group', group: name });
    await command(request, { cmd: 'update_group_settings', group: name, settings: { default_directory: directory, git_worktree: false, agent_provider: 'generic', agent_boot_command: '/bin/cat', worker_boot_command: '/bin/cat' } });
    await command(request, { cmd: 'save_action', group: name, name: 'schedule/review', scope: 'project', action: { prompt: '{{ TASK }} Scope={{ SCOPE | default("all") }}', description: 'Schedule QA' } });
  }
  await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'board', controlTab: 'mission' } });
  return { directory, group, other };
}
test('Schedules round-trip modes, variables, groups, enable/run metadata and reviewed deletion', async ({ page, request }) => {
  test.setTimeout(90_000); const { directory, group, other } = await fixture(request); const agents: string[] = []; const ids: string[] = [];
  try {
    await page.goto('/'); await page.getByRole('button', { name: 'Schedules', exact: true }).click(); const dialog = page.getByRole('dialog', { name: 'Schedules', exact: true });
    await dialog.getByLabel('Name', { exact: true }).fill('Zulu recurring review'); await dialog.getByLabel('Task title', { exact: true }).fill('Scheduled review {date}');
    await dialog.getByRole('button', { name: 'Monthly', exact: true }).click(); await dialog.getByRole('combobox', { name: 'Action', exact: true }).selectOption('schedule/review'); await dialog.getByRole('textbox', { name: 'SCOPE', exact: true }).fill('Reviewed scope');
    await dialog.getByLabel('Timezone', { exact: true }).fill('UTC'); await dialog.getByRole('button', { name: 'Create schedule', exact: true }).click();
    const row = dialog.getByRole('article', { name: 'Zulu recurring review', exact: true }); await expect(row).toContainText('Recurring · 0 9 1 * *'); await expect(row).toContainText('Timezone: UTC'); await expect(row).toContainText(/Next:.*UTC/); await expect(row).toContainText('0 runs'); await expect(row).not.toContainText('Next: Not scheduled');
    const list = await command(request, { cmd: 'schedule_list' }); const created = (list.schedules as Row[]).find((item) => item.name === 'Zulu recurring review')!; ids.push(String(created.id)); expect(created.action_vars).toEqual({ SCOPE: 'Reviewed scope' });
    await row.getByRole('button', { name: 'Edit', exact: true }).click(); await expect(dialog.getByRole('textbox', { name: 'SCOPE', exact: true })).toHaveValue('Reviewed scope');
    await dialog.getByRole('combobox', { name: 'Group', exact: true }).selectOption(other); await dialog.getByRole('button', { name: 'One-time', exact: true }).click(); await dialog.getByLabel('Date & time', { exact: true }).fill('2035-01-02T10:37');
    await dialog.getByRole('button', { name: 'Save schedule', exact: true }).click(); await expect(row).toContainText(other); await expect(row).toContainText('One-time');
    const saved = ((await command(request, { cmd: 'schedule_list' })).schedules as Row[]).find((item) => item.id === created.id)!;
    expect(saved.cron_expr).toBe(''); const expectedInstant = await page.evaluate(() => new Date('2035-01-02T10:37').toISOString()); expect(saved.scheduled_at).toBe(expectedInstant);
    await row.getByRole('button', { name: 'Edit', exact: true }).click(); await expect(dialog.getByLabel('Date & time', { exact: true })).toHaveValue('2035-01-02T10:37'); await dialog.getByRole('button', { name: 'Cancel edit', exact: true }).click();
    await row.getByRole('button', { name: 'Disable', exact: true }).click(); await expect(row).toContainText('Next: Disabled'); await row.getByRole('button', { name: 'Enable', exact: true }).click(); await expect(row.getByRole('button', { name: 'Disable', exact: true })).toBeVisible();
    await row.getByRole('button', { name: 'Run now', exact: true }).click(); await expect(row).toContainText('1 run');
    const ran = ((await command(request, { cmd: 'schedule_list' })).schedules as Row[]).find((item) => item.id === created.id)!; expect(ran.run_count).toBe(1); expect(ran.last_task_id).toBeTruthy();
    const task = (await command(request, { cmd: 'task_detail', id: ran.last_task_id })).task as Row; expect(task.group).toBe(other); expect(task.action_vars).toEqual({ SCOPE: 'Reviewed scope' }); expect(task.task).not.toContain('{date}'); if (typeof task.agent_id === 'string') agents.push(task.agent_id);
    expect(task.agent_id).toBeTruthy();
    const alpha = await command(request, { cmd: 'schedule_create', name: 'Alpha one-time', group, task_template: 'Future task', scheduled_at: '2035-04-01T01:02:03Z' }); ids.push(String(alpha.schedule_id));
    await expect(dialog.getByRole('article', { name: 'Alpha one-time', exact: true })).toBeVisible(); expect(await dialog.getByRole('article').evaluateAll((nodes) => nodes.map((node) => node.getAttribute('aria-label')))).toEqual(['Alpha one-time', 'Zulu recurring review']);
    await dialog.getByRole('combobox', { name: 'Show schedules', exact: true }).selectOption(group); await expect(row).toHaveCount(0); await dialog.getByRole('combobox', { name: 'Show schedules', exact: true }).selectOption(''); await expect(row).toBeVisible();
    await page.setViewportSize({ width: 760, height: 600 }); await row.scrollIntoViewIfNeeded(); await page.screenshot({ path: test.info().outputPath('schedule-operational-cards.png') });
    await row.getByRole('button', { name: 'Remove', exact: true }).click(); const confirmation = page.getByRole('dialog', { name: 'Remove schedule', exact: true }); await expect(confirmation).toContainText('Zulu recurring review'); await confirmation.getByRole('button', { name: 'Cancel removal', exact: true }).click(); await expect(row).toBeVisible();
    await row.getByRole('button', { name: 'Remove', exact: true }).click(); await confirmation.getByRole('button', { name: 'Confirm removal', exact: true }).click(); await expect(row).toHaveCount(0);
    await page.reload(); await page.getByRole('button', { name: 'Schedules', exact: true }).click(); await expect(row).toHaveCount(0); await expect(dialog.getByRole('article', { name: 'Alpha one-time', exact: true })).toBeVisible();
  } finally {
    for (const id of agents) await command(request, { cmd: 'remove_agent', id });
    for (const id of ids) { const list = (await command(request, { cmd: 'schedule_list' })).schedules as Row[]; if (list.some((row) => row.id === id)) await command(request, { cmd: 'schedule_remove', id }); }
    await rm(directory, { recursive: true, force: true });
  }
});
test('Schedules recover catalog and creation deadlines without losing drafts or duplicating dispatch definitions', async ({ page, request }) => {
  test.setTimeout(90_000); const { directory, group } = await fixture(request); let socket: WebSocketRoute | undefined; let connections = 0; let holdCatalog = false; let releaseCatalog = () => {}; let holdCreation = true; let releaseCreation = () => {}; const creates: Row[] = []; const reads: Row[] = [];
  await page.routeWebSocket(/\/ws\?/, (route) => { route.connectToServer(); socket = route; connections++; });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd === 'list_actions' || data.cmd === 'list_roles' || data.cmd === 'schedule_list') reads.push(data);
    if (data.cmd === 'list_actions' && holdCatalog) { holdCatalog = false; await new Promise<void>((resolve) => { releaseCatalog = resolve; }); await route.fulfill({ json: { ok: true, data: { type: 'actions', group: 'wrong', actions: [{ name: 'obsolete' }] } } }); return; }
    if (data.cmd === 'schedule_create') { creates.push(data); if (holdCreation) { holdCreation = false; const response = await route.fetch(); await new Promise<void>((resolve) => { releaseCreation = resolve; }); await route.fulfill({ response }); return; } }
    await route.continue();
  });
  try {
    await page.goto('/'); await page.getByRole('button', { name: 'Schedules', exact: true }).click(); const dialog = page.getByRole('dialog', { name: 'Schedules', exact: true });
    const name = dialog.getByLabel('Name', { exact: true }); await name.fill('Recovered schedule'); await dialog.getByRole('button', { name: 'Hourly', exact: true }).click(); await name.evaluate((node: HTMLInputElement) => { node.dataset.owner = 'retained'; node.focus(); node.setSelectionRange(2, 8); });
    holdCatalog = true; const before = connections; await socket!.close({ code: 1012, reason: 'Schedule catalog recovery' }); await expect.poll(() => connections).toBeGreaterThan(before);
    await expect(dialog.getByRole('alert')).toContainText('timed out', { timeout: 20_000 }); await expect(name).toHaveValue('Recovered schedule'); await expect(name).toHaveAttribute('data-owner', 'retained'); await expect(name).toBeFocused();
    expect(await name.evaluate((node: HTMLInputElement) => [node.selectionStart, node.selectionEnd])).toEqual([2, 8]);
    await dialog.getByRole('button', { name: 'Retry schedule options', exact: true }).click(); await expect(dialog.getByRole('alert')).toHaveCount(0); releaseCatalog(); await expect(dialog.getByRole('option', { name: 'obsolete', exact: true })).toHaveCount(0);
    await dialog.getByRole('button', { name: 'Create schedule', exact: true }).click(); await expect(dialog.getByRole('alert')).toContainText('outcome is unknown', { timeout: 35_000 }); await expect(name).toBeDisabled();
    await dialog.getByRole('button', { name: 'Retry schedule creation', exact: true }).scrollIntoViewIfNeeded(); await page.screenshot({ path: test.info().outputPath('schedule-creation-recovery.png') }); releaseCreation();
    await dialog.getByRole('button', { name: 'Retry schedule creation', exact: true }).click(); await expect(name).toHaveValue(''); expect(creates).toHaveLength(2); expect(creates[1]).toEqual(creates[0]);
    const matches = ((await command(request, { cmd: 'schedule_list' })).schedules as Row[]).filter((row) => row.group === group && row.name === 'Recovered schedule'); expect(matches).toHaveLength(1); await command(request, { cmd: 'schedule_remove', id: matches[0]!.id });
    await dialog.getByRole('button', { name: 'Close', exact: true }).click(); const readCount = reads.length; const prior = connections; await socket!.close({ code: 1012, reason: 'Hidden schedules stay idle' }); await expect.poll(() => connections).toBeGreaterThan(prior); expect(reads).toHaveLength(readCount);
  } finally { releaseCatalog(); releaseCreation(); await rm(directory, { recursive: true, force: true }); }
});
