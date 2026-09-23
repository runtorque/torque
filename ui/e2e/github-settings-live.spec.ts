import { expect, test, type APIRequestContext } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const response = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(response.ok, response.error).toBe(true); return response.data;
}
test('GitHub settings discover and resolve a project through the daemon without saving until requested', async ({ page, request }) => {
  test.skip(process.env.TORQUE_GITHUB_SETTINGS_FIXTURE !== '1', 'Requires isolated daemon with deterministic read-only gh fixture.');
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `GitHub configuration ${Date.now()}`;
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'settings' } });
  await page.goto('/'); const groupHeading = page.getByText(`${group} execution, worktrees, notifications and sync`, { exact: true }); await groupHeading.click();
  await page.getByRole('combobox', { name: 'Board sync provider', exact: true }).selectOption('github');
  const owner = page.getByRole('textbox', { name: 'Board sync github: Github project owner', exact: true }); await owner.fill('fixture-team');
  const repo = page.getByRole('textbox', { name: 'Board sync github: Github repo', exact: true }); await repo.fill('typed/repo');
  const tools = page.getByRole('region', { name: 'GitHub sync connection' });
  await tools.getByRole('button', { name: 'Load GitHub projects' }).click();
  await expect(tools.getByRole('option', { name: /Fixture delivery/ })).toHaveCount(1);
  await tools.getByRole('combobox').selectOption({ label: 'fixture-team · #7 — Fixture delivery' });
  await expect(tools.getByRole('status')).toContainText('GitHub connection OK');
  await expect(page.getByRole('spinbutton', { name: 'Board sync github: GitHub project number', exact: true })).toHaveValue('7');
  await expect(page.getByRole('textbox', { name: 'Board sync github: Github project id', exact: true })).toHaveValue('PVT_fixture_7');
  await expect(tools.getByText(/Project status options/)).toContainText('Todo');
  await expect(tools.getByRole('status')).toContainText('Filled empty lane mapping');
  const unsaved = (await command(request, { cmd: 'get_group_settings', group })).settings as Row;
  expect(unsaved.board_sync_provider).toBe('none'); expect(unsaved.board_sync_enabled).toBe(false);
  await tools.getByRole('button', { name: 'Use current repository' }).click(); await expect(repo).toHaveValue('fixture/current');
  let refuse = true;
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd === 'board_sync_preflight' && refuse) await route.fulfill({ json: { ok: true, data: { type: 'board_sync_preflight', provider: 'github', group, ok: false, phase: 'auth', error: 'Injected authentication failure' } } });
    else await route.continue();
  });
  await tools.getByRole('button', { name: 'Test GitHub connection' }).click(); await expect(tools.getByRole('alert')).toContainText('Injected authentication failure'); await expect(repo).toHaveValue('fixture/current');
  refuse = false; await tools.getByRole('button', { name: 'Test GitHub connection' }).click(); await expect(tools.getByRole('status')).toContainText('GitHub connection OK');
  await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible();
  const saved = (await command(request, { cmd: 'get_group_settings', group })).settings as Row;
  expect(saved).toMatchObject({ board_sync_provider: 'github', board_sync_enabled: false, board_sync_github: { github_repo: 'fixture/current', github_project_owner: 'fixture-team', github_project_number: 7, github_project_id: 'PVT_fixture_7' } });
  expect(Object.keys((saved.board_sync_github as Row).github_lane_status_map as Row).length).toBeGreaterThan(0);
  await page.reload(); await groupHeading.click(); await expect(repo).toHaveValue('fixture/current');
  await expect(tools.getByRole('combobox')).toHaveValue(JSON.stringify(['fixture-team', 7, 'PVT_fixture_7']));
  await tools.scrollIntoViewIfNeeded(); await page.screenshot({ animations: 'disabled', path: test.info().outputPath('github-settings.png') });
});
