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
