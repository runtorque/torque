import { expect, test, type APIRequestContext } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdir, mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { isAbsolute, join } from 'node:path';
import { fileURLToPath } from 'node:url';
type Row = Record<string, unknown>;
const quote = (value: string) => "'" + value.replaceAll("'", "'\\''") + "'";
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}

test('Companion terminals honor explicit directory, parent worktree and saved terminal/group fallbacks', async ({ page, request }) => {
  test.setTimeout(90_000); page.setDefaultTimeout(12_000);
  test.skip(!process.env.TORQUE_PTY_PYTHON, 'Requires an explicit Python receiver on an isolated QA daemon');
  const python = process.env.TORQUE_PTY_PYTHON!; expect(isAbsolute(python)).toBe(true);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const fixture = await realpath(await mkdtemp(join(tmpdir(), 'torque-companion-terminal-')));
  const root = join(fixture, 'project'); const terminalDirectory = join(fixture, 'terminal default'); const explicitDirectory = join(fixture, 'explicit terminal'); const ordinaryParentDirectory = join(fixture, 'ordinary parent');
  for (const path of [root, terminalDirectory, explicitDirectory, ordinaryParentDirectory]) await mkdir(path);
  const git = (...args: string[]) => execFileSync('git', ['-C', root, ...args], { encoding: 'utf8' }).trim();
  git('init', '-b', 'main'); git('config', 'user.name', 'Torque QA'); git('config', 'user.email', 'torque-qa@example.invalid');
  await writeFile(join(root, '.gitignore'), '.torque/\n'); await writeFile(join(root, 'readme.txt'), 'Companion terminal fixture\n'); git('add', '.'); git('commit', '-m', 'Fixture baseline');
  const group = `Companion defaults ${Date.now()}`; const ids: string[] = []; const requests: Row[] = []; const evidence: Row[] = [];
  const probe = fileURLToPath(new URL('./fixtures/terminal_settings_probe.py', import.meta.url));
  const boot = `exec ${quote(python)} -u ${quote(probe)}`;
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'settings' } });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd !== 'add_terminal') { await route.continue(); return; }
    requests.push(data); const response = await route.fetch(); const body = await response.json() as { data: Row };
    if (typeof body.data.id === 'string') ids.push(body.data.id); await route.fulfill({ response });
  });
  const reveal = async (label: string) => { await page.getByRole('searchbox', { name: 'Search settings' }).fill(label); await page.getByRole('button', { name: new RegExp(`^${label} — `) }).click(); return page.getByLabel(label, { exact: true }); };
  const save = async () => { await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible(); };
  const launch = async (name: string, parent: string, expected: string, explicit = '') => {
    const log = join(fixture, `${name}.jsonl`);
    await page.getByRole('button', { name: /⌁ Agents/ }).click(); await page.getByRole('button', { name: 'Create agent or terminal' }).click(); await page.getByRole('menuitem', { name: 'New Terminal…' }).click();
    const dialog = page.getByRole('dialog', { name: 'New terminal' }); await dialog.getByLabel('Name', { exact: true }).fill(name);
    await dialog.getByRole('combobox', { name: 'Parent agent', exact: true }).selectOption(parent);
    await dialog.getByLabel('Command arguments', { exact: true }).fill(quote(log));
    if (explicit) await dialog.getByLabel('Directory', { exact: true }).fill(explicit);
    await dialog.getByRole('button', { name: 'Create terminal', exact: true }).click(); await expect(dialog).toHaveCount(0);
    let actual: Row = {};
    await expect.poll(async () => { try { actual = JSON.parse((await readFile(log, 'utf8')).trim().split('\n')[0]!) as Row; return actual.directory; } catch { return ''; } }).toBe(expected);
    expect(requests.at(-1)).toMatchObject({ parent_id: parent, name });
    if (explicit) expect(requests.at(-1)?.directory).toBe(explicit); else expect(requests.at(-1)).not.toHaveProperty('directory');
    evidence.push({ name, parent, expected, actual });
  };
  try {
    await page.goto('/');
    for (const [label, value] of [['Default directory', root], ['Terminal directory', terminalDirectory], ['Terminal boot command', boot], ['Terminal shell', '/bin/sh']]) await (await reveal(label!)).fill(value!);
    await save(); await page.reload();
    for (const [label, value] of [['Default directory', root], ['Terminal directory', terminalDirectory], ['Terminal boot command', boot], ['Terminal shell', '/bin/sh']]) await expect(page.getByLabel(label!, { exact: true })).toHaveValue(value!);
    const isolated = await command(request, { cmd: 'add_worker', group, name: 'Isolated parent', provider: 'generic', command: '/bin/cat', directory: root, shell: '/bin/sh', worktree: true }); const parent = String(isolated.id); ids.push(parent);
    const ordinary = await command(request, { cmd: 'add_worker', group, name: 'Ordinary parent', provider: 'generic', command: '/bin/cat', directory: ordinaryParentDirectory, shell: '/bin/sh', worktree: false }); const other = String(ordinary.id); ids.push(other);
    const state = await command(request, { cmd: 'get_state' }); const path = String((state.agents as Record<string, Row>)[parent]!.worktree_path); expect(path).toContain('.torque/worktrees/');
    await launch('Parent checkout', parent, path);
    await launch('Explicit override', parent, explicitDirectory, explicitDirectory);
    await launch('Terminal fallback', other, terminalDirectory);
    await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click(); await (await reveal('Terminal directory')).fill(''); await save(); await page.reload(); await expect(page.getByLabel('Terminal directory', { exact: true })).toHaveValue('');
    await launch('Group fallback', other, root);
    await page.screenshot({ path: test.info().outputPath('companion-terminal-defaults.png'), animations: 'disabled' });
    const output = test.info().outputPath('companion-terminal-evidence.json'); await writeFile(output, JSON.stringify({ requests, evidence }, null, 2)); await test.info().attach('companion-terminal-evidence', { path: output, contentType: 'application/json' });
  } finally {
    for (const id of [...ids].reverse()) await command(request, { cmd: 'remove_agent', id });
    await rm(fixture, { recursive: true, force: true });
  }
});
