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

test('Settings preserve inactive legacy values and explain editable Classic layout preferences', async ({ page, request }) => {
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const originalGlobal = (await command(request, { cmd: 'get_global_settings' })).settings as Row;
  const group = `Settings legacy scope ${Date.now()}`;
  const preservedGroup = { terminal_always_custom_dialog: true, worktree_merge_instructions: 'Retain this old profile instruction', auto_terminals: 7 };
  await command(request, { cmd: 'add_group', group });
  await command(request, { cmd: 'update_group_settings', group, settings: { ...preservedGroup, max_agents: 3, filter_by_window: true, collapsed_default: true } });
  await command(request, { cmd: 'update_global_settings', settings: { focus_new_tabs: false, filter_by_window: false, default_command: 'cat' } });
  await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'settings' } });
  const writes: Row[] = [];
  page.on('request', (request) => {
    if (new URL(request.url()).pathname !== '/api/cmd') return;
    const data = request.postDataJSON() as Row;
    if (data.cmd === 'update_group_settings' || data.cmd === 'update_global_settings') writes.push(data);
  });
  const assertPreserved = async () => {
    expect((await command(request, { cmd: 'get_global_settings' })).settings).toMatchObject({ focus_new_tabs: false });
    expect((await command(request, { cmd: 'get_group_settings', group })).settings).toMatchObject(preservedGroup);
  };
  const inactive = ['Focus new tabs', 'Terminal always custom dialog', 'Worktree merge instructions', 'Auto terminals'];
  try {
    await page.goto('/');
    const max = page.getByRole('spinbutton', { name: 'Maximum agents', exact: true });
    await expect(max).toHaveValue('3');
    for (const label of inactive) {
      await expect(page.getByLabel(label, { exact: true })).toHaveCount(0);
      await page.getByRole('searchbox', { name: 'Search settings' }).fill(label);
      await expect(page.getByRole('button', { name: new RegExp(`^${label} — `) })).toHaveCount(0);
    }
    await page.getByRole('searchbox', { name: 'Search settings' }).fill('');
    await max.fill('5');
    await page.getByRole('button', { name: 'Save changes', exact: true }).click();
    await expect(page.getByText('Saved', { exact: true })).toBeVisible();
    expect(writes).toHaveLength(1); expect(writes[0]?.settings).toEqual({ max_agents: 5 });
    await assertPreserved();

    await page.getByRole('button', { name: 'Reset global defaults', exact: true }).click();
    await page.getByRole('button', { name: 'Reset group defaults', exact: true }).click();
    await page.getByRole('button', { name: 'Save changes', exact: true }).click();
    await expect(page.getByText('Saved', { exact: true })).toBeVisible();
    expect(writes).toHaveLength(3);
    for (const write of writes) {
      for (const key of ['focus_new_tabs', ...Object.keys(preservedGroup)]) expect(write.settings).not.toHaveProperty(key);
    }
    await assertPreserved();
    await page.reload(); await expect(max).toHaveValue('0');
    for (const label of inactive) await expect(page.getByLabel(label, { exact: true })).toHaveCount(0);

    const global = page.locator('details').filter({ has: page.getByText('Global defaults', { exact: true }) });
    const grouped = page.locator('details').filter({ has: page.getByText(`${group} execution, worktrees, notifications and sync`, { exact: true }) });
    await global.locator('summary').first().click();
    await grouped.locator('summary').first().click();
    for (const scope of [global, grouped]) await expect(scope.getByLabel('Filter by window', { exact: true })).toHaveAccessibleDescription(/Classic workspace only/);
    await expect(grouped.getByLabel('Collapsed default', { exact: true })).toHaveAccessibleDescription(/Classic workspace only/);
    await global.getByLabel('Filter by window', { exact: true }).selectOption('false');
    await grouped.getByLabel('Filter by window', { exact: true }).selectOption('true');
    await grouped.getByLabel('Collapsed default', { exact: true }).selectOption('true');
    await page.getByRole('button', { name: 'Save changes', exact: true }).click();
    await expect(page.getByText('Saved', { exact: true })).toBeVisible();
    expect((await command(request, { cmd: 'get_global_settings' })).settings).toMatchObject({ filter_by_window: false });
    expect((await command(request, { cmd: 'get_group_settings', group })).settings).toMatchObject({ filter_by_window: true, collapsed_default: true });
    await assertPreserved();
    await page.reload(); await expect(max).toHaveValue('0');
    await expect(global.getByLabel('Filter by window', { exact: true })).toHaveValue('false');
    await expect(grouped.getByLabel('Filter by window', { exact: true })).toHaveValue('true');
    await expect(grouped.getByLabel('Collapsed default', { exact: true })).toHaveValue('true');
    await grouped.locator('summary').first().click();
    await page.setViewportSize({ width: 760, height: 720 });
    await grouped.getByLabel('Collapsed default', { exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: test.info().outputPath('settings-classic-scope.png') });
  } finally {
    await command(request, { cmd: 'update_global_settings', settings: originalGlobal });
  }
});
