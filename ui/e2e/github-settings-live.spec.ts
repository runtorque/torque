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

test('GitHub check deadline retains the reviewed draft and choices and rejects a late repository suggestion', async ({ page, request }) => {
  test.setTimeout(75_000);
  test.skip(process.env.TORQUE_GITHUB_SETTINGS_FIXTURE !== '1', 'Requires isolated daemon with deterministic read-only gh fixture.');
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `GitHub recovery ${Date.now()}`;
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'settings' } });
  // Controlled transport adapter deliberately ignores abort, so observation
  // must settle independently and the eventual real daemon result stays stale.
  await page.addInitScript(() => {
    const original = window.fetch.bind(window);
    window.fetch = (input, init) => {
      if (typeof init?.body === 'string') {
        const data = JSON.parse(init.body) as { cmd?: string };
        if (data.cmd === 'board_sync_preflight') { const next = { ...init }; delete next.signal; return original(input, next); }
      }
      return original(input, init);
    };
  });
  let hold = false; let release: (() => void) | undefined; let released: Promise<void> | undefined; const checks: Row[] = [];
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd !== 'board_sync_preflight') { await route.continue(); return; }
    checks.push(data); const response = await route.fetch();
    if (hold) {
      hold = false; let completed: (() => void) | undefined;
      released = new Promise<void>((resolve) => { completed = resolve; });
      await new Promise<void>((resolve) => { release = resolve; });
      try { await route.fulfill({ response }); } finally { completed!(); }
    } else await route.fulfill({ response });
  });
  try {
    await page.goto('/'); await page.getByText(`${group} execution, worktrees, notifications and sync`, { exact: true }).click();
    await page.getByRole('combobox', { name: 'Board sync provider', exact: true }).selectOption('github');
    await page.getByRole('textbox', { name: 'Board sync github: Github project owner', exact: true }).fill('fixture-team');
    const repo = page.getByRole('textbox', { name: 'Board sync github: Github repo', exact: true }); await repo.fill('reviewed/repository');
    const tools = page.getByRole('region', { name: 'GitHub sync connection' });
    await tools.getByRole('button', { name: 'Load GitHub projects' }).click();
    await tools.getByRole('combobox').selectOption({ label: 'fixture-team · #7 — Fixture delivery' }); await expect(tools.getByRole('status')).toContainText('GitHub connection OK');
    hold = true; await tools.getByRole('button', { name: 'Use current repository' }).click(); await expect.poll(() => Boolean(release)).toBe(true);
    await repo.focus(); await repo.evaluate((node: HTMLInputElement) => { node.setSelectionRange(2, 6); node.dataset.checkAnchor = 'retained'; });
    await expect(tools.getByRole('alert')).toContainText('GitHub check timed out', { timeout: 35_000 });
    await expect(repo).toHaveValue('reviewed/repository'); await expect(repo).toBeFocused(); await expect(repo).toHaveAttribute('data-check-anchor', 'retained'); expect(await repo.evaluate((node: HTMLInputElement) => [node.selectionStart, node.selectionEnd])).toEqual([2, 6]);
    await expect(tools.getByRole('combobox')).toHaveValue(JSON.stringify(['fixture-team', 7, 'PVT_fixture_7'])); await expect(tools.getByRole('option', { name: /Fixture delivery/ })).toHaveCount(1);
    await page.setViewportSize({ width: 760, height: 650 }); await tools.scrollIntoViewIfNeeded(); await page.screenshot({ animations: 'disabled', path: test.info().outputPath('github-check-timeout.png') });
    await tools.getByRole('button', { name: 'Test GitHub connection' }).click(); await expect(tools.getByRole('status')).toContainText('GitHub connection OK');
    release!(); await released; await expect(repo).toHaveValue('reviewed/repository'); await expect(tools.getByRole('alert')).toHaveCount(0); expect(checks).toHaveLength(3);
    const unsaved = (await command(request, { cmd: 'get_group_settings', group })).settings as Row; expect(unsaved.board_sync_provider).toBe('none');
    await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible();
    expect((await command(request, { cmd: 'get_group_settings', group })).settings).toMatchObject({ board_sync_provider: 'github', board_sync_enabled: false, board_sync_github: { github_repo: 'reviewed/repository', github_project_number: 7 } });
  } finally { release?.(); }
});

