import { expect, test, type APIRequestContext } from '@playwright/test';
import { mkdtemp, mkdir, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
type Row = Record<string, unknown>;
const quote = (value: string) => "'" + value.replaceAll("'", "'\\''") + "'";
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}
test('Saved default role launches its real command, explicit role overrides it and clearing restores shared launch defaults', async ({ page, request }) => {
  test.setTimeout(90_000); page.setDefaultTimeout(12_000);
  test.skip(!process.env.TORQUE_PTY_PYTHON, 'Requires explicit local Python and an isolated daemon');
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const directory = await realpath(await mkdtemp(join(tmpdir(), 'torque-default-role-launch-'))); await mkdir(join(directory, '.torque', 'roles'), { recursive: true });
  const probe = fileURLToPath(new URL('./fixtures/terminal_settings_probe.py', import.meta.url));
  const boot = (phase: string) => `${quote(process.env.TORQUE_PTY_PYTHON!)} -u ${quote(probe)} ${quote(join(directory, `${phase}.jsonl`))} ${phase}`;
  const group = `Default role launch ${Date.now()}`; const ids: string[] = []; const evidence: Row[] = []; const launches: Row[] = [];
  await command(request, { cmd: 'add_group', group });
  await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: directory, default_agent_template: '', git_worktree: false } });
  for (const phase of ['default', 'explicit']) await command(request, { cmd: 'save_role', group, scope: 'project', name: `qa-${phase}`, data: { display_name: `QA ${phase}`, provider: 'generic', command: boot(phase), model: `${phase}-model`, preamble: `QA ${phase} instructions`, worktree: false } });
  await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'settings' } });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd !== 'add_worker') { await route.continue(); return; }
    launches.push(data); const response = await route.fetch(); const body = await response.json() as { ok: boolean; data: Row }; expect(body.ok).toBe(true);
    if (typeof body.data.id === 'string') ids.push(body.data.id); await route.fulfill({ response });
  });
  const setting = async (label: string) => {
    await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click();
    await page.getByRole('searchbox', { name: 'Search settings' }).fill(label); await page.getByRole('button', { name: new RegExp(`^${label} — `) }).click();
    return label === 'Default agent template' ? page.getByRole('combobox', { name: label, exact: true }) : page.getByLabel(label, { exact: true });
  };
  const save = async () => { await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible(); };
  const launch = async (phase: string, role: string) => {
    await page.getByRole('button', { name: /⌁ Agents/ }).click(); await page.getByRole('button', { name: 'Create agent or terminal' }).click(); await page.getByRole('menuitem', { name: 'New Worker…' }).click();
    const dialog = page.getByRole('dialog', { name: 'New worker' });
    if (phase === 'explicit') await dialog.getByRole('combobox', { name: 'Role / template', exact: true }).selectOption(role);
    else await expect(dialog.getByRole('combobox', { name: 'Role / template', exact: true })).toHaveValue('');
    await expect(dialog.getByLabel('Boot command', { exact: true })).toHaveValue(boot(phase)); await expect(dialog.getByLabel('Model', { exact: true })).toHaveValue(`${phase}-model`);
    await dialog.getByLabel('Name', { exact: true }).fill(`${phase} role Worker`); await dialog.getByRole('button', { name: 'Create worker', exact: true }).click(); await expect(dialog).toHaveCount(0);
    let process: Row = {};
    await expect.poll(async () => { try { process = JSON.parse((await readFile(join(directory, `${phase}.jsonl`), 'utf8')).split('\n')[0]!) as Row; return process; } catch { return {}; } }).toMatchObject({ kind: 'launch', directory, args: [phase] });
    const cell = ((await command(request, { cmd: 'get_state' })).agents as Record<string, Row>)[ids.at(-1)!]!;
    expect(cell).toMatchObject({ kind: 'worker', template: role }); expect(launches.at(-1)).toMatchObject({ command: boot(phase), model: `${phase}-model` });
    evidence.push({ phase, role, process, cell, command: launches.at(-1) });
  };
  try {
    await page.goto('/'); await (await setting('Default agent template')).selectOption('qa-default'); await save(); await page.reload();
    await expect(await setting('Default agent template')).toHaveValue('qa-default');
    await launch('default', 'qa-default'); await launch('explicit', 'qa-explicit');
    await (await setting('Default agent template')).selectOption(''); await (await setting('Agent provider')).fill('generic');
    await (await setting('Agent boot command')).fill(boot('shared')); await (await setting('Agent model')).fill('shared-model');
    await save(); await page.reload(); await expect(await setting('Default agent template')).toHaveValue(''); await launch('shared', '');
    const path = test.info().outputPath('default-role-launch-evidence.json'); await writeFile(path, JSON.stringify(evidence, null, 2)); await test.info().attach('default-role-launch-evidence', { path, contentType: 'application/json' });
    await page.screenshot({ path: test.info().outputPath('default-role-launch.png'), animations: 'disabled' });
  } finally {
    for (const id of ids) { await command(request, { cmd: 'remove_agent', id }); await command(request, { cmd: 'purge_agent_now', id }); }
    await rm(directory, { recursive: true, force: true });
  }
});
