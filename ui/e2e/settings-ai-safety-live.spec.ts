import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const body = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(body.ok, body.error).toBe(true); return body.data;
}
test('AI secret intents persist with AI disabled and blank keeps the saved key', async ({ page, request }) => {
  const runtime = await (await request.get('/api/runtime')).json() as { data: { runtime: { port: number; profile: string } } }; expect(runtime.data.runtime.port).not.toBe(18932); expect(runtime.data.runtime.profile).not.toBe('default');
  const group = `AI secrets ${Date.now()}`; await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'update_ai_settings', settings: { ai_enabled: false }, secrets: { anthropic: 'fixture-original-1234', openai_compatible: 'fixture-original-5678' } });
  const writes: Row[] = []; await page.route('**/api/cmd', async (route) => { const data = route.request().postDataJSON() as Row; if (data.cmd === 'update_ai_settings') writes.push(data); await route.continue(); });
  await page.goto('/'); await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click();
  const key = page.getByLabel('Anthropic key', { exact: true }); await expect(key).toHaveValue(''); await expect(key).toHaveAccessibleDescription(/Configured.*1234/);
  await key.fill('fixture-discarded-draft'); await page.getByRole('button', { name: 'Clear Anthropic key', exact: true }).click(); await expect(key).toHaveValue(''); await expect(key).toHaveAccessibleDescription('Will clear on save.');
  await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible(); expect(writes.at(-1)).toMatchObject({ secrets: {}, clear_secrets: ['anthropic'] });
  let settings = (await command(request, { cmd: 'get_ai_settings' })).settings as Row; expect(((settings.generation as Row).anthropic as Row).key).toMatchObject({ configured: false }); expect(JSON.stringify(settings)).not.toContain('fixture-original');
  await page.getByRole('button', { name: 'Clear OpenAI-compatible key', exact: true }).click(); await page.getByLabel('OpenAI-compatible key', { exact: true }).fill('fixture-replacement-9012');
  await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible(); expect(writes.at(-1)).toMatchObject({ secrets: { openai_compatible: 'fixture-replacement-9012' }, clear_secrets: [] });
  settings = (await command(request, { cmd: 'get_ai_settings' })).settings as Row; expect(((settings.generation as Row).openai_compatible as Row).key).toMatchObject({ configured: true, last4: '9012' }); expect(settings.enabled).toBe(false);
  await page.reload(); await expect(page.getByLabel('OpenAI-compatible key', { exact: true })).toHaveValue(''); await expect(page.getByLabel('OpenAI-compatible key', { exact: true })).toHaveAccessibleDescription(/9012/);
  await page.getByLabel('Default directory', { exact: true }).fill('/private/tmp/ai-key-unrelated'); const before = writes.length; await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible(); expect(writes).toHaveLength(before);
  await command(request, { cmd: 'update_ai_settings', clear_secrets: ['anthropic', 'openai_compatible'] });
});
test('AI rebuild confirmation owns the reviewed draft, redacts refusals and survives reconnect without replay', async ({ page, request }) => {
  const runtime = await (await request.get('/api/runtime')).json() as { data: { runtime: { port: number; profile: string } } }; expect(runtime.data.runtime.port).not.toBe(18932); expect(runtime.data.runtime.profile).not.toBe('default');
  const group = `AI confirmation ${Date.now()}`; await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  const writes: Row[] = []; let socket: WebSocketRoute | undefined; let connections = 0; let attempt = 0; let release!: () => void; const hold = new Promise<void>((resolve) => { release = resolve; });
  let ai: Row = { enabled: false, embeddings: { model_id: 'fixture-old', runtime: 'sentence_transformers' }, index: { corpus: { tasks: true }, counts: { chunks: 12 } }, generation: { anthropic: { key: { configured: true, last4: '1234' } } } };
  await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections += 1; });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd === 'get_ai_settings') { await route.fulfill({ json: { ok: true, data: { type: 'ai_settings', settings: ai } } }); return; }
    if (/^(update_|engineer_update_)/.test(String(data.cmd))) writes.push(data);
    if (data.cmd === 'update_ai_settings') {
      attempt += 1;
      if (attempt === 1) { await hold; await route.fulfill({ json: { ok: false, error: 'Fixture refused fixture-pending-secret token=fixture-extra-secret' } }); }
      else { ai = { ...ai, embeddings: { ...(ai.embeddings as Row), model_id: 'fixture-newer' } }; await route.fulfill({ json: { ok: true, data: { type: 'ai_settings', settings: ai } } }); }
      return;
    }
    await route.continue();
  });
  await page.goto('/'); await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click();
  const directory = page.getByLabel('Default directory', { exact: true }); await directory.fill('/private/tmp/ai-confirm-draft'); const model = page.getByLabel('Embedding model', { exact: true }); await model.fill('fixture-new');
  const key = page.getByLabel('Anthropic key', { exact: true }); await key.fill('fixture-pending-secret'); await page.getByRole('button', { name: 'Save changes', exact: true }).click();
  const confirmation = page.getByRole('group', { name: 'Confirm embedding rebuild' }); await expect(confirmation).toContainText('12 entries'); expect(writes).toHaveLength(0);
  await confirmation.getByRole('button', { name: 'Cancel rebuild', exact: true }).click(); await expect(confirmation).toHaveCount(0); await expect(key).toHaveValue('fixture-pending-secret'); expect(writes).toHaveLength(0);
  await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await model.fill('fixture-newer'); await expect(confirmation).toHaveCount(0); await page.getByRole('button', { name: 'Save changes', exact: true }).click();
  await model.focus(); await model.evaluate((element: HTMLInputElement) => { element.setSelectionRange(2, 6); element.dataset.aiAnchor = 'retained'; }); const previous = connections; await socket!.close({ code: 1012, reason: 'AI draft confirmation continuity' }); await expect.poll(() => connections).toBeGreaterThan(previous);
  await expect(confirmation).toBeVisible(); await expect(model).toBeFocused(); await expect(model).toHaveAttribute('data-ai-anchor', 'retained'); expect(await model.evaluate((element: HTMLInputElement) => [element.selectionStart, element.selectionEnd])).toEqual([2, 6]); expect(writes).toHaveLength(0);
  await confirmation.scrollIntoViewIfNeeded(); await page.screenshot({ path: test.info().outputPath('ai-confirmation.png') });
  await confirmation.getByRole('button', { name: 'Confirm settings and rebuild', exact: true }).click(); await expect.poll(() => attempt).toBe(1); await expect(key).toBeDisabled(); await expect(page.getByRole('button', { name: 'Save changes', exact: true })).toBeDisabled();
  const pendingConnection = connections; await socket!.close({ code: 1012, reason: 'AI pending acknowledgement' }); await expect.poll(() => connections).toBeGreaterThan(pendingConnection); expect(attempt).toBe(1);
  release(); const alert = page.getByRole('alert'); await expect(alert).toContainText('Fixture refused [redacted] token: [redacted]'); await expect(alert).not.toContainText('fixture-pending-secret'); await expect(key).toHaveValue('fixture-pending-secret'); await expect(directory).toHaveValue('/private/tmp/ai-confirm-draft');
  await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await confirmation.getByRole('button', { name: 'Confirm settings and rebuild', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible(); await expect(key).toHaveValue(''); await expect(confirmation).toHaveCount(0);
  expect(writes.filter((data) => data.cmd === 'update_group_settings')).toHaveLength(1); expect(writes.filter((data) => data.cmd === 'update_ai_settings')).toHaveLength(2); expect(writes.at(-1)).toMatchObject({ confirm_embedding_rebuild: true, settings: { ai_embedding_model: 'fixture-newer' } });
  expect(await page.evaluate(() => JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage)]))).not.toContain('fixture-pending-secret');
  await page.setViewportSize({ width: 390, height: 844 }); const section = page.getByRole('heading', { name: 'AI subsystem', exact: true }).locator('..'); await key.scrollIntoViewIfNeeded(); expect(await section.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true); await page.screenshot({ path: test.info().outputPath('ai-keys-narrow.png') });
  // Embedding-changing writes are fixture-owned; no models download or provider executes.
});