test('All GitHub settings persist typed edits, exact maps, both switches and cleared defaults', async ({ page, request }) => {
  test.setTimeout(90_000);
  test.skip(process.env.TORQUE_GITHUB_SETTINGS_FIXTURE !== '1', 'Requires isolated daemon with deterministic read-only gh fixture.');
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `GitHub fields ${Date.now()}`;
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'settings' } });
  const open = async () => { await page.getByText(`${group} execution, worktrees, notifications and sync`, { exact: true }).click(); };
  const field = (label: string) => page.getByLabel(`Board sync github: ${label}`, { exact: true });
  const save = async () => { await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible(); };
  const textFields = [['Github repo', 'fixture/edited'], ['Github project owner', 'fixture-team'], ['Github project id', 'PVT_explicit'], ['Github project status field', 'Workflow']] as const;
  const booleans = ['Github close issues via pr', 'Github create missing labels'];
  const maps = [['GitHub lane status map', 'In Progress', '  exact status  '], ['GitHub assignee map', 'qa-worker', 'qa-login']] as const;
  await page.goto('/'); await open();
  const provider = page.getByRole('combobox', { name: 'Board sync provider', exact: true });
  const enabled = page.getByRole('combobox', { name: 'Board sync enabled', exact: true });
  await provider.selectOption('github');
  for (const [label, value] of textFields) await field(label).fill(`  ${value}  `);
  await field('GitHub project number').fill('12');
  for (const label of booleans) await field(label).selectOption('false');
  for (const [label, key, value] of maps) {
    const map = page.getByRole('group', { name: `Board sync github: ${label}`, exact: true });
    await map.getByLabel(`New board sync github: ${label.toLowerCase()} key`, { exact: true }).fill(key);
    await map.getByRole('button', { name: 'Add entry', exact: true }).click();
    await field(`${label}: ${key}`).fill(value);
  }
  // This disposable group has no tasks; enabling cannot enqueue a remote write.
  await enabled.selectOption('true'); await save(); await page.reload(); await open();
  await expect(provider).toHaveValue('github'); await expect(enabled).toHaveValue('true');
  for (const [label, value] of textFields) await expect(field(label)).toHaveValue(value);
  await expect(field('GitHub project number')).toHaveValue('12');
  for (const label of booleans) await expect(field(label)).toHaveValue('false');
  for (const [label, key, value] of maps) await expect(field(`${label}: ${key}`)).toHaveValue(value);
  expect((await command(request, { cmd: 'get_group_settings', group })).settings).toMatchObject({ board_sync_provider: 'github', board_sync_enabled: true, board_sync_github: {
    github_repo: 'fixture/edited', github_project_owner: 'fixture-team', github_project_number: 12, github_project_id: 'PVT_explicit', github_project_status_field: 'Workflow', github_close_issues_via_pr: false, github_create_missing_labels: false,
    github_lane_status_map: { 'In Progress': '  exact status  ' }, github_assignee_map: { 'qa-worker': 'qa-login' },
  } });
  for (const [label] of textFields) await field(label).fill('');
  await field('GitHub project number').fill('0'); for (const label of booleans) await field(label).selectOption('true');
  for (const [label, key] of maps) await page.getByRole('button', { name: `Remove Board sync github: ${label}: ${key}`, exact: true }).click();
  await enabled.selectOption('false'); await provider.selectOption('none'); await save(); await page.reload(); await open();
  await expect(provider).toHaveValue('none'); await expect(enabled).toHaveValue('false');
  for (const [label] of textFields) await expect(field(label)).toHaveValue(label === 'Github project status field' ? 'Status' : '');
  await expect(field('GitHub project number')).toHaveValue('0'); for (const label of booleans) await expect(field(label)).toHaveValue('true');
  for (const [label, key] of maps) await expect(field(`${label}: ${key}`)).toHaveCount(0);
  expect((await command(request, { cmd: 'get_group_settings', group })).settings).toMatchObject({ board_sync_provider: 'none', board_sync_enabled: false, board_sync_github: { github_project_number: 0, github_project_status_field: 'Status', github_lane_status_map: {}, github_assignee_map: {}, github_close_issues_via_pr: true, github_create_missing_labels: true } });
  await field('Github project status field').scrollIntoViewIfNeeded(); await page.screenshot({ animations: 'disabled', path: test.info().outputPath('github-fields-cleared.png') });
});
