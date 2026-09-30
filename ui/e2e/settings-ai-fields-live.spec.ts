import { expect, test, type APIRequestContext, type Page, type WebSocketRoute } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
import { isAbsolute } from 'node:path';
type Row = Record<string, unknown>;
const corpus = ['architect_journals', 'engineer_journals', 'decisions', 'tasks', 'engineer_peer_threads'];
const textFields = [
  ['ai_anthropic_model', 'Anthropic model', '  qa-anthropic-model  '],
  ['ai_openai_compatible_base_url', 'OpenAI-compatible URL', '  http://127.0.0.1:9/v1  '],
  ['ai_openai_compatible_model', 'OpenAI-compatible model', '  qa-local-model  '],
  ['ai_embedding_model', 'Embedding model', '  /private/tmp/qa-disabled-embedding-model  '],
] as const;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}
async function setup(request: APIRequestContext) {
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const initial = await command(request, { cmd: 'get_global_settings' });
  const original = Object.fromEntries(Object.entries(initial.settings as Row).filter(([key]) => key.startsWith('ai_')));
  expect(original.ai_enabled).toBe(false);
  const group = `AI field acceptance ${Date.now()}`;
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'settings' } });
  return { runtime, original, group, defaults: initial.defaults as Row };
}
async function save(page: Page) { await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible(); }

