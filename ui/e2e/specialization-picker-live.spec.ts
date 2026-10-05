import { expect, test, type APIRequestContext, type Locator, type WebSocketRoute } from '@playwright/test';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) { const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row }; expect(result.ok, result.error).toBe(true); return result.data; }
async function add(picker: Locator, name: string) { await picker.getByRole('combobox', { name: 'Available specializations' }).selectOption(name); await picker.getByRole('button', { name: 'Add specialization', exact: true }).click(); }
async function selected(picker: Locator) { return picker.getByRole('list', { name: 'Selected specializations' }).getByRole('listitem').evaluateAll((rows) => rows.map((row) => (row as HTMLElement).dataset.specialization)); }
test('Engineer creation, per-agent settings and group defaults share ordered project specialization controls', async ({ page, request }) => {
  test.setTimeout(120_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime; expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const root = mkdtempSync(join(tmpdir(), 'torque-specialization-picker-')); const group = `Specialization picker ${Date.now()}`; let agent = ''; let holdList = true; const releases: (() => void)[] = [];
  try {
    await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: root, git_worktree: false, default_engineer_specializations: ['qa-alpha'] } });
    for (const name of ['qa-alpha', 'qa-beta', 'qa-gamma']) await command(request, { cmd: 'save_specialization', group, scope: 'project', name, data: { name, preamble: 'Isolated UI acceptance specialization.', priorities: [] } });
    await command(request, { cmd: 'ui_select_group', group });
    let socket: WebSocketRoute | undefined; let connections = 0; let refuse = true; const writes: Row[] = [];
    await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections++; });
    await page.route('**/api/cmd', async (route) => {
      const data = route.request().postDataJSON() as Row;
      if (data.cmd === 'list_specializations' && holdList) await new Promise<void>((resolve) => { releases.push(resolve); });
      if (data.cmd === 'add_engineer') { const result = await route.fetch(); const body = await result.json() as { ok: boolean; data: Row }; if (body.ok) agent = String(body.data.id); await route.fulfill({ response: result }); return; }
      if (data.cmd === 'set_engineer_specializations') { writes.push(data); if (refuse) { await route.fulfill({ json: { ok: false, error: 'Injected specialization refusal' } }); return; } }
      await route.continue().catch(() => {});
    });
    await page.goto('/'); await page.getByRole('button', { name: /⌁ Agents/ }).click(); await page.getByRole('button', { name: 'Create agent or terminal' }).click(); await page.getByRole('menuitem', { name: 'New Engineer…' }).click();
    const create = page.getByRole('dialog', { name: 'New engineer' }); await expect.poll(() => selected(create)).toEqual(['qa-alpha']);
    await create.getByRole('button', { name: 'Refresh specializations' }).click(); await expect(create.getByRole('alert')).toContainText('refresh timed out', { timeout: 20_000 }); await expect.poll(() => selected(create)).toEqual(['qa-alpha']); holdList = false; await create.getByRole('button', { name: 'Refresh specializations' }).click(); releases.forEach((release) => release()); await expect(create.getByRole('option', { name: 'qa-beta', exact: true })).toHaveCount(1); await add(create, 'qa-beta');
    await create.getByRole('button', { name: 'Move qa-beta up' }).focus(); await page.keyboard.press('Enter'); await expect.poll(() => selected(create)).toEqual(['qa-beta', 'qa-alpha']);
    await create.getByLabel('Name', { exact: true }).fill('Specialized QA engineer'); await create.getByLabel('Provider', { exact: true }).fill('generic'); await create.getByLabel('Boot command', { exact: true }).fill('/bin/cat');
    expect((await create.getByLabel('Heartbeat interval', { exact: true }).boundingBox())!.height).toBeLessThan(80);
    await create.getByRole('list', { name: 'Selected specializations' }).scrollIntoViewIfNeeded(); await page.screenshot({ animations: 'disabled', path: test.info().outputPath('specialization-create.png') });
    await create.getByRole('button', { name: 'Create engineer', exact: true }).click(); await expect(create).toHaveCount(0); expect(agent).not.toBe('');
    const persisted = async () => ((await command(request, { cmd: 'get_agent_settings', agent_id: agent })).resolved as Row).engineer_specializations;
    expect(await persisted()).toMatchObject({ value: ['qa-beta', 'qa-alpha'] });
    await page.getByRole('button', { name: 'Settings', exact: true }).click(); const settings = page.getByRole('dialog', { name: 'Agent settings', exact: true });
    await settings.getByRole('button', { name: 'Refresh specializations' }).click(); await expect(settings.getByRole('option', { name: 'qa-gamma', exact: true })).toHaveCount(1); await add(settings, 'qa-gamma'); await settings.getByRole('button', { name: 'Remove qa-alpha' }).click(); await settings.getByRole('button', { name: 'Move qa-gamma up' }).click();
    const first = settings.getByRole('listitem').first(); await expect(first).toBeFocused(); const before = connections; holdList = true; await socket!.close({ code: 1012, reason: 'Specialization draft refresh' }); await expect.poll(() => connections).toBeGreaterThan(before);
    await expect(settings.getByRole('alert')).toContainText('refresh timed out', { timeout: 20_000 }); await expect(first).toBeFocused(); await expect.poll(() => selected(settings)).toEqual(['qa-gamma', 'qa-beta']); holdList = false; await settings.getByRole('button', { name: 'Refresh specializations' }).click(); releases.forEach((release) => release()); await first.focus();
    await expect(settings.getByText('Loading project specializations…')).toHaveCount(0); await expect.poll(() => selected(settings)).toEqual(['qa-gamma', 'qa-beta']); await expect(first).toBeFocused();
    await settings.getByRole('button', { name: 'Save settings', exact: true }).click(); await expect(settings.getByRole('alert')).toContainText('Injected specialization refusal'); expect(await persisted()).toMatchObject({ value: ['qa-beta', 'qa-alpha'] });
    await expect.poll(() => selected(settings)).toEqual(['qa-gamma', 'qa-beta']); await settings.getByRole('list', { name: 'Selected specializations' }).scrollIntoViewIfNeeded(); await page.screenshot({ animations: 'disabled', path: test.info().outputPath('specialization-agent.png') });
    refuse = false; await settings.getByRole('button', { name: 'Save settings', exact: true }).click(); await expect(settings).toHaveCount(0); expect(await persisted()).toMatchObject({ value: ['qa-gamma', 'qa-beta'] }); expect(writes).toHaveLength(2);
    await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click(); await page.getByText(`${group} execution, worktrees, notifications and sync`, { exact: true }).click();
    const defaults = page.getByRole('group', { name: 'Default engineer specializations', exact: true }); holdList = true; await defaults.getByRole('button', { name: 'Refresh specializations' }).click(); await expect(defaults.getByRole('alert')).toContainText('refresh timed out', { timeout: 20_000 }); await expect.poll(() => selected(defaults)).toEqual(['qa-alpha']); holdList = false; await defaults.getByRole('button', { name: 'Refresh specializations' }).click(); releases.forEach((release) => release()); await expect(defaults.getByRole('option', { name: 'qa-gamma', exact: true })).toHaveCount(1); await add(defaults, 'qa-gamma'); await defaults.getByRole('button', { name: 'Move qa-gamma up' }).click();
    await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible(); expect((await command(request, { cmd: 'get_group_settings', group })).settings).toMatchObject({ default_engineer_specializations: ['qa-gamma', 'qa-alpha'] });
    await page.reload(); await page.getByText(`${group} execution, worktrees, notifications and sync`, { exact: true }).click(); await expect.poll(() => selected(defaults)).toEqual(['qa-gamma', 'qa-alpha']);
    await defaults.scrollIntoViewIfNeeded(); await page.setViewportSize({ width: 760, height: 720 }); await page.screenshot({ animations: 'disabled', path: test.info().outputPath('specialization-defaults.png') });
  } finally { releases.forEach((release) => release()); if (agent) await command(request, { cmd: 'remove_agent', id: agent }); rmSync(root, { recursive: true, force: true }); }
});
