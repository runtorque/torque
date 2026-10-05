import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
test('Relay device links require confirmation, encode locally, survive refresh and disappear on dismiss or close', async ({ page, request }) => {
  const runtime = await (await request.get('/api/runtime')).json() as { data: { runtime: { port: number; profile: string } } };
  expect(runtime.data.runtime.port).not.toBe(18932); expect(runtime.data.runtime.profile).not.toBe('default');
  const group = `Device link ${Date.now()}`; await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  const mints: Row[] = []; let enabled = true; let socket: WebSocketRoute | undefined; let connections = 0;
  let release!: () => void; const held = new Promise<void>((resolve) => { release = resolve; });
  const externalRequests: string[] = []; page.on('request', (incoming) => { if (new URL(incoming.url()).hostname !== '127.0.0.1') externalRequests.push(incoming.url()); });
  const resolvedConfig = () => ({ config: { enabled, relay_url: 'https://relay.invalid', daemon_id: 'fixture-daemon' }, sources: { enabled: { source: 'env', value: enabled }, relay_url: { source: 'env', value: 'https://relay.invalid' }, daemon_id: { source: 'env', value: 'fixture-daemon' } } });
  await page.routeWebSocket(/\/ws\?/, (connection) => {
    const server = connection.connectToServer(); socket = connection; connections += 1;
    server.onMessage((message) => {
      const frame = JSON.parse(String(message)) as Row;
      if (frame.type === 'state') frame.relay_config = resolvedConfig();
      if (frame.type === 'delta' && Array.isArray(frame.ops)) frame.ops = (frame.ops as Row[]).map((op) => op.op === 'relay_config' ? { ...op, ...resolvedConfig() } : op);
      connection.send(JSON.stringify(frame));
    });
  });
  const reconnect = async () => { const previous = connections; await socket!.close({ code: 1012, reason: 'Device link continuity' }); await expect.poll(() => connections).toBeGreaterThan(previous); };
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd === 'get_global_settings') {
      const response = await route.fetch(); const body = await response.json() as { data: Row }; body.data.relay_config = resolvedConfig(); await route.fulfill({ response, json: body }); return;
    }
    if (data.cmd === 'generate_relay_device_link') {
      mints.push(data);
      if (mints.length === 1) await route.fulfill({ json: { ok: true, data: { type: 'relay_device_link', ok: false, error: 'relay_not_started', message: 'Fixture Relay is disconnected.' } } });
      else { if (mints.length === 2) await held; const code = `fixture-device-code-${mints.length}`; await route.fulfill({ json: { ok: true, data: { type: 'relay_device_link', ok: true, code, establish_url: `https://relay.invalid/establish?code=${code}`, expires_at: new Date(Date.now() + 90_000).toISOString() } } }); }
      return;
    }
    await route.continue();
  });
  await page.goto('/'); await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click();
  const region = page.getByRole('region', { name: 'Relay device link' }); const generate = region.getByRole('button', { name: 'Generate one-time device link', exact: true });
  await expect(generate).toBeEnabled(); expect(mints).toEqual([]); await generate.click(); await expect(region.getByRole('group')).toContainText('single-use'); expect(mints).toEqual([]);
  await region.getByRole('button', { name: 'Cancel device link', exact: true }).click(); expect(mints).toEqual([]); await generate.click(); await region.getByRole('button', { name: 'Confirm and generate device link', exact: true }).click();
  await expect(region.getByRole('alert')).toContainText('Enable and start Relay'); expect(mints).toHaveLength(1);
  await generate.click(); await region.getByRole('button', { name: 'Confirm and generate device link', exact: true }).click(); await expect.poll(() => mints.length).toBe(2); await expect(region.getByRole('button', { name: 'Generating device link…', exact: true })).toBeDisabled();
  await reconnect(); expect(mints).toHaveLength(2); release();
  const url = region.getByLabel('Device link URL'); const qr = region.getByRole('img', { name: 'Device link QR code', exact: true });
  await expect(url).toHaveText('https://relay.invalid/establish?code=fixture-device-code-2'); await expect(qr).toBeVisible(); await expect(qr.locator('path')).toHaveAttribute('d', /^M\d+,\d+h1v1h-1z/); await expect(region.getByText(/Expires in/)).toBeVisible();
  await url.focus(); await url.evaluate((element) => { element.dataset.secretAnchor = 'original'; }); await qr.evaluate((element) => { element.dataset.qrAnchor = 'original'; });
  enabled = false; await reconnect(); await expect(generate).toBeDisabled(); await expect(url).toBeFocused(); await expect(url).toHaveAttribute('data-secret-anchor', 'original'); await expect(qr).toHaveAttribute('data-qr-anchor', 'original'); expect(mints).toHaveLength(2);
  expect(await page.evaluate(() => JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage)]))).not.toContain('fixture-device-code'); expect(externalRequests).toEqual([]);
  await region.scrollIntoViewIfNeeded(); await page.screenshot({ path: test.info().outputPath('relay-device-link.png') });
  await page.setViewportSize({ width: 390, height: 844 }); await region.scrollIntoViewIfNeeded(); expect(await region.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true); await page.screenshot({ path: test.info().outputPath('relay-device-link-narrow.png') });
  await region.getByRole('button', { name: 'Dismiss device link', exact: true }).click(); await expect(url).toHaveCount(0); await expect(qr).toHaveCount(0); enabled = true; await reconnect(); await expect(generate).toBeEnabled(); await expect(url).toHaveCount(0);
  await generate.click(); await region.getByRole('button', { name: 'Confirm and generate device link', exact: true }).click(); await expect(region.getByLabel('Device link code')).toHaveText('fixture-device-code-3');
  await page.getByRole('button', { name: 'Mission Control', exact: true }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click(); await expect(generate).toBeVisible(); await expect(region.getByLabel('Device link URL')).toHaveCount(0); await expect(region.getByRole('img')).toHaveCount(0);
  expect(mints).toEqual(Array.from({ length: 3 }, () => ({ cmd: 'generate_relay_device_link', confirm: true }))); expect(externalRequests).toEqual([]);
  // All mint replies are fixtures; no real external credential is generated.
});
