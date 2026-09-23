import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
function text(value: unknown): string { return typeof value === 'string' ? value : ''; }
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
test('inherited launch choices follow shared drafts and preserve sparse overrides after save and reconnect', async ({ page, request }) => {
  test.setTimeout(90_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Launch inheritance ${Date.now()}`;
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  const initial = await command(request, { cmd: 'get_group_settings', group }); const providers = initial.providers as Row[];
  const available = providers.filter((item) => Array.isArray(item.models) && item.models.length); expect(available.length).toBeGreaterThan(1);
  const provider = available.find((item) => (item.models as Row[]).some((model) => model.is_default)) ?? available[1]!;
  const models = provider.models as Row[]; const model = models.find((item) => item.is_default) ?? models[0]!;
  const modelId = String(model.id || model.model); const modelLabel = text(model.display_name) || text(model.displayName) || modelId;
  const modelIds = [...new Set(models.map((item) => String(item.id || item.model)))];
  const efforts = (model.reasoning_efforts ?? model.supported_reasoning_efforts ?? provider.reasoning_efforts) as (Row | string)[];
  const effortIds = [...new Set(efforts.map((item) => typeof item === 'string' ? item : String(item.value || item.reasoning_effort || item.reasoningEffort)))];
  expect(effortIds.length).toBeGreaterThan(0);
  const writes: Row[] = []; let socket: WebSocketRoute | undefined; let connections = 0;
  await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections++; });
  await page.route('**/api/cmd', async (route) => { const data = route.request().postDataJSON() as Row; if (/^(update_|engineer_update_)/.test(String(data.cmd))) writes.push(data); await route.continue(); });
  const open = async () => { await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click(); await page.getByText(`${group} execution, worktrees, notifications and sync`, { exact: true }).click(); await page.getByText('Engineer behavior defaults', { exact: true }).click(); await page.getByText('Architect behavior defaults', { exact: true }).click(); };
  const choices = (label: string) => page.getByLabel(label, { exact: true }).evaluate((input) => { const list = document.getElementById(input.getAttribute('list') ?? ''); return list ? Array.from(list.querySelectorAll('option')).map((option) => option.value) : []; });
  const save = async () => { await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible(); };
  await page.goto('/'); await open();
  const runtimeProvider = providers.find((item) => String(item.command).split(/\s+/)[0] === String(runtime.default_command).split(/\s+/)[0]);
  await expect(page.getByLabel('Engineer provider', { exact: true })).toHaveAttribute('placeholder', `Inherit · ${text(runtimeProvider?.display_name) || text(runtimeProvider?.name) || 'system default'}`);
  await page.getByLabel('Agent provider', { exact: true }).fill(String(provider.name)); await page.getByLabel('Agent model', { exact: true }).fill(modelId); await page.getByLabel('Agent reasoning effort', { exact: true }).fill(effortIds[0]!);
  await page.getByLabel('Agent boot command', { exact: true }).fill('preview-only --local'); await page.getByLabel('Default directory', { exact: true }).fill('/private/tmp');
  await page.getByLabel('Agent directory', { exact: true }).fill('/private/tmp/launch-preview'); await page.getByLabel('Agent shell', { exact: true }).selectOption('fish'); await page.getByLabel('Env file', { exact: true }).fill('/private/tmp/preview.env');
  for (const kind of ['Worker', 'Engineer', 'Architect']) {
    await expect(page.getByLabel(`${kind} provider`, { exact: true })).toHaveValue(''); await expect(page.getByLabel(`${kind} model`, { exact: true })).toHaveValue('');
    await expect(page.getByLabel(`${kind} model`, { exact: true })).toHaveAttribute('placeholder', `Inherit · ${modelLabel}`);
    expect(await choices(`${kind} model`)).toEqual(modelIds); expect(await choices(`${kind} reasoning effort`)).toEqual(effortIds);
    await expect(page.getByLabel(`${kind} boot command`, { exact: true })).toHaveAttribute('placeholder', 'Inherit · preview-only --local');
  }
  await expect(page.getByLabel('Engineer directory', { exact: true })).toHaveAttribute('placeholder', 'Inherit · /private/tmp/launch-preview');
  await expect(page.getByLabel('Architect shell', { exact: true }).getByRole('option', { name: 'Inherit · fish' })).toHaveAttribute('value', '');
  await expect(page.getByLabel('Agent env file', { exact: true })).toHaveAttribute('placeholder', 'Inherit · /private/tmp/preview.env');
  const custom = page.getByLabel('Engineer model', { exact: true }); await custom.fill('custom-local-model'); await custom.evaluate((input: HTMLInputElement) => { input.dataset.retained = 'yes'; input.setSelectionRange(2, 8); });
  expect(writes).toHaveLength(0);
  const before = connections; await socket!.close({ code: 1012, reason: 'Launch settings reconnect' }); await expect.poll(() => connections).toBeGreaterThan(before);
  await expect(custom).toBeFocused(); await expect(custom).toHaveValue('custom-local-model'); await expect(custom).toHaveAttribute('data-retained', 'yes');
  expect(await custom.evaluate((input: HTMLInputElement) => [input.selectionStart, input.selectionEnd])).toEqual([2, 8]);
  await expect(page.getByLabel('Architect model', { exact: true })).toHaveAttribute('placeholder', `Inherit · ${modelLabel}`); expect(writes).toHaveLength(0);
  await custom.evaluate((input) => input.scrollIntoView({ block: 'center' })); await page.screenshot({ path: test.info().outputPath('inherited-launch-preview.png') });
  await save();
  const saved = await command(request, { cmd: 'get_group_settings', group });
  expect(saved.settings).toMatchObject({ agent_provider: provider.name, agent_model: modelId, worker_provider: '', worker_model: '', agent_env_file: '' });
  expect(saved.engineer_settings).toMatchObject({ engineer_provider: '', engineer_model: 'custom-local-model', engineer_directory: '', engineer_shell: '' });
  expect(saved.architect_settings).toMatchObject({ architect_provider: '', architect_model: '', architect_boot_command: '' });
  expect(writes.some((entry) => entry.cmd === 'update_architect_settings')).toBe(false);
  const groupWrite = writes.find((entry) => entry.cmd === 'update_group_settings')!; expect(groupWrite.settings).not.toHaveProperty('worker_provider');
  await custom.fill(''); await save(); await page.reload(); await open();
  await expect(custom).toHaveValue(''); await expect(custom).toHaveAttribute('placeholder', `Inherit · ${modelLabel}`); expect(await choices('Engineer model')).toEqual(modelIds);
  await page.getByLabel('Engineer provider', { exact: true }).fill('unknown-local-provider'); await expect(custom).toHaveValue(''); expect(await choices('Engineer model')).toEqual([]);
});
