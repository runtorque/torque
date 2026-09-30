import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
import { mkdtemp, mkdir, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
type Row = Record<string, unknown>;
type Principal = 'engineer' | 'architect';
const quote = (value: string) => "'" + value.replaceAll("'", "'\\''") + "'";
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}

test('Principal launch defaults persist, retain drafts and drive specific, explicit and shared fallback launches', async ({ page, request }) => {
  test.setTimeout(120_000); page.setDefaultTimeout(12_000);
  test.skip(!process.env.TORQUE_PTY_PYTHON, 'Requires explicit local Python and an isolated daemon');
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const root = await realpath(await mkdtemp(join(tmpdir(), 'torque-principal-defaults-')));
  const dirs = { shared: join(root, 'shared workspace'), engineer: join(root, 'engineer workspace'), architect: join(root, 'architect workspace') };
  for (const directory of Object.values(dirs)) await mkdir(directory);
  const probe = fileURLToPath(new URL('./fixtures/terminal_settings_probe.py', import.meta.url));
  const boot = (phase: string) => 'if [ -n "${BASH_VERSION:-}" ]; then export QA_SHELL=bash; elif [ -n "${ZSH_VERSION:-}" ]; then export QA_SHELL=zsh; else export QA_SHELL=unknown; fi; exec ' + `${quote(process.env.TORQUE_PTY_PYTHON!)} -u ${quote(probe)} ${quote(root)}/"$TORQUE_CELL_ID.jsonl" ${quote(phase)}`;
  const group = `Principal defaults ${Date.now()}`; const ids: string[] = []; const writes: Row[] = []; const launches: Row[] = []; const evidence: Row[] = [];
  let socket: WebSocketRoute | undefined; let connections = 0;
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: root, default_agent_template: '', git_worktree: false } });
  await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'settings' } });
  await page.routeWebSocket(/\/ws\?/, (client) => { socket = client; connections++; client.connectToServer(); });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (['update_group_settings', 'engineer_update_settings', 'update_architect_settings'].includes(String(data.cmd))) writes.push(data);
    if (!['add_engineer', 'add_architect'].includes(String(data.cmd))) { await route.continue(); return; }
    launches.push(data); const response = await route.fetch(); const body = await response.json() as { data: Row };
    if (typeof body.data.id === 'string') ids.push(body.data.id); await route.fulfill({ response });
  });
  const reveal = async (label: string) => { await page.getByRole('searchbox', { name: 'Search settings' }).fill(label); await page.getByRole('button', { name: new RegExp(`^${label} — `) }).click(); return page.getByLabel(label, { exact: true }); };
  const edit = async (label: string, value: string) => { const input = await reveal(label); if (await input.evaluate((node) => node.tagName === 'SELECT')) await input.selectOption(value); else await input.fill(value); };
  const save = async () => { await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible(); };
  const openSettings = async () => { await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click(); };
  const fields = ['provider', 'boot command', 'model', 'reasoning effort', 'fast mode', 'directory', 'shell'] as const;
  const values = {
    Agent: ['generic', boot('shared'), 'shared-model', 'low', 'off', dirs.shared, 'bash'],
    Engineer: ['generic', boot('engineer'), 'engineer-model', 'high', 'on', dirs.engineer, 'bash'],
    Architect: ['generic', boot('architect'), 'architect-model', 'medium', 'off', dirs.architect, 'zsh'],
  };
  const launch = async (kind: Principal, mode: 'specific' | 'explicit' | 'fallback') => {
    const phase = mode === 'fallback' ? 'shared' : mode === 'explicit' ? `${kind}-explicit` : kind;
    const directory = mode === 'specific' ? dirs[kind] : dirs.shared;
    const shell = (mode === 'specific' && kind === 'architect') || (mode === 'explicit' && kind === 'engineer') ? 'zsh' : 'bash';
    const model = mode === 'fallback' ? 'shared-model' : `${phase}-model`;
    const effort = mode === 'fallback' ? 'low' : mode === 'explicit' ? 'minimal' : kind === 'engineer' ? 'high' : 'medium';
    const fast = mode === 'explicit' ? 'on' : mode === 'specific' && kind === 'engineer' ? 'on' : 'off';
    await page.getByRole('button', { name: /⌁ Agents/ }).click(); await page.getByRole('button', { name: 'Create agent or terminal' }).click(); await page.getByRole('menuitem', { name: `New ${kind === 'engineer' ? 'Engineer' : 'Architect'}…` }).click();
    const dialog = page.getByRole('dialog', { name: `New ${kind}` }); await dialog.getByLabel('Name', { exact: true }).fill(`${mode} ${kind}`);
    if (mode === 'explicit') {
      for (const [label, value] of [['Provider', 'generic'], ['Boot command', boot(phase)], ['Model', model], ['Reasoning effort', effort], ['Directory', directory]]) await dialog.getByLabel(label!, { exact: true }).fill(value!);
      await dialog.getByLabel('Icon', { exact: true }).fill(kind === 'engineer' ? 'E' : 'A');
      await dialog.getByLabel('Environment variables', { exact: true }).fill('QA_AGENT=  exact = value  \nQA_EMPTY=');
      await dialog.getByRole('combobox', { name: 'Fast mode', exact: true }).selectOption(fast); await dialog.getByRole('combobox', { name: 'Shell', exact: true }).selectOption(shell);
    }
    await dialog.getByRole('button', { name: `Create ${kind}`, exact: true }).click(); await expect(dialog).toHaveCount(0);
    const id = ids.at(-1)!; let process: Row = {};
    await expect.poll(async () => {
      try { process = JSON.parse((await readFile(join(root, `${id}.jsonl`), 'utf8')).split('\n')[0]!) as Row; return process; }
      catch { return {}; }
    }).toMatchObject({ kind: 'launch', directory, args: [phase], env: { QA_SHELL: shell } });
    if (mode === 'explicit') {
      expect(process.env).toMatchObject({ QA_AGENT: '  exact = value  ', QA_EMPTY: '' });
      const cell = ((await command(request, { cmd: 'get_state' })).agents as Record<string, Row>)[id]!;
      expect(cell.icon).toBe(kind === 'engineer' ? 'E' : 'A');
      const row = page.locator(`[role="treeitem"][data-agent-id="${id}"]`);
      await expect(row.getByText(kind === 'engineer' ? 'E' : 'A', { exact: true })).toBeVisible();
      await page.screenshot({ path: test.info().outputPath(`${kind}-created-icon.png`), animations: 'disabled' });
    }
    const resolved = (await command(request, { cmd: 'get_agent_settings', agent_id: id })).resolved as Row;
    const origin = mode === 'explicit' ? 'per-agent' : 'group';
    expect(resolved).toMatchObject({ provider: { value: 'generic', origin }, boot_command: { value: boot(phase), origin }, model: { value: model, origin }, reasoning_effort: { value: effort, origin }, fast_mode: { value: fast, origin } });
    if (mode !== 'explicit') for (const key of ['provider', 'command', 'model', 'reasoning_effort', 'fast_mode', 'directory', 'shell']) expect(launches.at(-1)).not.toHaveProperty(key);
    evidence.push({ kind, mode, id, process, resolved, launch: launches.at(-1) });
  };
  try {
    await page.goto('/');
    for (const [scope, entries] of Object.entries(values)) for (const [index, field] of fields.entries()) await edit(`${scope} ${field}`, entries[index]!);
    const retained = await reveal('Engineer reasoning effort'); await retained.focus(); await retained.evaluate((node: HTMLInputElement) => { node.setSelectionRange(1, 3); node.dataset.retained = 'yes'; });
    const before = connections; await socket!.close({ code: 1012, reason: 'Principal launch defaults draft' }); await expect.poll(() => connections).toBeGreaterThan(before);
    await expect(retained).toHaveValue('high'); await expect(retained).toBeFocused(); await expect(retained).toHaveAttribute('data-retained', 'yes'); expect(await retained.evaluate((node: HTMLInputElement) => [node.selectionStart, node.selectionEnd])).toEqual([1, 3]); expect(writes).toHaveLength(0);
    await save(); await page.reload();
    for (const [scope, entries] of Object.entries(values)) for (const [index, field] of fields.entries()) await expect(await reveal(`${scope} ${field}`)).toHaveValue(entries[index]!);
    for (const kind of ['engineer', 'architect'] as const) { await launch(kind, 'specific'); await launch(kind, 'explicit'); }
    await openSettings();
    for (const scope of ['Engineer', 'Architect']) for (const field of fields) await edit(`${scope} ${field}`, field === 'fast mode' ? 'inherit' : '');
    await save(); await page.reload();
    for (const scope of ['Engineer', 'Architect']) for (const field of fields) await expect(await reveal(`${scope} ${field}`)).toHaveValue(field === 'fast mode' ? 'inherit' : '');
    for (const kind of ['engineer', 'architect'] as const) await launch(kind, 'fallback');
    await openSettings(); await reveal('Architect reasoning effort'); await page.screenshot({ path: test.info().outputPath('principal-launch-inheritance.png'), animations: 'disabled' });
    const output = test.info().outputPath('principal-launch-evidence.json'); await writeFile(output, JSON.stringify({ writes, evidence }, null, 2)); await test.info().attach('principal-launch-evidence', { path: output, contentType: 'application/json' });
  } finally {
    for (const id of [...ids].reverse()) await command(request, { cmd: 'remove_agent', id });
    await rm(root, { recursive: true, force: true });
  }
});
