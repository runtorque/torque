import { mkdtempSync, mkdirSync, realpathSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}
test('Activity discovers and assigns classes from each agent project and retains unavailable choices through reconnect', async ({ page, request }) => {
  test.setTimeout(90_000); page.setDefaultTimeout(15_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'torque-activity-class-scope-'))); const projectA = join(root, 'a'); const projectB = join(root, 'b'); const ids: string[] = [];
  for (const project of [projectA, projectB]) mkdirSync(join(project, '.torque', 'agent_classes'), { recursive: true });
  const group = `Activity class scope ${Date.now()}`; const classId = 'scoped-worker';
  let socket: WebSocketRoute | undefined; let connections = 0; let refusal = false; const reads: Row[] = [];
  await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections++; });
  await page.route('**/api/cmd', async (route) => { const data = route.request().postDataJSON() as Row; if (data.cmd === 'agent_class_list') { reads.push(data); if (refusal) { refusal = false; await route.fulfill({ json: { ok: false, error: 'QA project catalog refused' } }); return; } } await route.continue(); });
  try {
    await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: projectA, git_worktree: false } }); await command(request, { cmd: 'ui_select_group', group });
    for (const [name, directory] of [['Project A class', projectA], ['Project B class', projectB]]) {
      await command(request, { cmd: 'agent_class_create', base_dir: directory, agent_class: { agent_class_schema_version: 5, id: classId, version: '1', base_kind: 'worker', display_name: name, description: `${name} description`, lifecycle: 'stable', acl: { mode: 'deny', rules: [] }, prompt: { job: 'Remain available for isolated QA' } } });
      const frame = await command(request, { cmd: 'add_agent', group, name, directory, provider: 'generic', command: '/bin/cat', shell: '/bin/sh', worktree: false });
      ids.push(String(Object.values(frame.agents as Record<string, Row>).find((row) => row.group === group && row.name === name)!.id));
    }
    await command(request, { cmd: 'ui_select_agent', id: ids[0] }); await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'agents', controlTab: 'mission' } }); await page.goto('/');
    await page.getByRole('tablist', { name: 'View for Project A class' }).getByRole('tab', { name: 'Activity' }).click();
    await page.getByRole('region', { name: 'Activity for Project A class' }).getByRole('tab', { name: 'Agent Class', exact: true }).click();
    const select = page.getByLabel('Desired class for next launch'); const save = page.getByRole('button', { name: 'Save assignment', exact: true });
    await expect(select.locator('option', { hasText: 'Project A class' })).toHaveCount(1); await expect(select.locator('option', { hasText: 'Project B class' })).toHaveCount(0); expect(reads.at(-1)).toMatchObject({ group, base_dir: projectA });
    await select.selectOption(classId); await expect(save).toBeEnabled(); await save.click(); await expect.poll(async () => (await command(request, { cmd: 'agent_class_status', agent_id: ids[0] })).status).toMatchObject({ assigned_class_id: classId });
    await select.selectOption(''); await save.click(); await expect.poll(async () => (await command(request, { cmd: 'agent_class_status', agent_id: ids[0] })).status).toMatchObject({ assigned_class_id: '' });
    await page.locator(`[role="treeitem"][data-agent-id="${ids[1]}"]`).click(); await page.getByRole('region', { name: 'Activity for Project B class' }).getByRole('tab', { name: 'Agent Class', exact: true }).click();
    await expect(select.locator('option', { hasText: 'Project B class' })).toHaveCount(1); await expect(select.locator('option', { hasText: 'Project A class' })).toHaveCount(0); expect(reads.at(-1)).toMatchObject({ group, base_dir: projectB });
    await select.selectOption(classId); await select.focus(); await select.evaluate((node) => node.setAttribute('data-retained', 'yes')); const before = connections; refusal = true; await socket!.close({ code: 1012, reason: 'Project class draft reconnect' }); await expect.poll(() => connections).toBeGreaterThan(before);
    await expect(page.getByRole('alert')).toContainText('QA project catalog refused'); await expect(select).toHaveValue(classId); await expect(select).toBeFocused(); await expect(select).toHaveAttribute('data-retained', 'yes'); await expect(select.locator('option:checked')).toHaveText('Project B class'); await expect(save).toBeDisabled();
    await page.getByRole('button', { name: 'Retry activity' }).click(); await expect(page.getByRole('alert')).toHaveCount(0); await expect(save).toBeEnabled(); await expect(select).toHaveValue(classId);
    await command(request, { cmd: 'agent_class_archive', base_dir: projectB, class_id: classId }); await page.getByRole('region', { name: 'Activity for Project B class' }).getByRole('button', { name: 'Refresh', exact: true }).click();
    await expect(select.locator('option:checked')).toHaveText('Project B class (unavailable)'); await expect(select.locator('option:checked')).toHaveJSProperty('disabled', true); await expect(save).toBeDisabled(); await expect(page.getByText('Archived or disabled Agent Classes cannot launch.')).toBeVisible();
    await command(request, { cmd: 'agent_class_delete', base_dir: projectB, class_id: classId }); await page.getByRole('region', { name: 'Activity for Project B class' }).getByRole('button', { name: 'Refresh', exact: true }).click(); await expect(select.locator('option:checked')).toHaveText(`${classId} (unavailable)`); await expect(save).toBeDisabled();
    await page.screenshot({ path: test.info().outputPath('activity-class-scope.png') });
    await select.selectOption(''); await expect(save).toBeEnabled(); await save.click();
  } finally { for (const id of ids.reverse()) await command(request, { cmd: 'remove_agent', id }); rmSync(root, { recursive: true, force: true }); }
});
