import { expect, test, type APIRequestContext } from '@playwright/test';
import { mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
type Row = Record<string, unknown>;
const quote = (value: string) => "'" + value.replaceAll("'", "'\\''") + "'";
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}
test('Saved global default command reaches actual launches, yields to group commands and clears to runtime fallback', async ({ page, request }) => {
  test.setTimeout(90_000); page.setDefaultTimeout(12_000);
  test.skip(!process.env.TORQUE_PTY_PYTHON, 'Requires a local probe interpreter and disposable daemon');
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.profile).not.toBe('default'); expect(runtime.port).not.toBe(18932);
  const original = (await command(request, { cmd: 'get_global_settings' })).settings as Row;
  const directory = await realpath(await mkdtemp(join(tmpdir(), 'torque-global-command-')));
  const probe = fileURLToPath(new URL('./fixtures/terminal_settings_probe.py', import.meta.url)); const ids: string[] = []; const evidence: Row[] = [];
  const boot = (phase: string) => `${quote(process.env.TORQUE_PTY_PYTHON!)} -u ${quote(probe)} ${quote(join(directory, `${phase}.jsonl`))} ${quote(`${phase} argument with spaces`)}`;
  const group = `Global command ${Date.now()}`;
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: directory, default_agent_template: '', agent_provider: 'generic', agent_boot_command: '', worker_provider: '', worker_boot_command: '', git_worktree: false } });
  await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'settings' } });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row; if (data.cmd !== 'add_worker') { await route.continue(); return; }
    const response = await route.fetch(); const body = await response.json() as { ok: boolean; data: Row }; expect(body.ok).toBe(true); ids.push(String(body.data.id)); await route.fulfill({ response });
  });
  const setting = async (label: string) => {
    await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click();
    await page.getByRole('searchbox', { name: 'Search settings' }).fill(label); await page.getByRole('button', { name: new RegExp(`^${label} — `) }).click(); return page.getByLabel(label, { exact: true });
  };
  const save = async () => { await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible(); };
  const create = async (expected: string) => {
    await page.getByRole('button', { name: /⌁ Agents/ }).click(); await page.getByRole('button', { name: 'Create agent or terminal' }).click(); await page.getByRole('menuitem', { name: 'New Worker…' }).click();
    const dialog = page.getByRole('dialog', { name: 'New worker' }); await expect(dialog.getByLabel('Boot command', { exact: true })).toHaveValue(expected); return dialog;
  };
  try {
    await page.goto('/'); await (await setting('Default command')).fill(`  ${boot('global')}  `); await save(); await page.reload(); await expect(await setting('Default command')).toHaveValue(boot('global'));
    for (const phase of ['global', 'group']) {
      if (phase === 'group') { await (await setting('Agent boot command')).fill(boot('group')); await save(); await page.reload(); await expect(await setting('Agent boot command')).toHaveValue(boot('group')); }
      const dialog = await create(phase === 'group' ? boot(phase) : ''); await dialog.getByLabel('Name', { exact: true }).fill(`Global command ${phase}`); await dialog.getByRole('button', { name: 'Create worker', exact: true }).click(); await expect(dialog).toHaveCount(0);
      let observed: Row = {}; await expect.poll(async () => { try { observed = JSON.parse((await readFile(join(directory, `${phase}.jsonl`), 'utf8')).split('\n')[0]!) as Row; return observed; } catch { return {}; } }).toMatchObject({ kind: 'launch', directory, args: [`${phase} argument with spaces`] });
      evidence.push({ phase, observed });
    }
    await (await setting('Agent boot command')).fill(''); await (await setting('Default command')).fill(''); await save(); await page.reload(); await expect(await setting('Default command')).toHaveValue('');
    expect((await command(request, { cmd: 'get_global_settings' })).settings).toMatchObject({ default_command: '' });
    const dialog = await create(''); await dialog.getByLabel('Name', { exact: true }).fill('Runtime fallback Worker'); await dialog.getByRole('button', { name: 'Create worker', exact: true }).click(); await expect(dialog).toHaveCount(0);
    const fallback = ((await command(request, { cmd: 'get_state' })).agents as Record<string, Row>)[ids.at(-1)!]!; expect(fallback.command).toBe(runtime.default_command); expect(fallback.session_id).toBeTruthy();
    const path = test.info().outputPath('global-command-launches.json'); await writeFile(path, JSON.stringify({ evidence, clearedFallback: { command: fallback.command, session: fallback.session_id } }, null, 2)); await test.info().attach('global-command-launches', { path, contentType: 'application/json' });
  } finally {
    for (const id of ids) { await command(request, { cmd: 'remove_agent', id }); await command(request, { cmd: 'purge_agent_now', id }); }
    await command(request, { cmd: 'update_global_settings', settings: { default_command: original.default_command } }); await rm(directory, { recursive: true, force: true });
  }
});
