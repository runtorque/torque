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

test('New Worker previews saved worker defaults and launches worker, explicit and shared fallback commands', async ({ page, request }) => {
  test.setTimeout(90_000); page.setDefaultTimeout(12_000);
  test.skip(!process.env.TORQUE_PTY_PYTHON, 'Requires explicit local Python and an isolated daemon');
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const directory = await realpath(await mkdtemp(join(tmpdir(), 'torque-worker-defaults-')));
  const probe = fileURLToPath(new URL('./fixtures/terminal_settings_probe.py', import.meta.url));
  const boot = (phase: string) => `${quote(process.env.TORQUE_PTY_PYTHON!)} -u ${quote(probe)} ${quote(join(directory, `${phase}.jsonl`))} ${phase}`;
  const group = `Worker defaults ${Date.now()}`; const ids: string[] = []; const launches: Row[] = []; const evidence: Row[] = [];
  await command(request, { cmd: 'add_group', group });
  await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: directory, default_agent_template: '', git_worktree: false } });
  await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'settings' } });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd !== 'add_worker') { await route.continue(); return; }
    launches.push(data); const response = await route.fetch(); const body = await response.json() as { data: Row };
    if (typeof body.data.id === 'string') ids.push(body.data.id); await route.fulfill({ response });
  });
  const openSettings = async () => { await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click(); };
  const reveal = async (label: string) => { await page.getByRole('searchbox', { name: 'Search settings' }).fill(label); await page.getByRole('button', { name: new RegExp(`^${label} — `) }).click(); return page.getByLabel(label, { exact: true }); };
  const save = async () => { await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible(); };
  const launch = async (phase: string, expected: string, explicit = false) => {
    await page.getByRole('button', { name: /⌁ Agents/ }).click(); await page.getByRole('button', { name: 'Create agent or terminal' }).click(); await page.getByRole('menuitem', { name: 'New Worker…' }).click();
    const dialog = page.getByRole('dialog', { name: 'New worker' });
    await expect(dialog.getByLabel('Boot command', { exact: true })).toHaveValue(boot(expected));
    await expect(dialog.getByLabel('Model', { exact: true })).toHaveValue(`${expected}-model`);
    await expect(dialog.getByLabel('Reasoning effort', { exact: true })).toHaveValue(expected === 'worker' ? 'high' : 'low');
    await expect(dialog.getByRole('combobox', { name: 'Fast mode', exact: true })).toHaveValue(expected === 'worker' ? 'off' : 'on');
    if (explicit) {
      await dialog.getByLabel('Boot command', { exact: true }).fill(boot(phase));
      await dialog.getByLabel('Model', { exact: true }).fill('explicit-model');
      await dialog.getByLabel('Reasoning effort', { exact: true }).fill('medium');
      await dialog.getByRole('combobox', { name: 'Fast mode', exact: true }).selectOption('on');
      await dialog.getByRole('button', { name: 'Refresh launch settings' }).click();
      await expect(dialog.getByText('Resolving launch settings…')).toHaveCount(0);
      await expect(dialog.getByLabel('Boot command', { exact: true })).toHaveValue(boot(phase));
    }
    await dialog.getByLabel('Name', { exact: true }).fill(`${phase} worker`);
    await page.screenshot({ path: test.info().outputPath(`${phase}-worker-preview.png`), animations: 'disabled' });
    await dialog.getByRole('button', { name: 'Create worker', exact: true }).click(); await expect(dialog).toHaveCount(0);
    let process: Row = {};
    await expect.poll(async () => {
      try { process = JSON.parse((await readFile(join(directory, `${phase}.jsonl`), 'utf8')).split('\n')[0]!) as Row; return process; }
      catch { return {}; }
    }).toMatchObject({ kind: 'launch', directory, args: [phase] });
    const cell = ((await command(request, { cmd: 'get_state' })).agents as Record<string, Row>)[ids.at(-1)!]!;
    expect(cell).toMatchObject({ kind: 'worker', terminal_backend: 'pty' });
    expect(launches.at(-1)?.fast_mode).toBe(explicit || expected === 'shared' ? 'on' : 'off');
    expect(launches.at(-1)).toMatchObject({ command: boot(phase), model: `${phase}-model`, reasoning_effort: explicit ? 'medium' : expected === 'worker' ? 'high' : 'low' });
    evidence.push({ phase, process, launch: launches.at(-1), fast_mode: launches.at(-1)?.fast_mode });
  };
  try {
    await page.goto('/');
    const values = [['Agent provider', 'generic'], ['Agent boot command', boot('shared')], ['Agent model', 'shared-model'], ['Agent reasoning effort', 'low'], ['Worker provider', 'generic'], ['Worker boot command', boot('worker')], ['Worker model', 'worker-model'], ['Worker reasoning effort', 'high']] as const;
    for (const [label, value] of values) await (await reveal(label)).fill(value);
    await (await reveal('Agent fast mode')).selectOption('on'); await (await reveal('Worker fast mode')).selectOption('off');
    await save(); await page.reload();
    for (const [label, value] of values) await expect(await reveal(label)).toHaveValue(value);
    await launch('worker', 'worker'); await launch('explicit', 'worker', true);
    await openSettings();
    for (const label of ['Worker provider', 'Worker boot command', 'Worker model', 'Worker reasoning effort']) await (await reveal(label)).fill('');
    await (await reveal('Worker fast mode')).selectOption('inherit'); await save(); await page.reload();
    await launch('shared', 'shared');
    const output = test.info().outputPath('worker-launch-evidence.json'); await writeFile(output, JSON.stringify(evidence, null, 2)); await test.info().attach('worker-launch-evidence', { path: output, contentType: 'application/json' });
  } finally {
    for (const id of [...ids].reverse()) await command(request, { cmd: 'remove_agent', id });
    await rm(directory, { recursive: true, force: true });
  }
});
