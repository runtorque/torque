import { mkdtempSync, mkdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
async function assertIsolated(request: APIRequestContext) {
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: { port: number; profile: string } } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
}
let created: string[] = [];
test.afterEach(async ({ request }) => { for (const id of [...new Set(created)].reverse()) await command(request, { cmd: 'remove_agent', id }); created = []; });
test('Worker creation resolves templates, retains edits and retries cached creation without duplicating the worker', async ({ page, request }) => {
  test.setTimeout(60_000); await assertIsolated(request);
  const project = mkdtempSync(join(tmpdir(), 'torque-agent-create-')); mkdirSync(join(project, '.torque', 'roles'), { recursive: true });
  const group = `Create worker ${Date.now()}`; const name = `${group} target`;
  try {
    await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: project, agent_directory: project, agent_shell: 'bash', agent_provider: 'generic', agent_boot_command: '/bin/cat', git_worktree: false } }); await command(request, { cmd: 'ui_select_group', group });
    const role = { provider: 'generic', command: '/bin/cat', model: 'role-model', reasoning_effort: 'high', env_vars: { MODE: 'qa' }, worktree: false, worktree_merge_squash: false };
    await command(request, { cmd: 'save_role', group, name: 'creation-role', scope: 'project', data: role });
    let socket: WebSocketRoute | undefined; let connections = 0; let refuseRead = false; let attempt = 0; let release: (() => void) | undefined; const writes: Row[] = [];
    await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections++; });
    await page.route('**/api/cmd', async (route) => {
      const data = route.request().postDataJSON() as Row;
      if (refuseRead && data.cmd === 'render_template') { await route.fulfill({ json: { ok: false, error: 'Injected template read refusal' } }); return; }
      if (data.cmd === 'add_worker') {
        writes.push(data); attempt++;
        if (attempt === 1) { await route.fulfill({ json: { ok: false, error: 'Injected creation refusal' } }); return; }
        const result = await route.fetch(); const body = await result.json() as { data: Row }; created.push(String(body.data.id));
        if (attempt === 2) { await new Promise<void>((resolve) => { release = resolve; }); await route.fulfill({ json: { ok: false, error: 'Simulated lost creation acknowledgement' } }); return; }
        await route.fulfill({ response: result }); return;
      }
      await route.continue();
    });
    await page.goto('/'); await page.getByRole('button', { name: /⌁ Agents/ }).click(); await page.getByRole('button', { name: 'Create agent or terminal' }).click(); await page.getByRole('menuitem', { name: 'New Worker…' }).click();
    const dialog = page.getByRole('dialog', { name: 'New worker' }); const model = dialog.getByRole('textbox', { name: 'Model', exact: true }); const create = dialog.getByRole('button', { name: 'Create worker', exact: true });
    await dialog.getByLabel('Name', { exact: true }).fill(name); await dialog.getByLabel('Role / template').selectOption('creation-role'); await expect(model).toHaveValue('role-model'); await expect(dialog.getByLabel('Provider', { exact: true })).toHaveValue('generic'); await expect(dialog.getByLabel('Boot command')).toHaveValue('/bin/cat'); await expect(dialog.getByLabel('Environment variables')).toHaveValue('MODE=qa'); await expect(dialog.getByLabel('Directory', { exact: true })).toHaveValue(project);
    await model.fill('local-model'); await model.focus(); await model.evaluate((node: HTMLInputElement) => { node.setSelectionRange(2, 7); node.dataset.creationAnchor = 'original'; });
    const body = dialog.locator('[data-dialog-body-layout]'); const scroll = await body.evaluate((node) => node.scrollTop);
    await command(request, { cmd: 'save_role', group, name: 'creation-role', scope: 'project', data: { ...role, model: 'changed-role-model', env_vars: { MODE: 'updated' } } });
    const before = connections; await socket!.close({ code: 1012, reason: 'Creation draft reconnect' }); await expect.poll(() => connections).toBeGreaterThan(before); await expect(dialog.getByLabel('Environment variables')).toHaveValue('MODE=updated'); await expect(model).toHaveValue('local-model'); await expect(dialog.getByLabel('Role / template')).toHaveValue('creation-role'); await expect(model).toBeFocused(); await expect(model).toHaveAttribute('data-creation-anchor', 'original'); expect(await model.evaluate((node: HTMLInputElement) => [node.selectionStart, node.selectionEnd])).toEqual([2, 7]); expect(await body.evaluate((node) => node.scrollTop)).toBe(scroll);
    refuseRead = true; await dialog.getByRole('button', { name: 'Refresh launch settings' }).click(); await expect(dialog.getByRole('alert')).toContainText('Injected template read refusal'); await expect(create).toBeDisabled(); refuseRead = false; await dialog.getByRole('button', { name: 'Retry launch settings' }).click(); await expect(create).toBeEnabled();
    await create.click(); await expect(dialog.getByRole('alert')).toHaveText('Injected creation refusal'); await expect(model).toHaveValue('local-model'); await expect(dialog.getByRole('alert')).toBeFocused();
    await create.click(); await expect.poll(() => Boolean(release)).toBe(true); await expect(dialog.getByLabel('Name', { exact: true })).toBeDisabled(); await dialog.getByRole('button', { name: 'Close dialog' }).click(); await page.mouse.click(2, 2); await page.keyboard.press('Escape'); await expect(dialog).toBeVisible(); expect(writes).toHaveLength(2);
    release!(); await expect(dialog.getByRole('alert')).toHaveText('Simulated lost creation acknowledgement'); await page.screenshot({ animations: 'disabled', path: test.info().outputPath('creation-retry.png') });
    await create.click(); await expect(dialog).toHaveCount(0); expect(writes).toHaveLength(3); expect(writes[0]).toEqual(writes[1]); expect(writes[1]).toEqual(writes[2]); expect(new Set(created).size).toBe(1);
    const id = created[0]!; await expect(page.locator(`[role="treeitem"][data-agent-id="${id}"]`)).toHaveAttribute('aria-selected', 'true');
    await page.reload(); await page.getByRole('button', { name: /⌁ Agents/ }).click(); await expect(page.locator('[role="treeitem"]').filter({ hasText: name })).toHaveCount(1);
  } finally { rmSync(project, { recursive: true, force: true }); }
});

