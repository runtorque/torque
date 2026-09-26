import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
import { mkdtemp, mkdir, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { isAbsolute, join } from 'node:path';
import { fileURLToPath } from 'node:url';
type Row = Record<string, unknown>;
const quote = (value: string) => "'" + value.replaceAll("'", "'\\''") + "'";
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}

test('Terminal settings launch real inherited and overridden PTYs and honor close-on-disconnect', async ({ page, request }) => {
  test.setTimeout(90_000);
  test.skip(!process.env.TORQUE_PTY_PYTHON, 'Requires an isolated daemon and explicit local Python runtime');
  const python = process.env.TORQUE_PTY_PYTHON!; expect(isAbsolute(python)).toBe(true);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const directory = await realpath(await mkdtemp(join(tmpdir(), 'torque-terminal-settings-')));
  const inheritedDirectory = join(directory, 'inherited workspace'); const overrideDirectory = join(directory, 'explicit workspace');
  await mkdir(inheritedDirectory); await mkdir(overrideDirectory);
  const envFile = join(directory, 'terminal environment.sh'); const initFile = join(directory, 'terminal initialization.sh');
  await writeFile(envFile, "export QA_FILE='from environment file'\n");
  await writeFile(initFile, "export QA_INIT='from initialization script'\nif [ -n \"$BASH_VERSION\" ]; then export QA_SHELL=bash; else export QA_SHELL=unexpected; fi\n");
  const probe = fileURLToPath(new URL('./fixtures/terminal_settings_probe.py', import.meta.url));
  const logs = [join(directory, 'inherited.jsonl'), join(directory, 'explicit.jsonl'), join(directory, 'fallback.jsonl')];
  const boot = `exec ${quote(python)} -u ${quote(probe)}`;
  const group = `Terminal settings ${Date.now()}`; const created: string[] = []; const launches: Row[] = [];
  let socket: WebSocketRoute | undefined; let connections = 0; const updates: Row[] = [];
  await page.routeWebSocket(/\/ws\?/, (client) => {
    socket = client; connections++; const server = client.connectToServer();
    server.onMessage((message) => {
      const frame = JSON.parse(String(message)) as Row;
      if (frame.type === 'delta' && Array.isArray(frame.ops)) updates.push(...(frame.ops as Row[]).filter((op) => op.op === 'group_settings_update' && op.name === group));
      client.send(message);
    });
  });
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'settings' } });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd !== 'add_terminal') { await route.continue(); return; }
    launches.push(data); const response = await route.fetch(); const body = await response.json() as { data: Row };
    if (typeof body.data.id === 'string') created.push(body.data.id);
    await route.fulfill({ response });
  });
  const read = async (index: number) => {
    try { return (await readFile(logs[index]!, 'utf8')).trim().split('\n').map((line) => JSON.parse(line) as Row); }
    catch { return []; }
  };
  const reveal = async (label: string) => {
    await page.getByRole('searchbox', { name: 'Search settings' }).fill(label);
    await page.getByRole('button', { name: new RegExp(`^${label} — `) }).click();
    return page.getByLabel(label, { exact: true });
  };
  const addEnv = async (label: string, key: string, value: string) => {
    const map = page.getByRole('group', { name: label, exact: true });
    await map.getByLabel(`New ${label.toLowerCase()} key`, { exact: true }).fill(key);
    await map.getByRole('button', { name: 'Add entry', exact: true }).click();
    await map.getByLabel(`${label}: ${key}`, { exact: true }).fill(value);
  };
  const openTerminal = async (name: string) => {
    await page.getByRole('button', { name: /⌁ Agents/ }).click();
    await page.getByRole('button', { name: 'Create agent or terminal' }).click();
    await page.getByRole('menuitem', { name: 'New Terminal…' }).click();
    const dialog = page.getByRole('dialog', { name: 'New terminal' });
    await dialog.getByLabel('Name', { exact: true }).fill(name); return dialog;
  };
  try {
    await page.goto('/');
    const entries = [
      ['Default directory', directory], ['Terminal boot command', boot],
      ['Terminal command args', `${quote(logs[0]!)} ${quote('inherited argument with spaces')}`],
      ['Terminal init script', initFile], ['Terminal directory', inheritedDirectory],
      ['Terminal shell', '/bin/bash'], ['Terminal env file', envFile],
    ] as const;
    for (const [index, [label, value]] of entries.entries()) {
      const input = await reveal(label); await input.fill(value);
      await input.evaluate((node) => (node as HTMLInputElement).setSelectionRange(2, 2));
      const notify = index % 2 === 1; const updateCount = updates.length;
      await command(request, { cmd: 'update_group_settings', group, settings: { notify_on_finish: notify } });
      await expect.poll(() => updates.slice(updateCount).some((op) => op.notify_on_finish === notify)).toBe(true);
      await expect(input).toHaveValue(value); await expect(input).toBeFocused();
      expect(await input.evaluate((node) => (node as HTMLInputElement).selectionStart)).toBe(2);
      // The form reconciles a fresh scoped read on reconnect, retaining its draft.
      const previousConnections = connections;
      await socket!.close({ code: 1012, reason: 'Terminal default draft acceptance' });
      await expect.poll(() => connections).toBeGreaterThan(previousConnections);
      await expect(page.getByLabel('Notify on finish', { exact: true })).toHaveValue(String(notify));
      await expect(input).toHaveValue(value); await expect(input).toBeFocused();
      expect(await input.evaluate((node) => (node as HTMLInputElement).selectionStart)).toBe(2);
    }
    await page.getByRole('searchbox', { name: 'Search settings' }).fill('');
    await addEnv('Env vars', 'QA_GROUP', 'group base'); await addEnv('Env vars', 'QA_OVERLAP', 'group value');
    await addEnv('Terminal env vars', 'QA_TERMINAL', 'terminal default'); await addEnv('Terminal env vars', 'QA_OVERLAP', 'terminal value');
    await page.getByRole('button', { name: 'Save changes', exact: true }).click();
    await expect(page.getByText('Saved', { exact: true })).toBeVisible();
    await page.reload();
    for (const [label, value] of entries) await expect(page.getByLabel(label, { exact: true })).toHaveValue(value);
    for (const [label, value] of [['Env vars: QA_GROUP', 'group base'], ['Env vars: QA_OVERLAP', 'group value'], ['Terminal env vars: QA_TERMINAL', 'terminal default'], ['Terminal env vars: QA_OVERLAP', 'terminal value']]) await expect(page.getByLabel(label!, { exact: true })).toHaveValue(value!);

    const first = await openTerminal('Inherited terminal');
    await first.getByRole('button', { name: 'Create terminal', exact: true }).click(); await expect(first).toHaveCount(0);
    await expect.poll(async () => (await read(0))[0]).toMatchObject({ kind: 'launch', directory: inheritedDirectory,
      args: ['inherited argument with spaces'], env: { QA_GROUP: 'group base', QA_TERMINAL: 'terminal default', QA_OVERLAP: 'terminal value', QA_FILE: 'from environment file', QA_INIT: 'from initialization script', QA_SHELL: 'bash' } });
    for (const key of ['directory', 'shell', 'command', 'command_args', 'env_vars', 'init_script']) expect(launches[0]).not.toHaveProperty(key);
    await expect(page.getByRole('region', { name: 'Inherited terminal terminal', exact: true }).getByText('connected', { exact: true })).toBeVisible();
    await page.locator('.xterm-helper-textarea').focus(); await page.keyboard.type('exit-probe'); await page.keyboard.press('Enter');
    await expect.poll(async () => (await read(0)).some((row) => row.kind === 'input' && row.text === 'exit-probe')).toBe(true);
    await expect(page.getByText('Terminal stopped', { exact: true })).toBeVisible();
    await expect(page.getByRole('treeitem', { name: /^Inherited terminal,/ })).toBeVisible();

    await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click();
    await (await reveal('Terminal close on disconnect')).selectOption('true');
    await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible();
    const second = await openTerminal('Explicit terminal');
    await second.getByLabel('Directory', { exact: true }).fill(overrideDirectory);
    await second.getByLabel('Command arguments', { exact: true }).fill(`${quote(logs[1]!)} ${quote('explicit argument with spaces')}`);
    await second.getByLabel('Environment variables', { exact: true }).fill('QA_OVERLAP=explicit value');
    await second.getByRole('button', { name: 'Create terminal', exact: true }).click(); await expect(second).toHaveCount(0);
    await expect.poll(async () => (await read(1))[0]).toMatchObject({ kind: 'launch', directory: overrideDirectory,
      args: ['explicit argument with spaces'], env: { QA_GROUP: 'group base', QA_TERMINAL: 'terminal default', QA_OVERLAP: 'explicit value', QA_FILE: 'from environment file', QA_INIT: 'from initialization script', QA_SHELL: 'bash' } });
    await expect(page.getByRole('region', { name: 'Explicit terminal terminal', exact: true }).getByText('connected', { exact: true })).toBeVisible();
    await page.screenshot({ path: test.info().outputPath('terminal-settings-runtime.png') });
    await page.locator('.xterm-helper-textarea').focus(); await page.keyboard.type('exit-probe'); await page.keyboard.press('Enter');
    await expect.poll(async () => (await read(1)).some((row) => row.kind === 'input' && row.text === 'exit-probe')).toBe(true);
    await expect(page.getByRole('treeitem', { name: /^Explicit terminal,/ })).toHaveCount(0);
    await expect(page.getByRole('treeitem', { name: /^Inherited terminal,/ })).toBeVisible();

    // Empty terminal overrides inherit the ordinary group launch defaults.
    const groupEnvFile = join(directory, 'group environment.sh');
    await writeFile(groupEnvFile, "export QA_FILE='from group environment file'\n");
    await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click();
    await (await reveal('Shell')).selectOption('bash');
    await (await reveal('Env file')).fill(groupEnvFile);
    for (const label of ['Terminal directory', 'Terminal shell', 'Terminal env file']) await (await reveal(label)).fill('');
    await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible();
    await page.reload();
    for (const label of ['Terminal directory', 'Terminal shell', 'Terminal env file']) await expect(page.getByLabel(label, { exact: true })).toHaveValue('');
    const third = await openTerminal('Group fallback terminal');
    await third.getByLabel('Command arguments', { exact: true }).fill(`${quote(logs[2]!)} ${quote('group fallback argument')}`);
    await third.getByRole('button', { name: 'Create terminal', exact: true }).click(); await expect(third).toHaveCount(0);
    await expect.poll(async () => (await read(2))[0]).toMatchObject({ kind: 'launch', directory,
      args: ['group fallback argument'], env: { QA_GROUP: 'group base', QA_TERMINAL: 'terminal default', QA_OVERLAP: 'terminal value', QA_FILE: 'from group environment file', QA_INIT: 'from initialization script', QA_SHELL: 'bash' } });
    for (const key of ['directory', 'shell', 'command', 'env_vars', 'init_script']) expect(launches[2]).not.toHaveProperty(key);
    await expect(page.getByRole('region', { name: 'Group fallback terminal terminal', exact: true }).getByText('connected', { exact: true })).toBeVisible();
    await page.locator('.xterm-helper-textarea').focus(); await page.keyboard.type('exit-probe'); await page.keyboard.press('Enter');
    await expect(page.getByRole('treeitem', { name: /^Group fallback terminal,/ })).toHaveCount(0);
    const evidence = test.info().outputPath('terminal-launch-evidence.json');
    await writeFile(evidence, JSON.stringify({ launches: await Promise.all(logs.map((_, index) => read(index))), requests: launches }, null, 2));
    await test.info().attach('terminal-launch-evidence', { path: evidence, contentType: 'application/json' });
  } finally {
    for (const id of created) await command(request, { cmd: 'remove_agent', id });
    await rm(directory, { recursive: true, force: true });
  }
});
