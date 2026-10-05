import { expect, test, type APIRequestContext } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
test('settings retain numeric drafts, reject invalid boundaries and retry only unfinished scopes', async ({ page, request }) => {
  const runtime = await (await request.get('/api/runtime')).json() as { data: { runtime: { port: number; profile: string } } };
  expect(runtime.data.runtime.port).not.toBe(18932); expect(runtime.data.runtime.profile).not.toBe('default');
  await command(request, { cmd: 'update_global_settings', settings: { xterm_scrollback: 2000, max_event_log: 500, max_pipeline_depth: 10 } });
  await command(request, { cmd: 'update_ai_settings', settings: { ai_enabled: false, ai_boot_summary_min_interval_seconds: 600, ai_boot_summary_max_refreshes_per_hour: 20 } });
  const group = `Numeric settings ${Date.now()}`;
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'update_group_settings', group, settings: { max_agents: 3, env_vars: { max_agents: 'literal value' }, board_sync_github: {} } });
  await page.goto('/'); await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click();
  const maximum = page.getByRole('spinbutton', { name: 'Maximum agents', exact: true }); await expect(maximum).toHaveValue('3');
  const writes: Row[] = []; let reject = true;
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (/^(update_|engineer_update_)/.test(String(data.cmd))) writes.push(data);
    if (reject && data.cmd === 'update_group_settings') await route.fulfill({ json: { ok: false, error: 'Injected group refusal' } });
    else await route.continue();
  });
  const save = page.getByRole('button', { name: 'Save changes', exact: true });
  for (const [name, invalid, valid] of [
    ['Maximum agents', ['', '-1', '101', '2.5'], '0'],
    ['Terminal scrollback', ['', '99', '100001', '100.5'], '100'],
    ['Event retention', ['0', '49', '10001'], '50'],
    ['Pipeline depth', ['-1', '9007199254740993'], '0'],
    ['Boot summary minimum interval', ['', '-1', '1.5'], '0'],
    ['Boot summary hourly limit', ['', '-1'], '0'],
  ] as const) {
    const input = page.getByRole('spinbutton', { name, exact: true });
    for (const value of invalid) {
      await input.fill(value); await save.click();
      await expect(page.getByRole('alert')).toHaveText('Correct the highlighted setting before saving.');
      await expect(input).toBeFocused(); expect(writes).toHaveLength(0);
    }
    await input.fill(valid);
  }
  const groupSection = page.getByText(`${group} execution, worktrees, notifications and sync`, { exact: true }); await groupSection.click();
  await page.getByRole('textbox', { name: 'Env vars: max_agents', exact: true }).fill('009');
  const project = page.getByRole('spinbutton', { name: 'Board sync github: GitHub project number', exact: true });
  await project.fill('1.5'); await groupSection.click(); await save.click(); await expect(project).toBeFocused(); expect(writes).toHaveLength(0);
  await project.fill('8');
  await save.click(); await expect(page.getByRole('alert')).toContainText('Injected group refusal');
  expect(writes.map((item) => item.cmd)).toEqual(['update_global_settings', 'update_group_settings']);
  const global = await command(request, { cmd: 'get_global_settings' }); expect(global.settings).toMatchObject({ xterm_scrollback: 100, max_event_log: 50, max_pipeline_depth: 0 });
  // A later operator change to a completed scope must survive retry.
  await command(request, { cmd: 'update_global_settings', settings: { xterm_scrollback: 7000 } });
  await expect(maximum).toHaveValue('0'); reject = false; await save.click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible();
  expect(writes.map((item) => item.cmd)).toEqual(['update_global_settings', 'update_group_settings', 'update_group_settings', 'update_ai_settings']);
  const saved = await command(request, { cmd: 'get_group_settings', group });
  expect(saved.settings).toMatchObject({ max_agents: 0, env_vars: { max_agents: '009' }, board_sync_github: { github_project_number: 8 } });
  expect((await command(request, { cmd: 'get_global_settings' })).settings).toMatchObject({ xterm_scrollback: 7000, ai_boot_summary_min_interval_seconds: 0, ai_boot_summary_max_refreshes_per_hour: 0 });
  await page.reload(); await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await expect(page.getByRole('spinbutton', { name: 'Terminal scrollback', exact: true })).toHaveValue('7000'); await expect(maximum).toHaveValue('0');
  await page.screenshot({ path: test.info().outputPath('settings-validation.png'), fullPage: true });
});
