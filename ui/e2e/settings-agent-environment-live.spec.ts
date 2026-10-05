import { expect, test, type APIRequestContext, type Page, type WebSocketRoute } from '@playwright/test';
import { mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { isAbsolute, join } from 'node:path';
import { fileURLToPath } from 'node:url';
type Row = Record<string, unknown>;
const quote = (value: string) => "'" + value.replaceAll("'", "'\\''") + "'";
async function command(request: APIRequestContext, data: Row) {
  const response = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(response.ok, response.error).toBe(true); expect(response.data.type).not.toBe('error'); return response.data;
}
async function save(page: Page) { await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible(); }

test('Agent environment defaults persist exact maps, reach each agent kind and fall back after explicit removal', async ({ page, request }) => {
  test.setTimeout(150_000);
  test.skip(!process.env.TORQUE_PTY_PYTHON, 'Requires an isolated daemon and explicit local Python runtime');
  const python = process.env.TORQUE_PTY_PYTHON!; expect(isAbsolute(python)).toBe(true);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const directory = await realpath(await mkdtemp(join(tmpdir(), 'torque-agent-environment-')));
  const groupFile = join(directory, 'group environment.sh'); const agentFile = join(directory, 'agent environment.sh');
  await writeFile(groupFile, "export QA_FILE='from group environment file'\n");
  await writeFile(agentFile, "export QA_FILE='from agent environment file'\n");
  const receiver = fileURLToPath(new URL('./fixtures/terminal_settings_probe.py', import.meta.url));
  const group = `Agent environment ${Date.now()}`; const ids: string[] = []; const writes: Row[] = []; const launches: Row[] = []; const evidence: Row[] = [];
  let socket: WebSocketRoute | undefined; let connections = 0; let updates = 0; let refuse = true;
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: directory, git_worktree: false, agent_provider: 'generic', default_agent_template: '' } });
  await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'settings' } });
  await page.routeWebSocket(/\/ws\?/, (client) => {
    socket = client; connections++; const server = client.connectToServer();
    server.onMessage((message) => { const frame = JSON.parse(String(message)) as Row; if (frame.type === 'delta' && Array.isArray(frame.ops)) updates += (frame.ops as Row[]).filter((op) => op.op === 'group_settings_update' && op.name === group).length; client.send(message); });
  });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd === 'update_group_settings') { writes.push(data); if (refuse) { refuse = false; await route.fulfill({ json: { ok: false, error: 'QA environment save refused' } }); return; } }
    if (['add_worker', 'add_engineer', 'add_architect'].includes(String(data.cmd))) {
      launches.push(data); const response = await route.fetch(); const body = await response.json() as { data: Row }; if (typeof body.data.id === 'string') ids.push(body.data.id); await route.fulfill({ response }); return;
    }
    await route.continue();
  });
  const openSettings = async () => {
    await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click();
    const summary = page.getByText(`${group} execution, worktrees, notifications and sync`, { exact: true }); if (!(await summary.evaluate((node) => node.closest('details')?.open))) await summary.click();
  };
  const add = async (label: string, key: string, value: string) => {
    const map = page.getByRole('group', { name: label, exact: true }); await map.getByLabel(`New ${label.toLowerCase()} key`, { exact: true }).fill(key); await map.getByRole('button', { name: 'Add entry', exact: true }).click(); await map.getByLabel(`${label}: ${key}`, { exact: true }).fill(value);
  };
  const launch = async (kind: 'worker' | 'engineer' | 'architect', phase: string, expected: Row, override?: string) => {
    const output = join(directory, `${kind}-${phase}.jsonl`); const name = `Environment ${kind} ${phase}`;
    await page.getByRole('button', { name: /⌁ Agents/ }).click(); await page.getByRole('button', { name: 'Create agent or terminal' }).click();
    await page.getByRole('menuitem', { name: `New ${kind[0]!.toUpperCase()}${kind.slice(1)}…` }).click();
    const dialog = page.getByRole('dialog', { name: `New ${kind}`, exact: true }); await dialog.getByLabel('Name', { exact: true }).fill(name);
    await dialog.getByLabel('Provider', { exact: true }).fill('generic'); await dialog.getByLabel('Boot command', { exact: true }).fill([python, '-u', receiver, output].map(quote).join(' '));
    const isolation = dialog.getByLabel('Create an isolated worktree', { exact: true });
    if (kind === 'worker') await isolation.uncheck();
    else await expect(isolation).toHaveCount(0);
    if (override !== undefined) await dialog.getByRole('textbox', { name: 'Environment variables', exact: true }).fill(override);
    await dialog.getByRole('button', { name: `Create ${kind}`, exact: true }).click(); await expect(dialog).toHaveCount(0);
    const read = async () => { try { return JSON.parse((await readFile(output, 'utf8')).split('\n')[0]!) as Row; } catch { return null; } };
    await expect.poll(read, { timeout: 20_000 }).toMatchObject({ kind: 'launch', directory, env: expected });
    evidence.push({ kind, phase, launch: await read(), request: launches.at(-1) });
    await command(request, { cmd: 'remove_agent', id: ids.at(-1) }); ids.pop();
  };
  try {
    await page.goto('/'); await openSettings();
    await page.getByLabel('Env file', { exact: true }).fill(groupFile); await page.getByLabel('Agent env file', { exact: true }).fill(`  ${agentFile}  `);
    await add('Env vars', 'QA_GROUP', '  group value with spaces  '); await add('Env vars', 'QA_OVERLAP', 'group fallback');
    await add('Agent env vars', 'QA_AGENT', 'agent value'); await add('Agent env vars', 'QA_OVERLAP', '  agent override = exact  '); await add('Agent env vars', 'QA_EMPTY', '');
    const retained = page.getByLabel('Agent env vars: QA_OVERLAP', { exact: true }); await retained.focus(); await retained.evaluate((node: HTMLInputElement) => { node.setSelectionRange(2, 7); node.dataset.retained = 'yes'; });
    const beforeUpdates = updates; await command(request, { cmd: 'update_group_settings', group, settings: { max_agents: 12 } }); await expect.poll(() => updates).toBeGreaterThan(beforeUpdates);
    const before = connections; await socket!.close({ code: 1012, reason: 'Agent environment map draft' }); await expect.poll(() => connections).toBeGreaterThan(before);
    await expect(retained).toHaveValue('  agent override = exact  '); await expect(retained).toBeFocused(); await expect(retained).toHaveAttribute('data-retained', 'yes'); expect(await retained.evaluate((node: HTMLInputElement) => [node.selectionStart, node.selectionEnd])).toEqual([2, 7]); expect(writes).toHaveLength(0);
    await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByRole('alert')).toContainText('QA environment save refused'); await expect(retained).toHaveValue('  agent override = exact  ');
    await save(page); expect(writes[1]).toEqual(writes[0]);
    const expectedSettings = { env_file: groupFile, agent_env_file: agentFile, env_vars: { QA_GROUP: '  group value with spaces  ', QA_OVERLAP: 'group fallback' }, agent_env_vars: { QA_AGENT: 'agent value', QA_OVERLAP: '  agent override = exact  ', QA_EMPTY: '' } };
    expect(writes[1]!.settings).toEqual(expectedSettings); expect((await command(request, { cmd: 'get_group_settings', group })).settings).toMatchObject(expectedSettings);
    await page.reload(); await openSettings(); await expect(page.getByLabel('Agent env file', { exact: true })).toHaveValue(agentFile); await expect(retained).toHaveValue('  agent override = exact  '); await expect(page.getByLabel('Agent env vars: QA_EMPTY', { exact: true })).toHaveValue(''); await expect(page.getByLabel('Agent env vars: QA_EMPTY', { exact: true })).toHaveAttribute('placeholder', '');
    await page.getByRole('group', { name: 'Agent env vars', exact: true }).scrollIntoViewIfNeeded(); await page.screenshot({ path: test.info().outputPath('agent-environment-settings.png') });
    const inherited = { QA_GROUP: '  group value with spaces  ', QA_AGENT: 'agent value', QA_OVERLAP: '  agent override = exact  ', QA_EMPTY: '', QA_FILE: 'from agent environment file' };
    for (const kind of ['worker', 'engineer', 'architect'] as const) { await launch(kind, 'inherited', inherited); if (kind === 'worker') expect(launches.at(-1)?.env_vars).toEqual(expectedSettings.agent_env_vars); else expect(launches.at(-1)).not.toHaveProperty('env_vars'); }
    await launch('worker', 'explicit', { ...inherited, QA_OVERLAP: 'creation override', QA_EMPTY: 'set by creation' }, 'QA_OVERLAP=creation override\nQA_EMPTY=set by creation');
    expect(launches.at(-1)?.env_vars).toEqual({ QA_OVERLAP: 'creation override', QA_EMPTY: 'set by creation' });
    await openSettings(); await page.getByLabel('Agent env file', { exact: true }).fill('');
    for (const key of ['QA_AGENT', 'QA_OVERLAP', 'QA_EMPTY']) await page.getByRole('button', { name: `Remove Agent env vars: ${key}`, exact: true }).click();
    await save(page); expect(writes.at(-1)?.settings).toEqual({ agent_env_file: '', agent_env_vars: {} });
    await page.reload(); await openSettings(); await expect(page.getByLabel('Agent env file', { exact: true })).toHaveValue(''); await expect(page.getByLabel('Agent env vars: QA_OVERLAP', { exact: true })).toHaveCount(0);
    await launch('worker', 'fallback', { QA_GROUP: '  group value with spaces  ', QA_AGENT: null, QA_OVERLAP: 'group fallback', QA_EMPTY: null, QA_FILE: 'from group environment file' });
    await openSettings(); await page.getByLabel('Env file', { exact: true }).fill(''); for (const key of ['QA_GROUP', 'QA_OVERLAP']) await page.getByRole('button', { name: `Remove Env vars: ${key}`, exact: true }).click(); await save(page);
    expect(writes.at(-1)?.settings).toEqual({ env_file: '', env_vars: {} });
    await page.reload(); await openSettings(); await expect(page.getByLabel('Env file', { exact: true })).toHaveValue('');
    await launch('worker', 'cleared', { QA_GROUP: null, QA_AGENT: null, QA_OVERLAP: null, QA_EMPTY: null, QA_FILE: null });
    const path = test.info().outputPath('agent-environment-launches.json'); await writeFile(path, JSON.stringify({ writes, evidence }, null, 2)); await test.info().attach('agent-environment-launches', { path, contentType: 'application/json' });
  } finally { for (const id of ids) await command(request, { cmd: 'remove_agent', id }); await rm(directory, { recursive: true, force: true }); }
});
