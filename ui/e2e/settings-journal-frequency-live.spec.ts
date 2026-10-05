import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}
test('Architect journal frequency offers presets and preserves validated custom drafts through save and reconnect', async ({ page, request }) => {
  test.setTimeout(60_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Journal frequency ${Date.now()}`;
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'update_architect_settings', group, settings: { architect_journal_checkpoint_frequency: 'every_7_minutes', architect_custom_instructions: 'Preserve these instructions' } });
  let socket: WebSocketRoute | undefined; let connections = 0; let refuse = false; const writes: Row[] = [];
  await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections++; });
  await page.route('**/api/cmd', async (route) => { const data = route.request().postDataJSON() as Row; if (data.cmd === 'update_architect_settings') { writes.push(data); if (refuse) { refuse = false; await route.fulfill({ json: { ok: false, error: 'QA journal frequency refused' } }); return; } } await route.continue(); });
  const open = async () => { await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click(); await page.getByText('Architect behavior defaults', { exact: true }).click(); };
  const picker = page.getByRole('combobox', { name: 'Architect journal checkpoint frequency', exact: true }); const custom = page.getByRole('textbox', { name: 'Custom journal checkpoint frequency', exact: true });
  const save = async () => { await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible(); };
  const persisted = async (value: string) => expect((await command(request, { cmd: 'get_group_settings', group })).architect_settings).toMatchObject({ architect_journal_checkpoint_frequency: value, architect_custom_instructions: 'Preserve these instructions' });
  await page.goto('/'); await open(); await expect(custom).toHaveValue('every_7_minutes'); await expect(picker.getByRole('option', { name: 'Custom · Every 7 minutes', exact: true })).toHaveCount(1);
  for (const value of ['every_5_actions', 'every_30_minutes', 'manual_only']) {
    await picker.selectOption(value); await save(); await persisted(value); await page.reload(); await open(); await expect(picker).toHaveValue(value);
  }
  expect(writes).toHaveLength(3); for (const write of writes) expect(Object.keys(write.settings as Row)).toEqual(['architect_journal_checkpoint_frequency']);
  await picker.selectOption('__custom'); await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByRole('alert')).toContainText('Correct the highlighted setting'); await expect(custom).toBeFocused(); expect(writes).toHaveLength(3);
  await custom.fill('every_0_actions'); await page.getByRole('button', { name: 'Save changes', exact: true }).click(); expect(writes).toHaveLength(3);
  await custom.fill('every_35_actions'); await custom.focus(); await custom.evaluate((input) => input.setAttribute('data-retained', 'yes')); const before = connections; await socket!.close({ code: 1012, reason: 'Journal custom draft reconnect' }); await expect.poll(() => connections).toBeGreaterThan(before);
  await expect(custom).toHaveValue('every_35_actions'); await expect(custom).toBeFocused(); await expect(custom).toHaveAttribute('data-retained', 'yes'); expect(writes).toHaveLength(3);
  refuse = true; await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByRole('alert')).toContainText('QA journal frequency refused'); await expect(custom).toHaveValue('every_35_actions'); await save(); await persisted('every_35_actions');
  await page.reload(); await open(); await expect(custom).toHaveValue('every_35_actions'); await custom.scrollIntoViewIfNeeded(); await page.screenshot({ path: test.info().outputPath('journal-frequency.png') });
  await page.getByRole('button', { name: 'Reset Architect journal checkpoint frequency', exact: true }).click(); await expect(picker).toHaveValue('every_10_actions'); await expect(custom).toHaveCount(0); await save(); await persisted('every_10_actions');
});
