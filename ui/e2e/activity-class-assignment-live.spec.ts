import { mkdtempSync, mkdirSync, realpathSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect as baseExpect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
const expect = baseExpect.configure({ timeout: 15_000 });
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}
test('Activity acknowledges assignments once, follows external changes and refreshes effective status after relaunch', async ({ page, request }) => {
  test.setTimeout(90_000); page.setDefaultTimeout(15_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const directory = realpathSync(mkdtempSync(join(tmpdir(), 'torque-class-assignment-'))); mkdirSync(join(directory, '.torque', 'agent_classes'), { recursive: true });
  const group = `Class assignment ${Date.now()}`; let id = ''; let socket: WebSocketRoute | undefined; let connections = 0;
  let hold = true; let refuse = false; let release!: () => void; const held = new Promise<void>((resolve) => { release = resolve; }); const writes: Row[] = [];
  await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections++; });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (['agent_class_assign', 'agent_class_clear'].includes(String(data.cmd))) {
      writes.push(data);
      if (refuse) { refuse = false; await route.fulfill({ json: { ok: false, error: 'QA assignment refused' } }); return; }
      if (hold) { hold = false; const response = await route.fetch(); await held; await route.fulfill({ response }); return; }
    }
    await route.continue();
  });
  try {
    await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: directory, git_worktree: false, worker_provider: 'generic', worker_boot_command: '/bin/cat', agent_shell: '/bin/sh' } }); await command(request, { cmd: 'ui_select_group', group });
    for (const classId of ['qa-class-a', 'qa-class-b']) await command(request, { cmd: 'agent_class_create', base_dir: directory, agent_class: { agent_class_schema_version: 5, id: classId, version: '1', base_kind: 'worker', display_name: classId, description: 'Isolated assignment acceptance', lifecycle: 'stable', acl: { mode: 'deny', rules: [] }, prompt: { job: 'Remain available for isolated QA' } } });
    const frame = await command(request, { cmd: 'add_agent', group, name: 'Assignment QA', directory, provider: 'generic', command: '/bin/cat', shell: '/bin/sh', worktree: false });
    id = String(Object.values(frame.agents as Record<string, Row>).find((row) => row.group === group && row.name === 'Assignment QA')!.id);
    await command(request, { cmd: 'ui_select_agent', id }); await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'agents', controlTab: 'mission' } }); await page.goto('/');
    await page.getByRole('tablist', { name: 'View for Assignment QA' }).getByRole('tab', { name: 'Activity' }).click();
    const activity = page.getByRole('region', { name: 'Activity for Assignment QA' }); await activity.getByRole('tab', { name: 'Agent Class', exact: true }).click();
    const select = page.getByLabel('Desired class for next launch'); const save = page.getByRole('button', { name: 'Save assignment', exact: true });
    const fact = (label: string) => page.getByRole('region', { name: 'Agent Class assignment' }).locator('dl > div').filter({ has: page.locator('dt', { hasText: new RegExp(`^${label}$`) }) }).locator('dd');
    await expect(save).toBeEnabled(); await select.selectOption('qa-class-a'); await save.click();
    await expect(page.getByText('Saving assignment…')).toBeVisible(); await expect(save).toBeDisabled(); await expect(select).toBeDisabled();
    await expect.poll(async () => (await command(request, { cmd: 'agent_class_status', agent_id: id })).status).toMatchObject({ assigned_class_id: 'qa-class-a' });
    const before = connections; await socket!.close({ code: 1012, reason: 'Pending assignment acknowledgement' }); await expect.poll(() => connections).toBeGreaterThan(before); await expect(save).toBeDisabled(); expect(writes).toHaveLength(1);
    await page.getByRole('tablist', { name: 'View for Assignment QA' }).getByRole('tab', { name: 'Live', exact: true }).click();
    await page.getByRole('tablist', { name: 'View for Assignment QA' }).getByRole('tab', { name: 'Activity' }).click(); await activity.getByRole('tab', { name: 'Agent Class', exact: true }).click();
    await expect(save).toBeDisabled(); await expect(select).toHaveValue('qa-class-a'); expect(writes).toHaveLength(1);
    release(); await expect(page.getByText('Assignment saved. Applies at the next launch.')).toBeVisible(); await expect(save).toBeEnabled(); await expect(select).toHaveValue('qa-class-a');
    await expect(fact('Desired')).toHaveText('qa-class-a');
    // External writes trigger fresh status through the live agent projection.
    await command(request, { cmd: 'agent_class_assign', agent_id: id, class_id: 'qa-class-b', base_dir: directory }); await expect(fact('Desired')).toHaveText('qa-class-b'); await expect(select).toHaveValue('qa-class-b');
    await select.selectOption('qa-class-a'); await select.focus(); await select.evaluate((node) => node.setAttribute('data-retained', 'yes'));
    await command(request, { cmd: 'agent_class_clear', agent_id: id }); await expect(select).toHaveValue('qa-class-a'); await expect(select).toBeFocused(); await expect(select).toHaveAttribute('data-retained', 'yes'); await expect(fact('Desired')).not.toHaveText('qa-class-b');
    refuse = true; await expect(save).toBeEnabled(); await save.click(); await expect(page.getByRole('alert')).toContainText('QA assignment refused'); await expect(select).toHaveValue('qa-class-a'); expect(writes).toHaveLength(2);
    await activity.getByRole('button', { name: 'Refresh', exact: true }).click(); await expect(save).toBeEnabled(); expect(writes).toHaveLength(2);
    await save.click(); await expect(page.getByText('Assignment saved. Applies at the next launch.')).toBeVisible(); await expect(save).toBeEnabled(); expect(writes).toHaveLength(3);
    await expect(fact('Apply state')).toHaveText('Pending relaunch'); await expect(page.getByRole('button', { name: 'Relaunch to apply', exact: true })).toHaveCount(0);
    await expect(page.getByText('The running session keeps its current class. The desired class applies at the next launch or relaunch.')).toBeVisible();
    // Restore the isolated fixture as stopped, then exercise the same relaunch
    // command available from Classic. Running agents must never get a no-op button.
    await command(request, { cmd: 'remove_agent', id }); await command(request, { cmd: 'restore_agent', id }); await command(request, { cmd: 'ui_select_agent', id });
    await page.getByRole('tablist', { name: 'View for Assignment QA' }).getByRole('tab', { name: 'Activity' }).click();
    await activity.getByRole('tab', { name: 'Agent Class', exact: true }).click(); await expect(save).toBeEnabled();
    await page.getByRole('button', { name: 'Relaunch to apply', exact: true }).click();
    await expect.poll(async () => (await command(request, { cmd: 'agent_class_status', agent_id: id })).status, { timeout: 15_000 }).toMatchObject({ effective_class_id: 'qa-class-a', pending_next_launch: false });
    await expect(fact('Effective now')).toHaveText('qa-class-a'); await expect(fact('Apply state')).toHaveText('Current'); await expect(page.getByRole('button', { name: 'Relaunch to apply', exact: true })).toHaveCount(0);
    await select.selectOption(''); await save.click(); await expect(page.getByText('Assignment cleared. The default applies at the next launch.')).toBeVisible(); await expect(select).toHaveValue('');
    await expect.poll(async () => (await command(request, { cmd: 'agent_class_status', agent_id: id })).status).toMatchObject({ assigned_class_id: '', effective_class_id: 'qa-class-a', pending_next_launch: true });
    await page.screenshot({ path: test.info().outputPath('activity-class-assignment.png') });
  } finally { release(); if (id) await command(request, { cmd: 'remove_agent', id }); rmSync(directory, { recursive: true, force: true }); }
});
