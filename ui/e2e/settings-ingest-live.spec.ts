import { expect, test, type APIRequestContext } from '@playwright/test';

type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const response = await request.post('/api/cmd', { data });
  expect(response.ok()).toBe(true);
  const result = await response.json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}

test('capture choices control future persisted MCP values and retention limits reject invalid drafts', async ({ page, request }) => {
  test.setTimeout(60_000);
  const runtime = await (await request.get('/api/runtime')).json() as { data: { runtime: { port: number; profile: string } } };
  expect(runtime.data.runtime.port).not.toBe(18932); expect(runtime.data.runtime.profile).not.toBe('default');
  const baseline = (await command(request, { cmd: 'get_global_settings' })).settings as Row;
  const keys = ['mcp_call_log_args_capture', 'mcp_call_log_full_capture_tools', 'event_ingest_max_rows', 'event_ingest_max_days'];
  const prefix = `mcp__parity_${Date.now()}__`;
  const calls = async () => (await command(request, { cmd: 'mcp_calls', tool_name_pattern: `${prefix}%`, limit: 100 })).calls as Row[];
  const ingest = async (name: string) => {
    const response = await request.post('/events', { data: {
      event_id: `${prefix}${name}`, hook_event_name: 'PostToolUse', tool_name: `${prefix}${name}`,
      tool_input: { token: 'fixture-input-value', task_id: 'fixture-task' },
      tool_output: { value: 'fixture-output-value' }, tool_response: { value: 'fixture-response-value' },
    } });
    expect(response.status()).toBe(200);
    await expect.poll(async () => (await calls()).some((row) => row.tool_name === `${prefix}${name}`)).toBe(true);
    return (await calls()).find((row) => row.tool_name === `${prefix}${name}`)!;
  };
  try {
    await command(request, { cmd: 'update_global_settings', settings: { mcp_call_log_args_capture: 'metadata', mcp_call_log_full_capture_tools: [], event_ingest_max_rows: 100000, event_ingest_max_days: 14 } });
    await page.goto('/'); await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click();
    await page.getByText('Global defaults', { exact: true }).click();
    const capture = page.getByRole('combobox', { name: 'MCP call log args capture', exact: true });
    const allowlist = page.getByRole('textbox', { name: 'MCP call log full capture tools', exact: true });
    const rows = page.getByRole('spinbutton', { name: 'Event ingest max rows', exact: true });
    const days = page.getByRole('spinbutton', { name: 'Event ingest max days', exact: true });
    const save = page.getByRole('button', { name: 'Save changes', exact: true });
    const saveChanges = async () => {
      const mode = await capture.inputValue();
      await save.click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible();
      expect((await command(request, { cmd: 'get_global_settings' })).settings).toMatchObject({ mcp_call_log_args_capture: mode });
      await page.reload(); await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click(); await page.getByText('Global defaults', { exact: true }).click();
      await expect(capture).toHaveValue(mode);
    };
    const writes: Row[] = [];
    await page.route('**/api/cmd', async (route) => { const data = route.request().postDataJSON() as Row; if (data.cmd === 'update_global_settings') writes.push(data); await route.continue(); });
    for (const [input, invalid, valid] of [[rows, ['0', '-1', '1.5', ''], '100000'], [days, ['-1', '0.5', ''], '0']] as const) {
      for (const value of invalid) {
        await input.fill(value); await save.click();
        await expect(page.getByRole('alert')).toHaveText('Correct the highlighted setting before saving.');
        await expect(input).toBeFocused(); expect(writes).toHaveLength(0);
      }
      await input.fill(valid);
    }
    await saveChanges();
    expect((await command(request, { cmd: 'get_global_settings' })).settings).toMatchObject({ event_ingest_max_rows: 100000, event_ingest_max_days: 0 });
    expect(await capture.locator('option').allTextContents()).toEqual(['Off', 'Metadata', 'Full']);
    const metadata = await ingest('metadata');
    expect(metadata.args).toMatchObject({ redacted: true, arg_keys: ['task_id', 'token'] });
    expect(JSON.stringify(metadata)).not.toContain('fixture-input-value');
    expect(metadata.raw).toMatchObject({ tool_output: { redacted: true }, tool_response: { redacted: true } });
    await capture.selectOption('full'); await saveChanges();
    const full = await ingest('full');
    expect(full.args).toEqual({ token: 'fixture-input-value', task_id: 'fixture-task' });
    expect(full.raw).toMatchObject({ tool_output: { value: 'fixture-output-value' }, tool_response: { value: 'fixture-response-value' } });
    await capture.selectOption('metadata'); await allowlist.fill(`${prefix}allowed*\n^${prefix}regex$`); await saveChanges();
    expect((await ingest('allowed-tool')).args).toEqual(full.args);
    expect((await ingest('regex')).args).toEqual(full.args);
    expect((await ingest('unmatched')).args).toMatchObject({ redacted: true });
    await capture.selectOption('off'); await saveChanges();
    const off = await ingest('allowed-off');
    expect(off.args).toBeNull();
    for (const key of ['tool_input', 'tool_output', 'tool_response']) expect(off.raw).not.toHaveProperty(key);
    // Changing modes never rewrites earlier capture decisions.
    const history = await calls();
    expect(history.find((row) => row.tool_name === `${prefix}full`)?.args).toEqual(full.args);
    expect(history.find((row) => row.tool_name === `${prefix}metadata`)?.args).toEqual(metadata.args);
    await rows.fill('1'); await saveChanges();
    await ingest('retained-last');
    await expect.poll(async () => (await calls()).map((row) => row.tool_name)).toEqual([`${prefix}retained-last`]);
    await page.reload(); await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click(); await page.getByText('Global defaults', { exact: true }).click();
    await expect(capture).toHaveValue('off'); await expect(rows).toHaveValue('1'); await expect(days).toHaveValue('0');
    await expect(allowlist).toHaveValue(`${prefix}allowed*\n^${prefix}regex$`);
    await capture.scrollIntoViewIfNeeded(); await page.screenshot({ path: test.info().outputPath('settings-ingest-controls.png') });
  } finally {
    await command(request, { cmd: 'update_global_settings', settings: Object.fromEntries(keys.map((key) => [key, baseline[key]])) });
  }
});
