import { mkdtemp, mkdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
test('creation owns role choices across deadlines, unrelated replies, deletion and group changes', async ({ page, request }) => {
  test.setTimeout(65_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime; expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const root = await mkdtemp(join(tmpdir(), 'torque-creation-roles-')); const groups = [`Creation alpha ${Date.now()}`, `Creation beta ${Date.now()}`];
  let release = () => {}; let hold = true; let wrongGroup = false; let connections = 0; let socket: WebSocketRoute | undefined; const reads: Row[] = [];
  try {
    for (const [index, group] of groups.entries()) {
      const directory = join(root, String(index)); await mkdir(join(directory, '.torque', 'roles'), { recursive: true });
      await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: directory, git_worktree: false, agent_provider: 'generic', agent_boot_command: '/bin/cat' } });
      await command(request, { cmd: 'save_role', group, scope: 'project', name: index ? 'beta-local' : 'alpha-local', data: { provider: 'generic', command: '/bin/cat', model: index ? 'beta-model' : 'alpha-model', worktree: false } });
    }
    const foreign = await command(request, { cmd: 'list_roles', group: groups[1] });
    await command(request, { cmd: 'ui_select_group', group: groups[0] });
    await page.routeWebSocket(/\/ws\?/, (route) => { route.connectToServer(); socket = route; connections++; });
    await page.route('**/api/cmd', async (route) => {
      const data = route.request().postDataJSON() as Row;
      if (data.cmd === 'list_roles') {
        reads.push(data);
        if (hold) { hold = false; await new Promise<void>((resolve) => { release = resolve; }); await route.fulfill({ json: { ok: true, data: foreign } }); return; }
        if (wrongGroup) { await route.fulfill({ json: { ok: true, data: foreign } }); return; }
      }
      await route.continue();
    });
    const reconnect = async () => { const before = connections; await socket!.close({ code: 1012, reason: 'Creation role recovery' }); await expect.poll(() => connections).toBeGreaterThan(before); };
    const open = async () => { await page.getByRole('button', { name: 'Create agent or terminal' }).click(); await page.getByRole('menuitem', { name: 'New Worker…' }).click(); return page.getByRole('dialog', { name: 'New worker' }); };
    await page.goto('/'); await page.getByRole('button', { name: /⌁ Agents/ }).click(); let dialog = await open();
    const discovery = dialog.getByRole('region', { name: 'Creation role discovery' }); const picker = dialog.getByRole('combobox', { name: 'Role / template' }); const model = dialog.getByRole('textbox', { name: 'Model', exact: true });
    await dialog.getByRole('textbox', { name: 'Name', exact: true }).fill('Retained role draft'); await expect(model).toBeEnabled(); await model.fill('explicit-model'); await model.evaluate((node: HTMLInputElement) => { node.dataset.owner = 'retained'; node.setSelectionRange(1, 6); });
    await expect(discovery.getByRole('alert')).toContainText('timed out', { timeout: 20_000 }); await expect(model).toBeFocused(); expect(await model.evaluate((node: HTMLInputElement) => [node.selectionStart, node.selectionEnd])).toEqual([1, 6]);
    await discovery.getByRole('button', { name: 'Retry creation roles' }).click(); await expect(picker.locator('option[value="alpha-local"]')).toHaveCount(1); release(); await picker.selectOption('alpha-local'); await expect(dialog.getByRole('button', { name: 'Create worker' })).toBeEnabled();
    socket!.send(JSON.stringify(foreign)); await expect(picker.locator('option[value="beta-local"]')).toHaveCount(0); await expect(picker).toHaveValue('alpha-local'); await expect(model).toHaveValue('explicit-model');
    wrongGroup = true; await reconnect(); await expect(discovery.getByRole('alert')).toContainText('did not match'); await expect(picker).toHaveValue('alpha-local'); await expect(dialog.getByRole('button', { name: 'Create worker' })).toBeDisabled();
    wrongGroup = false; await discovery.getByRole('button', { name: 'Retry creation roles' }).click(); await expect(dialog.getByRole('button', { name: 'Create worker' })).toBeEnabled();
    await command(request, { cmd: 'delete_role', group: groups[0], name: 'alpha-local', scope: 'project' }); await reconnect(); await expect(discovery.getByRole('alert')).toContainText('no longer available'); await expect(picker).toHaveValue('alpha-local'); await expect(model).toHaveAttribute('data-owner', 'retained'); await expect(dialog.getByRole('button', { name: 'Create worker' })).toBeDisabled();
    await page.setViewportSize({ width: 760, height: 650 }); await discovery.scrollIntoViewIfNeeded(); await page.screenshot({ path: test.info().outputPath('unavailable-creation-role.png') });
    await picker.selectOption(''); await expect(dialog.getByRole('button', { name: 'Create worker' })).toBeEnabled();
    await dialog.getByRole('combobox', { name: 'Agent kind' }).selectOption('terminal'); dialog = page.getByRole('dialog', { name: 'New terminal' }); const count = reads.length; await reconnect(); expect(reads).toHaveLength(count);
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click(); await command(request, { cmd: 'ui_select_group', group: groups[1] }); await page.reload(); await page.getByRole('button', { name: /⌁ Agents/ }).click(); dialog = await open();
    await expect(dialog.getByRole('combobox', { name: 'Role / template' }).locator('option[value="beta-local"]')).toHaveCount(1); await expect(dialog.getByRole('combobox', { name: 'Role / template' }).locator('option[value="alpha-local"]')).toHaveCount(0);
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click(); const closed = reads.length; await reconnect(); expect(reads).toHaveLength(closed);
  } finally { release(); await rm(root, { recursive: true, force: true }); }
});