test('Architect, Engineer and terminal creation acknowledge actual targets and persist after reload', async ({ page, request }) => {
  test.setTimeout(60_000); await assertIsolated(request); const group = `Create kinds ${Date.now()}`;
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: '/private/tmp', git_worktree: false } }); await command(request, { cmd: 'ui_select_group', group });
  const writes: Row[] = []; await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (['add_architect', 'add_engineer', 'add_terminal'].includes(String(data.cmd))) { writes.push(data); const result = await route.fetch(); const body = await result.json() as { ok: boolean; data: Row }; if (body.ok) created.push(String(body.data.id)); await route.fulfill({ response: result }); } else await route.continue();
  });
  await page.goto('/'); await page.getByRole('button', { name: /⌁ Agents/ }).click();
  for (const kind of ['architect', 'engineer', 'terminal']) {
    await page.getByRole('button', { name: 'Create agent or terminal' }).click(); await page.getByRole('menuitem', { name: `New ${kind[0]!.toUpperCase()}${kind.slice(1)}…` }).click();
    const dialog = page.getByRole('dialog', { name: `New ${kind}` }); await dialog.getByLabel('Name', { exact: true }).fill(`${group} ${kind}`); if (kind !== 'terminal') await dialog.getByLabel('Provider', { exact: true }).fill('generic'); await dialog.getByLabel('Boot command').fill('/bin/cat');
    if (kind === 'terminal') { await dialog.getByLabel('Parent agent').selectOption(created[0]!); await page.setViewportSize({ width: 720, height: 760 }); await page.screenshot({ animations: 'disabled', path: test.info().outputPath('creation-terminal.png') }); }
    await dialog.getByRole('button', { name: `Create ${kind}` }).click(); await expect(dialog).toHaveCount(0); const id = created.at(-1)!; await expect(page.locator(`[role="treeitem"][data-agent-id="${id}"]`)).toHaveAttribute('aria-selected', 'true');
  }
  expect(writes.map((data) => data.cmd)).toEqual(['add_architect', 'add_engineer', 'add_terminal']); await page.reload(); await page.getByRole('button', { name: /⌁ Agents/ }).click(); for (const id of created) await expect(page.locator(`[role="treeitem"][data-agent-id="${id}"]`)).toBeVisible();
});

