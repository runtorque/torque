import { expect, test, type APIRequestContext, type Page, type WebSocketRoute } from '@playwright/test';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { isAbsolute, join } from 'node:path';
import { fileURLToPath } from 'node:url';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const response = await request.post('/api/cmd', { data }); expect(response.ok()).toBe(true);
  const result = await response.json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}
async function setup(request: APIRequestContext, name: string) {
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `${name} ${Date.now()}`;
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'settings' } });
  return group;
}
async function save(page: Page) {
  await page.getByRole('button', { name: 'Save changes', exact: true }).click();
  await expect(page.getByText('Saved', { exact: true })).toBeVisible();
}

test('Global metrics saves and reopens while real collection and Health follow both values', async ({ page, request }) => {
  test.setTimeout(60_000);
  const group = await setup(request, 'Metrics settings');
  const original = (await command(request, { cmd: 'get_global_settings' })).settings as Row;
  const ticks: Row[] = []; const writes: Row[] = []; let socket: WebSocketRoute | undefined; let connections = 0;
  await page.routeWebSocket(/\/ws\?/, (client) => {
    socket = client; connections++; const server = client.connectToServer();
    server.onMessage((message) => { const frame = JSON.parse(String(message)) as Row; if (frame.type === 'metrics_tick') ticks.push(frame); client.send(message); });
  });
  await page.route('**/api/cmd', async (route) => { const data = route.request().postDataJSON() as Row; if (data.cmd === 'update_global_settings') writes.push(data); await route.continue(); });
  try {
    await command(request, { cmd: 'update_global_settings', settings: { metrics_enabled: true } });
    await page.goto('/'); const metrics = page.getByRole('combobox', { name: 'Metrics', exact: true });
    await expect(metrics).toHaveValue('on'); await expect.poll(() => ticks.at(-1)?.enabled, { timeout: 12_000 }).toBe(true);
    await metrics.selectOption('off'); await metrics.focus(); await metrics.evaluate((node) => node.setAttribute('data-retained', 'yes'));
    const before = connections; await socket!.close({ code: 1012, reason: 'Unsaved metrics draft' }); await expect.poll(() => connections).toBeGreaterThan(before);
    await expect(metrics).toHaveValue('off'); await expect(metrics).toBeFocused(); await expect(metrics).toHaveAttribute('data-retained', 'yes'); expect(writes).toHaveLength(0);
    await save(page); expect((await command(request, { cmd: 'get_global_settings' })).settings).toMatchObject({ metrics_enabled: false });
    await expect.poll(() => ticks.at(-1)?.enabled, { timeout: 12_000 }).toBe(false);
    await page.reload(); await expect(metrics).toHaveValue('off');
    await page.getByRole('button', { name: 'Mission Control', exact: true }).click();
    const live = page.getByRole('region', { name: 'Live performance', exact: true });
    await expect(live).toContainText('Metrics collection is off.'); await expect(live.getByRole('article', { name: 'Process memory live' }).locator('strong')).toHaveText('—');
    await page.getByRole('button', { name: 'Settings', exact: true }).click(); await metrics.selectOption('on'); await save(page);
    await expect.poll(() => ticks.at(-1)?.enabled, { timeout: 12_000 }).toBe(true);
    expect((await command(request, { cmd: 'get_global_settings' })).settings).toMatchObject({ metrics_enabled: true });
    await page.reload(); await expect(metrics).toHaveValue('on'); await page.getByRole('button', { name: 'Mission Control', exact: true }).click();
    await expect(live).toContainText('Live metrics'); await expect(live.getByRole('article', { name: 'Process memory live' }).locator('strong')).toHaveText(/\d+ MB/);
    expect(writes).toEqual([{ cmd: 'update_global_settings', settings: { metrics_enabled: false } }, { cmd: 'update_global_settings', settings: { metrics_enabled: true } }]);
    await live.scrollIntoViewIfNeeded(); await page.screenshot({ path: test.info().outputPath('metrics-resumed.png') });
    const path = test.info().outputPath('metrics-transitions.json'); await writeFile(path, JSON.stringify({ group, writes, ticks }, null, 2)); await test.info().attach('metrics-transitions', { path, contentType: 'application/json' });
  } finally { await command(request, { cmd: 'update_global_settings', settings: { metrics_enabled: original.metrics_enabled } }); }
});

