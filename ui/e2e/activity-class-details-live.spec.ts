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
test('Activity previews selected and default classes, scopes warnings and retains readable authority across reconnect', async ({ page, request }) => {
  test.setTimeout(90_000); page.setDefaultTimeout(15_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const directory = realpathSync(mkdtempSync(join(tmpdir(), 'torque-class-details-'))); mkdirSync(join(directory, '.torque', 'agent_classes'), { recursive: true });
  const group = `Class details ${Date.now()}`; let id = ''; let socket: WebSocketRoute | undefined; let connections = 0; let refuse = false; const writes: Row[] = [];
  await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections++; });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (['agent_class_assign', 'agent_class_clear'].includes(String(data.cmd))) writes.push(data);
    if (refuse && data.cmd === 'agent_class_list') { refuse = false; await route.fulfill({ json: { ok: false, error: 'QA preview catalog refused' } }); return; }
    await route.continue();
  });
  const base = { agent_class_schema_version: 5, version: '1', base_kind: 'worker', lifecycle: 'stable', prompt: { job: 'Remain available for isolated QA' } };
  const current = { ...base, id: 'qa-current-class', display_name: 'Current QA class', description: 'Current class purpose', acl: { mode: 'allow', rules: [{ capability: 'self.read', scope: 'self' }] }, warnings: ['Frozen QA warning', 'External connectors are not governed by Agent Classes; manage connector access separately.'] };
  const selected = { ...base, id: 'qa-selected-class', version: '2', display_name: 'Selected QA class', description: 'Selected class purpose', lifecycle: 'draft', draft: { scratch_only: true }, acl: { mode: 'deny', rules: [{ capability: 'task.read' }] }, warnings: ['Review Agent Classes before launch.', ' review agent class before launch! ', 'Selected QA warning', 'External connector credentials have expired.'] };
  try {
    await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: directory, git_worktree: false, worker_provider: 'generic', worker_boot_command: '/bin/cat' } }); await command(request, { cmd: 'ui_select_group', group });
    await command(request, { cmd: 'agent_class_create', base_dir: directory, agent_class: current }); await command(request, { cmd: 'agent_class_create', base_dir: directory, agent_class: selected });
    const frame = await command(request, { cmd: 'create_agent_from_class', group, name: 'Class details QA', class_id: current.id, directory, provider: 'generic', command: '/bin/cat', shell: '/bin/sh', worktree: false });
    id = String((frame.agent as Row).id);
    const actual = (await command(request, { cmd: 'agent_class_status', agent_id: id })).status as Row; expect(actual.effective_class_id).toBe(current.id);
    await command(request, { cmd: 'ui_select_agent', id }); await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'agents', controlTab: 'mission' } }); await page.goto('/');
    await page.getByRole('tablist', { name: 'View for Class details QA' }).getByRole('tab', { name: 'Activity' }).click();
    const activity = page.getByRole('region', { name: 'Activity for Class details QA' }); await activity.getByRole('tab', { name: 'Agent Class', exact: true }).click();
    const preview = page.getByRole('region', { name: 'Selected Agent Class preview' }); const facts = page.getByRole('region', { name: 'Current Agent Class status' }); const select = page.getByLabel('Desired class for next launch');
    const save = page.getByRole('button', { name: 'Save assignment', exact: true }); await expect(save).toBeEnabled();
    await expect(preview.getByRole('heading', { name: 'Current QA class@1', exact: true })).toBeVisible(); await expect(facts.getByText('restricted', { exact: true })).toBeVisible();
    await expect(facts.locator('time')).toHaveCount(2);
    const times = await facts.locator('time').evaluateAll((nodes) => nodes.map((node) => node.getAttribute('datetime')));
    expect(times).toEqual([new Date(Number(actual.assigned_at) * 1000).toISOString(), new Date(Number(actual.effective_applied_at) * 1000).toISOString()]);
    await select.selectOption(selected.id); await expect(preview.getByRole('heading', { name: 'Selected QA class@2', exact: true })).toBeVisible(); await expect(preview.getByText('Scratch only', { exact: true })).toBeVisible();
    const currentWarnings = page.getByRole('region', { name: 'Current class warnings' }); const selectedWarnings = page.getByRole('region', { name: 'Selected class warnings' });
    await expect(currentWarnings.getByText('Frozen QA warning', { exact: true })).toBeVisible(); await expect(currentWarnings.getByText('Selected QA warning', { exact: true })).toHaveCount(0);
    await expect(selectedWarnings.getByText('Review Agent Classes before launch.', { exact: true })).toHaveCount(1); await expect(selectedWarnings.getByText(' review agent class before launch! ', { exact: true })).toHaveCount(0); await expect(selectedWarnings.getByText('External connector credentials have expired.', { exact: true })).toBeVisible();
    await expect(activity.getByText(/External connectors are not governed/)).toHaveCount(0);
    const summary = preview.locator('summary').filter({ hasText: 'Allowed actions' }); await summary.click(); const allowed = summary.locator('..');
    await expect(allowed.getByText('self.read', { exact: true })).toBeVisible(); await expect(allowed.getByText('task.read', { exact: true })).toHaveCount(0); await expect(preview.getByRole('region', { name: 'Selected class access' }).getByText('task.read', { exact: true })).toBeVisible();
    await summary.evaluate((node) => node.setAttribute('data-retained', 'yes')); await select.focus(); await select.evaluate((node) => node.setAttribute('data-retained', 'yes'));
    const before = connections; refuse = true; await socket!.close({ code: 1012, reason: 'Preview continuity' }); await expect.poll(() => connections).toBeGreaterThan(before); await expect(page.getByRole('alert')).toContainText('QA preview catalog refused');
    await expect(summary).toHaveAttribute('data-retained', 'yes'); await expect(allowed).toHaveAttribute('open', ''); await expect(select).toHaveValue(selected.id); await expect(select).toBeFocused(); await expect(select).toHaveAttribute('data-retained', 'yes'); await expect(save).toBeDisabled();
    await command(request, { cmd: 'agent_class_update', base_dir: directory, agent_class: { ...selected, version: '3', description: 'Updated selected purpose', warnings: ['Updated selected warning'] } });
    await page.getByRole('button', { name: 'Retry activity' }).click(); await expect(save).toBeEnabled(); await expect(preview.getByRole('heading', { name: 'Selected QA class@3', exact: true })).toBeVisible(); await expect(allowed).toHaveAttribute('open', ''); await expect(select).toHaveValue(selected.id); await expect(currentWarnings.getByText('Frozen QA warning', { exact: true })).toBeVisible(); await expect(selectedWarnings.getByText('Updated selected warning', { exact: true })).toBeVisible();
    expect(writes).toHaveLength(0); await page.screenshot({ path: test.info().outputPath('activity-class-details-desktop.png') });
    await summary.click(); await preview.screenshot({ path: test.info().outputPath('activity-class-details-preview.png') });
    await select.selectOption(''); await expect(preview.getByRole('heading', { name: 'Default Worker@1', exact: true })).toBeVisible(); await expect(preview.getByText(/No explicit class selected/)).toBeVisible(); await expect(preview.getByText('No explicit denials.', { exact: true })).toBeVisible(); await expect(currentWarnings.getByText('Frozen QA warning', { exact: true })).toBeVisible(); expect(writes).toHaveLength(0);
    await page.setViewportSize({ width: 390, height: 844 }); await preview.evaluate((node) => node.scrollIntoView({ block: 'start' })); await expect(preview.getByRole('heading', { level: 3 })).toBeVisible(); expect(await preview.evaluate((node) => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
    await page.screenshot({ path: test.info().outputPath('activity-class-details-narrow.png') });
    await preview.screenshot({ path: test.info().outputPath('activity-class-details-narrow-preview.png') });
  } finally { if (id) await command(request, { cmd: 'remove_agent', id }); rmSync(directory, { recursive: true, force: true }); }
});
