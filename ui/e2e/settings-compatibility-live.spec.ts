import { expect, test, type APIRequestContext } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}
test('Settings omit the inert terminal fallback and preserve its stored value through edit, reset and reload', async ({ page, request }) => {
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Settings compatibility ${Date.now()}`; await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { auto_terminals: 7, max_agents: 3 } }); await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'settings' } });
  const writes: Row[] = []; page.on('request', (request) => { if (new URL(request.url()).pathname === '/api/cmd') { const data = request.postDataJSON() as Row; if (data.cmd === 'update_group_settings') writes.push(data); } });
  await page.goto('/'); const max = page.getByRole('spinbutton', { name: 'Maximum agents', exact: true }); await expect(max).toHaveValue('3'); await page.getByText(`${group} execution, worktrees, notifications and sync`, { exact: true }).click(); await expect(page.getByLabel('Auto terminals', { exact: true })).toHaveCount(0);
  await max.fill('5'); await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible(); expect(writes).toHaveLength(1); expect(writes[0]?.settings).toEqual({ max_agents: 5 }); expect((await command(request, { cmd: 'get_group_settings', group })).settings).toMatchObject({ auto_terminals: 7, max_agents: 5 });
  await page.getByRole('button', { name: 'Reset group defaults', exact: true }).click(); await expect(max).toHaveValue('0'); await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible(); expect(writes).toHaveLength(2); expect(writes[1]?.settings).not.toHaveProperty('auto_terminals'); expect((await command(request, { cmd: 'get_group_settings', group })).settings).toMatchObject({ auto_terminals: 7, max_agents: 0 });
  await page.reload(); await expect(max).toHaveValue('0'); await expect(page.getByLabel('Auto terminals', { exact: true })).toHaveCount(0); await page.getByRole('searchbox', { name: 'Search settings' }).fill('Auto terminals'); await expect(page.getByRole('button', { name: /^Auto terminals — / })).toHaveCount(0); await page.getByRole('searchbox', { name: 'Search settings' }).fill(''); await max.scrollIntoViewIfNeeded(); await page.screenshot({ path: test.info().outputPath('settings-compatibility.png') });
});