test('AI fields persist actual provider, model, corpus and summary choices with exact draft recovery', async ({ page, request }) => {
  test.setTimeout(90_000);
  const { original, group, defaults } = await setup(request);
  let socket: WebSocketRoute | undefined; let connections = 0; let updates = 0; let refuse = true; const writes: Row[] = []; const evidence: Row[] = [];
  await page.routeWebSocket(/\/ws\?/, (client) => {
    socket = client; connections++; const server = client.connectToServer();
    server.onMessage((message) => { const frame = JSON.parse(String(message)) as Row; if (frame.type === 'delta' && Array.isArray(frame.ops)) updates += (frame.ops as Row[]).filter((op) => op.op === 'group_settings_update' && op.name === group).length; client.send(message); });
  });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd === 'update_ai_settings') { writes.push(data); if (refuse) { refuse = false; await route.fulfill({ json: { ok: false, error: 'QA AI field save refused' } }); return; } }
    await route.continue();
  });
  const section = page.getByRole('heading', { name: 'AI subsystem', exact: true }).locator('..');
  try {
    await page.goto('/');
    const runtime = section.getByRole('combobox', { name: 'Embedding runtime', exact: true });
    await expect(runtime).toHaveValue('sentence_transformers');
    expect(await runtime.locator('option').evaluateAll((options) => options.map((option) => (option as HTMLOptionElement).value))).toEqual(['sentence_transformers']);
    expect(await section.getByRole('combobox', { name: 'Generation provider', exact: true }).locator('option').evaluateAll((options) => options.map((option) => (option as HTMLOptionElement).value))).toEqual(['anthropic', 'openai_compatible']);
    const invalid = await (await request.post('/api/cmd', { data: { cmd: 'update_ai_settings', settings: { ai_embedding_runtime: 'fastembed' } } })).json() as { ok: boolean; error: string };
    expect(invalid.ok).toBe(false); expect(invalid.error).toContain('sentence_transformers');
    for (const [key, label] of textFields) await expect(section.getByRole('textbox', { name: label, exact: true })).toHaveValue(String(original[key]));
    for (const [index, [, label, value]] of textFields.entries()) {
      const input = section.getByRole('textbox', { name: label, exact: true }); await input.fill(value); await input.evaluate((node: HTMLInputElement) => { node.setSelectionRange(2, 8); node.setAttribute('data-retained', 'yes'); });
      const priorUpdates = updates; await command(request, { cmd: 'update_group_settings', group, settings: { max_agents: index + 1 } }); await expect.poll(() => updates).toBeGreaterThan(priorUpdates);
      const before = connections; await socket!.close({ code: 1012, reason: 'AI text field reconnect' }); await expect.poll(() => connections).toBeGreaterThan(before);
      await expect(input).toHaveValue(value); await expect(input).toBeFocused(); await expect(input).toHaveAttribute('data-retained', 'yes'); expect(await input.evaluate((node: HTMLInputElement) => [node.selectionStart, node.selectionEnd])).toEqual([2, 8]);
    }
    await section.getByRole('combobox', { name: 'Generation provider', exact: true }).selectOption('openai_compatible');
    await section.getByRole('combobox', { name: 'Boot summaries', exact: true }).selectOption('off');
    await section.getByRole('spinbutton', { name: 'Boot summary minimum interval', exact: true }).fill('17');
    await section.getByRole('spinbutton', { name: 'Boot summary hourly limit', exact: true }).fill('9');
    for (const key of corpus) await section.getByRole('checkbox', { name: key.replaceAll('_', ' '), exact: true }).uncheck();
    expect(writes).toHaveLength(0); await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByRole('alert')).toContainText('QA AI field save refused');
    for (const [, label, value] of textFields) await expect(section.getByRole('textbox', { name: label, exact: true })).toHaveValue(value);
    await save(page); expect(writes).toHaveLength(2); expect(writes[1]).toEqual(writes[0]); expect(writes[1]).not.toHaveProperty('confirm_embedding_rebuild');
    const expected: Row = { ...Object.fromEntries(textFields.map(([key, , value]) => [key, value.trim()])), ai_generation_provider: 'openai_compatible', ai_boot_summary_enabled: false, ai_boot_summary_min_interval_seconds: 17, ai_boot_summary_max_refreshes_per_hour: 9, ai_index_corpus: Object.fromEntries(corpus.map((key) => [key, false])) };
    const global = (await command(request, { cmd: 'get_global_settings' })).settings as Row; expect(global).toMatchObject({ ...expected, ai_enabled: false, ai_embedding_runtime: 'sentence_transformers' });
    expect(Object.keys(writes[1]!.settings as Row).sort()).toEqual(Object.keys(expected).sort());
    const ai = (await command(request, { cmd: 'get_ai_settings' })).settings as Row; expect(ai.enabled).toBe(false); expect((ai.index as Row).status).toBe('disabled'); expect((ai.embeddings as Row).desired_model_id).toBe(expected.ai_embedding_model); evidence.push({ expected, global, ai });
    await page.reload();
    for (const [key, label] of textFields) await expect(section.getByRole('textbox', { name: label, exact: true })).toHaveValue(String(expected[key]));
    await expect(section.getByRole('combobox', { name: 'Generation provider', exact: true })).toHaveValue('openai_compatible'); await expect(section.getByRole('combobox', { name: 'Boot summaries', exact: true })).toHaveValue('off');
    await expect(section.getByRole('spinbutton', { name: 'Boot summary minimum interval', exact: true })).toHaveValue('17'); await expect(section.getByRole('spinbutton', { name: 'Boot summary hourly limit', exact: true })).toHaveValue('9');
    for (const key of corpus) await expect(section.getByRole('checkbox', { name: key.replaceAll('_', ' '), exact: true })).not.toBeChecked();
    // Explicit empty text restores embedding fallback and clears generation overrides.
    for (const [, label] of textFields) await section.getByRole('textbox', { name: label, exact: true }).fill('');
    await section.getByRole('combobox', { name: 'Generation provider', exact: true }).selectOption('anthropic'); await section.getByRole('combobox', { name: 'Boot summaries', exact: true }).selectOption('on');
    for (const key of corpus) await section.getByRole('checkbox', { name: key.replaceAll('_', ' '), exact: true }).check();
    await section.getByRole('spinbutton', { name: 'Boot summary minimum interval', exact: true }).fill('0'); await section.getByRole('spinbutton', { name: 'Boot summary hourly limit', exact: true }).fill('0');
    await save(page); const cleared = (await command(request, { cmd: 'get_global_settings' })).settings as Row;
    const clearedExpected = { ai_anthropic_model: '', ai_openai_compatible_base_url: '', ai_openai_compatible_model: '', ai_embedding_model: defaults.ai_embedding_model, ai_generation_provider: 'anthropic', ai_boot_summary_enabled: true, ai_boot_summary_min_interval_seconds: 0, ai_boot_summary_max_refreshes_per_hour: 0, ai_index_corpus: Object.fromEntries(corpus.map((key) => [key, true])) };
    expect(cleared).toMatchObject(clearedExpected); evidence.push({ expected: clearedExpected, global: cleared });
    await page.reload(); for (const [key, label] of textFields) await expect(section.getByRole('textbox', { name: label, exact: true })).toHaveValue(String(cleared[key]));
    await expect(section.getByRole('combobox', { name: 'Generation provider', exact: true })).toHaveValue('anthropic'); await expect(section.getByRole('combobox', { name: 'Boot summaries', exact: true })).toHaveValue('on');
    for (const label of ['Boot summary minimum interval', 'Boot summary hourly limit']) await expect(section.getByRole('spinbutton', { name: label, exact: true })).toHaveValue('0');
    for (const key of corpus) await expect(section.getByRole('checkbox', { name: key.replaceAll('_', ' '), exact: true })).toBeChecked();
    await runtime.scrollIntoViewIfNeeded(); await page.screenshot({ path: test.info().outputPath('ai-supported-settings.png') });
    const path = test.info().outputPath('ai-field-roundtrips.json'); await writeFile(path, JSON.stringify(evidence, null, 2)); await test.info().attach('ai-field-roundtrips', { path, contentType: 'application/json' });
  } finally { await command(request, { cmd: 'update_ai_settings', settings: original }); }
});

