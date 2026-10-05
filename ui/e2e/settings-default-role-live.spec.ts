import { mkdtempSync, mkdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}
test('default role discovery keeps scoped choices and missing selections through save and catalog refresh', async ({ page, request }) => {
  test.setTimeout(90_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const project = mkdtempSync(join(tmpdir(), 'torque-default-role-')); mkdirSync(join(project, '.torque', 'roles'), { recursive: true });
  const group = `Default role ${Date.now()}`; const role = 'qa-default-reviewer';
  const writeRole = (label: string) => command(request, { cmd: 'save_role', group, scope: 'project', name: role, data: { name: role, display_name: label, description: 'Default role QA', preamble: 'Review locally', provider: 'generic', worktree: false } });
  const writes: Row[] = []; let socket: WebSocketRoute | undefined; let connections = 0;
  await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections++; });
  await page.route('**/api/cmd', async (route) => { const data = route.request().postDataJSON() as Row; if (/^(update_|engineer_update_)/.test(String(data.cmd))) writes.push(data); await route.continue(); });
  const open = async () => { await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click(); await page.getByText(`${group} execution, worktrees, notifications and sync`, { exact: true }).click(); };
  const picker = page.getByRole('combobox', { name: 'Default agent template', exact: true });
  const reconnect = async () => { const before = connections; await socket!.close({ code: 1012, reason: 'Default role catalog refresh' }); await expect.poll(() => connections).toBeGreaterThan(before); };
  const save = async () => { await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible(); };
  try {
    await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: project, default_agent_template: 'saved-unavailable-role', agent_model: 'keep-shared-model' } }); await command(request, { cmd: 'ui_select_group', group }); await writeRole('Project QA reviewer');
    await page.goto('/'); await open(); await expect(picker).toHaveValue('saved-unavailable-role'); await expect(picker.getByRole('option', { name: 'saved-unavailable-role (not in current catalog)' })).toHaveCount(1);
    await expect(picker.locator('optgroup[label="Project"]').getByRole('option', { name: 'Project QA reviewer', exact: true })).toHaveCount(1);
    await picker.selectOption(role); await picker.focus(); await picker.evaluate((select) => { (select as HTMLSelectElement).dataset.retained = 'yes'; }); expect(writes).toHaveLength(0);
    await command(request, { cmd: 'delete_role', group, scope: 'project', name: role }); await reconnect();
    await expect(picker.getByRole('option', { name: `${role} (not in current catalog)` })).toHaveCount(1); await expect(picker).toHaveValue(role); await expect(picker).toBeFocused(); await expect(picker).toHaveAttribute('data-retained', 'yes'); expect(writes).toHaveLength(0);
    await writeRole('Updated QA reviewer'); await reconnect(); await expect(picker.getByRole('option', { name: 'Updated QA reviewer', exact: true })).toHaveCount(1); await expect(picker).toHaveValue(role); await expect(picker).toBeFocused();
    await save(); expect(writes).toHaveLength(1); expect(writes[0]).toMatchObject({ cmd: 'update_group_settings', group, settings: { default_agent_template: role } }); expect(Object.keys(writes[0]!.settings as Row)).toEqual(['default_agent_template']);
    expect((await command(request, { cmd: 'get_group_settings', group })).settings).toMatchObject({ default_agent_template: role, agent_model: 'keep-shared-model' });
    await page.reload(); await open(); await expect(picker).toHaveValue(role); await expect(picker.getByRole('option', { name: 'Updated QA reviewer', exact: true })).toHaveCount(1);
    await picker.evaluate((select) => select.scrollIntoView({ block: 'center' })); await page.screenshot({ path: test.info().outputPath('default-role-picker.png') });
    await picker.selectOption(''); await save(); expect((await command(request, { cmd: 'get_group_settings', group })).settings).toMatchObject({ default_agent_template: '', agent_model: 'keep-shared-model' });
    await picker.selectOption(role); await page.getByRole('button', { name: 'Reset Default agent template', exact: true }).click(); await expect(picker).toHaveValue(''); await expect(page.getByLabel('Agent model', { exact: true })).toHaveValue('keep-shared-model');
  } finally { rmSync(project, { recursive: true, force: true }); }
});
