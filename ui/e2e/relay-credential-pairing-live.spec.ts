import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
test('Relay pairing retains drafts through confirmation, pending reconnect, recovery and acknowledged success', async ({ page, request }) => {
  const runtime = await (await request.get('/api/runtime')).json() as { data: { runtime: { port: number; profile: string } } };
  expect(runtime.data.runtime.port).not.toBe(18932); expect(runtime.data.runtime.profile).not.toBe('default');
  const group = `Credential pairing ${Date.now()}`; await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  let credentialId = 'existing-fixture-credential'; const pairings: Row[] = []; let reads = 0; let socket: WebSocketRoute | undefined; let connections = 0;
  let release!: () => void; const held = new Promise<void>((resolve) => { release = resolve; });
  const resolvedConfig = () => ({ config: { enabled: false, relay_url: 'https://relay.invalid', daemon_id: 'fixture-daemon', credential_id: credentialId, private_key_path: '/private/tmp/fixture-key.pem' }, sources: { relay_url: { source: 'env', value: 'https://relay.invalid' }, daemon_id: { source: 'env', value: 'fixture-daemon' }, credential_id: { source: 'settings', value: credentialId }, private_key_path: { source: 'settings', value: '/private/tmp/fixture-key.pem' } } });
  await page.routeWebSocket(/\/ws\?/, (connection) => {
    const server = connection.connectToServer(); socket = connection; connections += 1;
    // Both transports represent the same fixture configuration, including resync.
    server.onMessage((message) => {
      const frame = JSON.parse(String(message)) as Row;
      if (frame.type === 'state') frame.relay_config = resolvedConfig();
      if (frame.type === 'delta' && Array.isArray(frame.ops)) frame.ops = (frame.ops as Row[]).map((op) => op.op === 'relay_config' ? { ...op, ...resolvedConfig() } : op);
      connection.send(JSON.stringify(frame));
    });
  });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd === 'get_global_settings') {
      reads += 1; const response = await route.fetch(); const body = await response.json() as { data: Row }; body.data.relay_config = resolvedConfig(); await route.fulfill({ response, json: body }); return;
    }
    if (data.cmd === 'generate_daemon_credential') {
      pairings.push(data);
      if (pairings.length === 1) { await held; await route.fulfill({ json: { ok: true, data: { type: 'daemon_credential', ok: false, error: 'settings_write_failed', credential_id: 'recoverable-fixture-credential', private_key_path: '/private/tmp/recover-fixture.pem', message: 'Fixture Relay accepted the credential but local persistence failed.', detail: 'Fixture disk unavailable' } } }); }
      else { credentialId = 'new-fixture-credential'; await route.fulfill({ json: { ok: true, data: { type: 'daemon_credential', ok: true, daemon_id: 'fixture-daemon', credential_id: credentialId, private_key_path: '/private/tmp/fixture-key.pem', owner_user_id: 'fixture-owner', provenance: { private_key_path: 'local_keygen' }, relay_config: resolvedConfig() } } }); }
      return;
    }
    await route.continue();
  });
  await page.goto('/'); await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click();
  const directory = page.getByRole('textbox', { name: 'Default directory', exact: true }); await directory.fill('/private/tmp/retained-pairing-draft');
  const region = page.getByRole('region', { name: 'Daemon credential pairing' }); const token = region.getByLabel('One-time pairing token');
  await expect(region.getByRole('button', { name: 'Pair daemon credential', exact: true })).toBeDisabled(); await token.fill('fixture-transient-pairing-token');
  await token.focus(); await token.evaluate((element: HTMLInputElement) => { element.setSelectionRange(3, 8); element.dataset.pairingAnchor = 'original'; });
  let previous = connections; await socket!.close({ code: 1012, reason: 'Pairing token continuity' }); await expect.poll(() => connections).toBeGreaterThan(previous);
  await expect(token).toHaveValue('fixture-transient-pairing-token'); await expect(token).toBeFocused(); await expect(token).toHaveAttribute('data-pairing-anchor', 'original'); expect(await token.evaluate((element: HTMLInputElement) => [element.selectionStart, element.selectionEnd])).toEqual([3, 8]);
  await region.getByRole('button', { name: 'Pair daemon credential', exact: true }).click(); await expect(region.getByRole('group')).toContainText('existing-fixture-credential'); expect(pairings).toEqual([]);
  await region.getByRole('button', { name: 'Cancel replacement', exact: true }).click(); expect(pairings).toEqual([]);
  await region.getByRole('button', { name: 'Pair daemon credential', exact: true }).click(); await region.getByRole('button', { name: 'Generate replacement credential', exact: true }).click(); await expect.poll(() => pairings.length).toBe(1);
  await expect(token).toBeDisabled(); await expect(page.getByRole('button', { name: 'Save changes', exact: true })).toBeDisabled(); const beforeReads = reads;
  previous = connections; await socket!.close({ code: 1012, reason: 'Pairing pending continuity' }); await expect.poll(() => connections).toBeGreaterThan(previous); expect(pairings).toHaveLength(1); expect(reads).toBe(beforeReads);
  await page.getByRole('button', { name: 'Mission Control', exact: true }).click(); const dialog = page.getByRole('dialog', { name: 'Settings save in progress' }); await expect(dialog).toBeVisible(); await dialog.getByRole('button', { name: 'Keep editing', exact: true }).click();
  release(); await expect(region.getByRole('alert')).toContainText('Credential recovery required'); await expect(region.getByRole('alert')).toContainText('/private/tmp/recover-fixture.pem'); await expect(token).toHaveValue('fixture-transient-pairing-token'); await expect(directory).toHaveValue('/private/tmp/retained-pairing-draft');
  await region.getByRole('button', { name: 'Pair daemon credential', exact: true }).click(); await region.getByRole('button', { name: 'Generate replacement credential', exact: true }).click();
  await expect(region.getByText('Daemon credential generated and stored in Settings.', { exact: true })).toBeVisible(); await expect(token).toHaveValue(''); await expect(page.getByRole('textbox', { name: 'Credential ID', exact: true })).toHaveValue('new-fixture-credential'); await expect(directory).toHaveValue('/private/tmp/retained-pairing-draft'); expect(pairings).toEqual([{ cmd: 'generate_daemon_credential', pairing_token: 'fixture-transient-pairing-token' }, { cmd: 'generate_daemon_credential', pairing_token: 'fixture-transient-pairing-token' }]);
  expect(await page.evaluate(() => JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage)]))).not.toContain('fixture-transient-pairing-token');
  await region.scrollIntoViewIfNeeded(); await page.screenshot({ path: test.info().outputPath('relay-pairing-success.png') });
  await page.setViewportSize({ width: 390, height: 844 }); await region.scrollIntoViewIfNeeded(); expect(await region.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true); await page.screenshot({ path: test.info().outputPath('relay-pairing-narrow.png') });
  // Pairing traffic is intercepted; no external Relay is contacted or credential actually minted.
});