test('AI master toggle persists through reload and handles an unavailable local model without provider calls', async ({ page, request }) => {
  test.setTimeout(60_000);
  test.skip(!process.env.TORQUE_AI_SETTINGS_LOCAL_MODEL, 'Requires a QA-owned unavailable local model and offline model loading');
  const model = process.env.TORQUE_AI_SETTINGS_LOCAL_MODEL!; expect(isAbsolute(model)).toBe(true);
  const { runtime, original } = await setup(request); expect(model.startsWith(`${String(runtime.data_dir)}/`)).toBe(true);
  const initial = (await command(request, { cmd: 'get_ai_settings' })).settings as Row;
  const jobs: Row[] = [];
  await page.routeWebSocket(/\/ws\?/, (client) => {
    const server = client.connectToServer();
    server.onMessage((message) => {
      const frame = JSON.parse(String(message)) as Row;
      if (frame.type === 'delta' && Array.isArray(frame.ops)) for (const op of frame.ops as Row[]) {
        if (op.op === 'ai_index_status_update') { const job = (op.index as Row)?.current_job; if (job) jobs.push(job as Row); }
      }
      client.send(message);
    });
  });
  try {
    await command(request, { cmd: 'update_ai_settings', settings: { ai_enabled: false, ai_boot_summary_enabled: false, ai_embedding_model: model, ai_index_corpus: Object.fromEntries(corpus.map((key) => [key, false])) } });
    await page.goto('/'); const enabled = page.getByRole('combobox', { name: 'AI', exact: true }); await expect(enabled).toHaveValue('off');
    await enabled.selectOption('on'); await save(page); expect((await command(request, { cmd: 'get_global_settings' })).settings).toMatchObject({ ai_enabled: true });
    // Observe a new job through its terminal delta. The read endpoint exposes
    // only queued/running jobs, and an old last_error is not fresh evidence.
    await expect.poll(() => jobs.some((job) => job.status === 'error' && jobs.some((prior) => prior.id === job.id && prior.status === 'running')), { timeout: 30_000 }).toBe(true);
    await page.reload(); await expect(enabled).toHaveValue('on');
    await expect.poll(async () => {
      const ai = (await command(request, { cmd: 'get_ai_settings' })).settings as Row; return (ai.index as Row).status;
    }, { timeout: 30_000 }).toBe('error');
    const active = (await command(request, { cmd: 'get_ai_settings' })).settings as Row; expect(active.enabled).toBe(true); expect((active.index as Row).last_error).toBeTruthy(); expect((active.metering as Row).calls_24h).toBe((initial.metering as Row).calls_24h);
    await expect(page.getByRole('region', { name: 'Vector index', exact: true })).toContainText(String((active.index as Row).last_error));
    await page.getByRole('region', { name: 'Vector index', exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: test.info().outputPath('ai-local-model-error.png') });
    await enabled.selectOption('off'); await save(page); await page.reload(); await expect(enabled).toHaveValue('off');
    const disabled = (await command(request, { cmd: 'get_ai_settings' })).settings as Row; expect(disabled.enabled).toBe(false); expect((disabled.index as Row).status).toBe('disabled');
    const path = test.info().outputPath('ai-enabled-local-failure.json'); await writeFile(path, JSON.stringify({ jobs, active, disabled }, null, 2)); await test.info().attach('ai-enabled-local-failure', { path, contentType: 'application/json' });
  } finally { await command(request, { cmd: 'update_ai_settings', settings: { ...original, ai_enabled: false } }); }
});