test('Agent Class creation discovers the selected project and revalidates retained choices after reconnect', async ({ page, request }) => {
  test.setTimeout(60_000); await assertIsolated(request);
  const root = mkdtempSync(join(tmpdir(), 'torque-class-create-')); const groups = ['Alpha', 'Beta'].map((label) => `${label} class creation ${Date.now()}`);
  const projects = ['alpha', 'beta'].map((label) => join(root, label)); const classId = 'same-local-class';
  const definition = (label: string): Row => ({ agent_class_schema_version: 5, id: classId, version: '1', base_kind: 'engineer', display_name: `${label} Project Engineer`, acl: { mode: 'deny', rules: [] } });
  try {
    for (const [index, group] of groups.entries()) {
      const project = projects[index]!; mkdirSync(join(project, '.torque', 'agent_classes'), { recursive: true });
      await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: project, git_worktree: false } });
      const saved = await command(request, { cmd: 'agent_class_create', base_dir: project, agent_class: definition(index ? 'Beta' : 'Alpha') }); expect(saved.ok).toBe(true);
    }
    await command(request, { cmd: 'ui_select_group', group: groups[0] });
    let socket: WebSocketRoute | undefined; let connections = 0; let refuse = false; const discoveries: Row[] = []; const launches: Row[] = [];
    await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections++; });
    const reconnect = async () => { const before = connections; await socket!.close({ code: 1012, reason: 'Class selection revalidation' }); await expect.poll(() => connections).toBeGreaterThan(before); };
    await page.route('**/api/cmd', async (route) => {
      const data = route.request().postDataJSON() as Row;
      if (data.cmd === 'agent_class_list') { discoveries.push(data); if (refuse) { await route.fulfill({ json: { ok: false, error: 'Injected class discovery refusal' } }); return; } }
      if (data.cmd === 'create_agent_from_class') {
        launches.push(data); const result = await route.fetch(); const body = await result.json() as { ok: boolean; data: Row };
        if (body.ok && body.data.agent) created.push(String((body.data.agent as Row).id)); await route.fulfill({ response: result }); return;
      }
      await route.continue();
    });
    const open = async () => { await page.getByRole('button', { name: 'Create agent or terminal' }).click(); await page.getByRole('menuitem', { name: 'New Engineer…' }).click(); return page.getByRole('dialog', { name: 'New engineer' }); };
    await page.goto('/'); await page.getByRole('button', { name: /⌁ Agents/ }).click(); let dialog = await open();
    const picker = dialog.getByRole('combobox', { name: 'Agent Class', exact: true });
    await expect(picker.locator('option').filter({ hasText: 'Alpha Project Engineer' })).toHaveCount(1); await expect(picker.locator('option').filter({ hasText: 'Beta Project Engineer' })).toHaveCount(0); await picker.selectOption(classId);
    const name = dialog.getByLabel('Name', { exact: true }); await name.fill('Retained Alpha draft'); await name.evaluate((node: HTMLInputElement) => node.setSelectionRange(2, 8));
    refuse = true; await reconnect(); await expect(dialog.getByRole('alert')).toContainText('Injected class discovery refusal'); await expect(picker).toHaveValue(classId); await expect(name).toBeFocused(); expect(await name.evaluate((node: HTMLInputElement) => [node.selectionStart, node.selectionEnd])).toEqual([2, 8]); await expect(dialog.getByRole('button', { name: 'Create engineer' })).toBeDisabled();
    refuse = false; await dialog.getByRole('button', { name: 'Retry Agent Classes' }).click(); await expect(dialog.getByRole('button', { name: 'Create engineer' })).toBeEnabled();
    await command(request, { cmd: 'agent_class_archive', base_dir: projects[0], class_id: classId }); await reconnect(); await expect(dialog.getByRole('alert')).toContainText('Archived or disabled'); await expect(picker).toHaveValue(classId); await expect(dialog.getByRole('button', { name: 'Create engineer' })).toBeDisabled(); expect(launches).toHaveLength(0);
    await page.screenshot({ animations: 'disabled', path: test.info().outputPath('class-selection-unavailable.png') });
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click(); await command(request, { cmd: 'ui_select_group', group: groups[1] }); await page.reload(); await page.getByRole('button', { name: /⌁ Agents/ }).click(); dialog = await open();
    const betaPicker = dialog.getByRole('combobox', { name: 'Agent Class', exact: true }); await expect(betaPicker.locator('option').filter({ hasText: 'Beta Project Engineer' })).toHaveCount(1); await expect(betaPicker.locator('option').filter({ hasText: 'Alpha Project Engineer' })).toHaveCount(0); await betaPicker.selectOption(classId);
    await dialog.getByLabel('Name', { exact: true }).fill('Created Beta class engineer'); await dialog.getByLabel('Provider', { exact: true }).fill('generic'); await dialog.getByLabel('Boot command').fill('/bin/cat');
    await dialog.getByRole('button', { name: 'Create engineer' }).click(); await expect(dialog).toHaveCount(0); expect(launches).toHaveLength(1); expect(launches[0]).toMatchObject({ group: groups[1], class_id: classId });
    const id = created.at(-1)!; await expect(page.locator(`[role="treeitem"][data-agent-id="${id}"]`)).toHaveAttribute('aria-selected', 'true');
    const status = (await command(request, { cmd: 'agent_class_status', agent_id: id })).status as Row; expect(status).toMatchObject({ assigned_class_id: classId, effective_class_id: classId, primary_identity_label: 'Beta Project Engineer' });
    await page.reload(); await page.getByRole('button', { name: /⌁ Agents/ }).click(); await expect(page.locator(`[role="treeitem"][data-agent-id="${id}"]`)).toBeVisible(); expect(discoveries.map((data) => data.group)).toContain(groups[0]); expect(discoveries.map((data) => data.group)).toContain(groups[1]);
  } finally { for (const id of created.splice(0).reverse()) await command(request, { cmd: 'remove_agent', id }); rmSync(root, { recursive: true, force: true }); }
});