test('Global boot nudges preserve multiline drafts and reach their real Engineer and Architect receivers', async ({ page, request }) => {
  test.setTimeout(120_000);
  test.skip(!process.env.TORQUE_PTY_PYTHON, 'Requires an isolated daemon and explicit local Python runtime');
  const python = process.env.TORQUE_PTY_PYTHON!; expect(isAbsolute(python)).toBe(true);
  const group = await setup(request, 'Boot nudge settings');
  const initial = await command(request, { cmd: 'get_global_settings' });
  const original = initial.settings as Row; const serverDefaults = initial.defaults as Row;
  const directory = await mkdtemp(join(tmpdir(), 'torque-boot-nudge-')); const agents: string[] = []; const evidence: Row[] = [];
  const receiver = fileURLToPath(new URL('./fixtures/terminal_settings_probe.py', import.meta.url));
  const quote = (value: string) => "'" + value.replaceAll("'", "'\\''") + "'";
  const fields = [
    { kind: 'engineer', key: 'engineer_default_boot_nudge', label: 'Engineer default boot nudge', text: 'QA_ENGINEER_BOOT first line\n  Preserve indented Engineer continuation.' },
    { kind: 'architect', key: 'architect_default_boot_nudge', label: 'Architect default boot nudge', text: 'QA_ARCHITECT_BOOT first line\n  Preserve indented Architect continuation.' },
  ];
  const writes: Row[] = []; let socket: WebSocketRoute | undefined; let connections = 0; let updates = 0; let refuse = true;
  await page.routeWebSocket(/\/ws\?/, (client) => {
    socket = client; connections++; const server = client.connectToServer();
    server.onMessage((message) => { const frame = JSON.parse(String(message)) as Row; if (frame.type === 'delta' && Array.isArray(frame.ops)) updates += (frame.ops as Row[]).filter((op) => op.op === 'group_settings_update' && op.name === group).length; client.send(message); });
  });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd === 'update_global_settings') { writes.push(data); if (refuse) { refuse = false; await route.fulfill({ json: { ok: false, error: 'QA boot nudge save refused' } }); return; } }
    await route.continue();
  });
  const open = async () => { const summary = page.getByText('Global defaults', { exact: true }); if (!(await summary.evaluate((node) => node.closest('details')?.open))) await summary.click(); };
  const receive = async (kind: string, phase: string) => {
    const path = join(directory, `${kind}-${phase}.jsonl`);
    const agent = await command(request, { cmd: `add_${kind}`, group, name: `Boot QA ${kind} ${phase}`, provider: 'generic', command: [python, '-u', receiver, path].map(quote).join(' '), directory, shell: '/bin/sh', worktree: false });
    const id = String(agent.id); agents.push(id);
    // A direct message queues behind launch prompts; seeing it in the receiver
    // is an observed barrier for checking an intentionally empty boot nudge.
    const marker = `QA_BOOT_BARRIER_${kind}_${phase}`;
    await command(request, { cmd: 'user_agent_message', agent_id: id, thread_id: `user-agent:user:${id}`, message: marker, idempotency_key: `${group}-${kind}-${phase}` });
    const read = async () => { try { return (await readFile(path, 'utf8')).trim().split('\n').map((line) => JSON.parse(line) as Row).filter((row) => row.kind === 'input').map((row) => String(row.text)).join('\n'); } catch { return ''; } };
    await expect.poll(read, { timeout: 25_000 }).toContain(marker);
    const input = await read(); evidence.push({ kind, phase, input }); await command(request, { cmd: 'remove_agent', id }); agents.splice(agents.indexOf(id), 1); return input;
  };
  try {
    await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: directory, git_worktree: false, default_agent_template: '', agent_provider: 'generic' } });
    await page.goto('/'); await open();
    for (const [index, field] of fields.entries()) {
      const input = page.getByRole('textbox', { name: field.label, exact: true }); await expect(input).toHaveValue(String(original[field.key]));
      await input.fill(field.text); await input.evaluate((node: HTMLTextAreaElement) => { node.setAttribute('data-retained', 'yes'); node.setSelectionRange(2, 9); });
      const beforeUpdates = updates; await command(request, { cmd: 'update_group_settings', group, settings: { max_agents: index + 3 } }); await expect.poll(() => updates).toBeGreaterThan(beforeUpdates);
      const before = connections; await socket!.close({ code: 1012, reason: 'Boot nudge draft reconnect' }); await expect.poll(() => connections).toBeGreaterThan(before);
      await expect(input).toHaveValue(field.text); await expect(input).toBeFocused(); await expect(input).toHaveAttribute('data-retained', 'yes'); expect(await input.evaluate((node: HTMLTextAreaElement) => [node.selectionStart, node.selectionEnd])).toEqual([2, 9]);
    }
    expect(writes).toHaveLength(0); await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByRole('alert')).toContainText('QA boot nudge save refused');
    for (const field of fields) await expect(page.getByRole('textbox', { name: field.label, exact: true })).toHaveValue(field.text);
    await save(page); const expected = Object.fromEntries(fields.map((field) => [field.key, field.text]));
    expect(writes).toEqual(Array.from({ length: 2 }, () => ({ cmd: 'update_global_settings', settings: expected })));
    expect((await command(request, { cmd: 'get_global_settings' })).settings).toMatchObject(expected);
    await page.reload(); await open();
    for (const field of fields) { await expect(page.getByRole('textbox', { name: field.label, exact: true })).toHaveValue(field.text); const input = await receive(field.kind, 'custom'); expect(input).toContain(field.text); expect(input).not.toContain(fields.find((other) => other.kind !== field.kind)!.text); }
    for (const field of fields) await page.getByRole('textbox', { name: field.label, exact: true }).fill('');
    await save(page); await page.reload(); await open();
    for (const field of fields) { await expect(page.getByRole('textbox', { name: field.label, exact: true })).toHaveValue(''); const input = await receive(field.kind, 'empty'); expect(input).not.toContain(field.text); expect(input).not.toContain(String(serverDefaults[field.key])); }
    for (const field of fields) await page.getByRole('button', { name: `Reset ${field.label}`, exact: true }).click();
    await save(page); const defaults = (await command(request, { cmd: 'get_global_settings' })).settings as Row;
    for (const field of fields) expect(defaults[field.key]).toBe(serverDefaults[field.key]);
    await page.reload(); await open(); for (const field of fields) await expect(page.getByRole('textbox', { name: field.label, exact: true })).toHaveValue(String(serverDefaults[field.key]));
    await page.getByRole('textbox', { name: fields[0]!.label, exact: true }).scrollIntoViewIfNeeded(); await page.screenshot({ path: test.info().outputPath('boot-nudge-defaults.png') });
    const path = test.info().outputPath('boot-nudge-receivers.json'); await writeFile(path, JSON.stringify(evidence, null, 2)); await test.info().attach('boot-nudge-receivers', { path, contentType: 'application/json' });
  } finally {
    for (const id of agents.reverse()) await command(request, { cmd: 'remove_agent', id });
    await command(request, { cmd: 'update_global_settings', settings: Object.fromEntries(fields.map((field) => [field.key, original[field.key]])) });
    await rm(directory, { recursive: true, force: true });
  }
});
