import { expect, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
import { test } from './relay-profile-fixture';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
test('resolved Relay fields preserve inheritance, focused drafts and sparse writes under real updates', async ({ page, request }) => {
  test.setTimeout(90_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).toMatch(/^qa-relay-config-/);
  const initial = await command(request, { cmd: 'get_global_settings' }); const original = initial.settings as Row;
  const resolved = initial.relay_config as { config: Row; sources: Record<string, Row> };
  expect(resolved.sources.enabled).toEqual({ source: 'env', value: true });
  expect(resolved.sources.relay_url).toEqual({ source: 'ee_connector.json', value: 'wss://relay-file.invalid/ws' });
  expect(resolved.sources.credential_id).toEqual({ source: 'env', value: 'qa-env-credential' });
  expect(JSON.stringify(initial)).not.toContain('QA_INLINE_SENTINEL_NOT_A_KEY');
  const group = `Relay config ${Date.now()}`; await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  const writes: Row[] = []; let rejectSave = false; let socket: WebSocketRoute | undefined; let connections = 0;
  await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections++; });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd === 'update_global_settings') { writes.push(data); if (rejectSave) { rejectSave = false; await route.fulfill({ status: 200, json: { ok: false, error: 'QA Relay save refused' } }); return; } }
    await route.continue();
  });
  const open = async () => { await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click(); };
  const url = page.getByLabel('Relay URL', { exact: true }); const daemon = page.getByLabel('Daemon ID', { exact: true }); const credential = page.getByLabel('Credential ID', { exact: true }); const enabled = page.getByLabel('Relay', { exact: true });
  const save = async () => { await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible(); };
  try {
    await page.goto('/'); await open(); await expect(enabled).toHaveValue('on'); await expect(url).toHaveValue(''); await expect(url).toHaveAttribute('placeholder', 'wss://relay-file.invalid/ws'); await expect(credential).toHaveAttribute('placeholder', 'qa-env-credential');
    await expect(page.getByLabel('Private key path', { exact: true })).toHaveValue(''); await expect(page.locator('body')).not.toContainText('QA_INLINE_SENTINEL_NOT_A_KEY');
    await page.getByLabel('Terminal scrollback', { exact: true }).fill(String(Number(original.xterm_scrollback) + 1)); await save(); expect(writes.at(-1)?.settings).toEqual({ xterm_scrollback: Number(original.xterm_scrollback) + 1 });
    await command(request, { cmd: 'update_global_settings', settings: { relay_url: 'wss://server-one.invalid/ws', relay_daemon_id: 'server-one' } });
    await expect(url).toHaveValue('wss://server-one.invalid/ws'); await expect(daemon).toHaveValue('server-one');
    await daemon.fill('local-daemon-draft'); await url.focus(); await url.evaluate((input: HTMLInputElement) => input.setSelectionRange(6, 16));
    await command(request, { cmd: 'update_global_settings', settings: { relay_url: 'wss://server-two.invalid/ws', relay_daemon_id: 'server-two', relay_credential_id: 'remote-credential' } });
    await expect(credential).toHaveValue('remote-credential'); await expect(url).toHaveValue('wss://server-one.invalid/ws'); await expect(url).toBeFocused(); await expect(daemon).toHaveValue('local-daemon-draft');
    expect(await url.evaluate((input: HTMLInputElement) => [input.selectionStart, input.selectionEnd])).toEqual([6, 16]);
    const before = connections; await socket!.close({ code: 1012, reason: 'Relay config reconnect' }); await expect.poll(() => connections).toBeGreaterThan(before);
    await expect(url).toBeFocused(); await expect(url).toHaveValue('wss://server-one.invalid/ws'); await expect(daemon).toHaveValue('local-daemon-draft'); expect(writes).toHaveLength(1);
    await url.press('Tab'); await expect(url).toHaveValue('wss://server-two.invalid/ws');
    await save(); expect(writes.at(-1)?.settings).toEqual({ relay_daemon_id: 'local-daemon-draft' });
    await url.fill(''); await credential.fill(''); await save(); expect(writes.at(-1)?.settings).toEqual({ relay_url: '', relay_credential_id: '' });
    await expect(url).toHaveValue(''); await expect(url).toHaveAttribute('placeholder', 'wss://relay-file.invalid/ws'); await expect(credential).toHaveAttribute('placeholder', 'qa-env-credential');
    await enabled.selectOption('off'); await save(); expect(writes.at(-1)?.settings).toEqual({ relay_enabled: false }); await expect(enabled).toHaveValue('on');
    await expect(page.getByText('Relay is enabled by the environment.', { exact: false })).toBeVisible();
    await url.fill('wss://retry-local.invalid/ws'); rejectSave = true; await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByRole('alert')).toContainText('QA Relay save refused'); await expect(url).toHaveValue('wss://retry-local.invalid/ws');
    await save(); expect(((await command(request, { cmd: 'get_global_settings' })).settings as Row).relay_url).toBe('wss://retry-local.invalid/ws');
    await url.fill(''); await save(); await page.reload(); await open(); await expect(url).toHaveValue(''); await expect(url).toHaveAttribute('placeholder', 'wss://relay-file.invalid/ws'); await expect(enabled).toHaveValue('on');
    await url.evaluate((input) => input.scrollIntoView({ block: 'center' })); await page.screenshot({ path: test.info().outputPath('resolved-relay-config.png') });
  } finally {
    await command(request, { cmd: 'update_global_settings', settings: Object.fromEntries(['xterm_scrollback', 'relay_enabled', 'relay_url', 'relay_daemon_id', 'relay_credential_id', 'relay_private_key_path'].map((key) => [key, original[key]])) });
  }
});
