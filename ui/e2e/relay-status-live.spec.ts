import { expect as baseExpect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
const expect = baseExpect.configure({ timeout: 15_000 });
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
test('Relay diagnostics and passive status update without losing drafts or retained connection probes', async ({ page, request }) => {
  test.setTimeout(90_000);
  const runtime = await (await request.get('/api/runtime')).json() as { data: { runtime: { port: number; profile: string } } };
  expect(runtime.data.runtime.port).not.toBe(18932); expect(runtime.data.runtime.profile).not.toBe('default');
  const initialSettings = await command(request, { cmd: 'get_global_settings' }); expect(((initialSettings.relay_config as Row).config as Row).enabled).not.toBe(true);
  const group = `Relay status ${Date.now()}`; await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'update_global_settings', settings: { status_bar_visibility: { daemon_status: true } } });
  let connection: Row = { status: 'connecting', enabled: true, configured: true, relay_host: 'fixture.relay.invalid', daemon_id: 'fixture-daemon', retry_count: 4, since: '2026-09-25T10:01:00Z', last_connected_at: '2026-09-25T10:00:00Z', last_error: 'Fixture connection refused' };
  let socket: WebSocketRoute | undefined; let connections = 0; let probes = 0; let pulseCount = 0;
  let release!: () => void; const held = new Promise<void>((resolve) => { release = resolve; });
  await page.routeWebSocket(/\/ws\?/, (route) => {
    socket = route; connections += 1; const server = route.connectToServer();
    server.onMessage((message) => {
      const frame = JSON.parse(String(message)) as Row;
      if (frame.type === 'state') frame.relay_connection = connection;
      if (frame.type === 'delta' && Array.isArray(frame.ops)) frame.ops = [...(frame.ops as Row[]), { op: 'relay_connection', ...connection }];
      route.send(JSON.stringify(frame));
    });
  });
  const pulse = async (patch: Row) => { connection = { ...connection, ...patch }; pulseCount += 1; await command(request, { cmd: 'update_group_settings', group, settings: { max_agents: pulseCount } }); };
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd !== 'test_relay_connection') { await route.continue(); return; }
    probes += 1;
    if (probes === 1) { await route.continue(); return; } // Exercise the real disabled backend probe; it cannot contact an external Relay.
    if (probes === 2) { await held; await route.fulfill({ json: { ok: true, data: { type: 'relay_test_result', status: 'ca_missing', message: 'Install the fixture CA bundle.', detail: 'Fixture root certificate unavailable.\nVerify the CA path.' } } }); return; }
    if (probes === 3) { await route.fulfill({ json: { ok: false, error: 'Fixture probe refused' } }); return; }
    await route.fulfill({ json: { ok: true, data: { type: 'relay_test_result', status: 'ok', message: 'Fixture connectivity verified.', detail: 'Authenticated fixture response.' } } });
  });
  await page.goto('/'); await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click();
  const footer = page.locator('footer[aria-label="Workspace status"]'); const indicator = footer.getByRole('img', { name: /^Relay:/ });
  const diagnostics = page.getByRole('region', { name: 'Relay connection', exact: true }); const probe = page.getByRole('region', { name: 'Relay connection test', exact: true });
  await expect(indicator).toHaveAttribute('data-tone', 'warning'); await expect(indicator).toHaveAttribute('title', /Host: fixture.relay.invalid/); await expect(diagnostics).toContainText('fixture-daemon'); await expect(diagnostics).toContainText('Fixture connection refused'); expect(probes).toBe(0);
  await probe.getByRole('button', { name: 'Test connection', exact: true }).click(); await expect(probe.getByRole('status')).toContainText('Relay disabled'); expect(probes).toBe(1);
  await probe.getByRole('button', { name: 'Test connection', exact: true }).click(); await expect(probe.getByRole('button', { name: 'Testing connection…', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Mission Control', exact: true }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click(); await expect(probe.getByRole('button', { name: 'Testing connection…', exact: true })).toBeDisabled();
  const previous = connections; await socket!.close({ code: 1012, reason: 'Pending probe continuity' }); await expect.poll(() => connections).toBeGreaterThan(previous); expect(probes).toBe(2); release();
  await expect(probe.getByRole('status')).toContainText('CA certificate unavailable'); await expect(probe.getByRole('status')).toContainText('Verify the CA path.');
  const directory = page.getByRole('textbox', { name: 'Default directory', exact: true }); await directory.fill('/private/tmp/retained-relay-status-draft'); await directory.focus(); await directory.evaluate((input: HTMLInputElement) => { input.setSelectionRange(3, 10); input.dataset.relayDraft = 'original'; });
  await probe.getByRole('status').evaluate((element) => { element.dataset.probeAnchor = 'original'; });
  await pulse({ retry_count: 5 }); await expect(indicator).toHaveAttribute('data-tone', 'danger'); await expect(indicator).toHaveAttribute('data-state', 'connecting'); await expect(diagnostics).toContainText('Repeated retries need attention.');
  await expect(directory).toBeFocused(); await expect(directory).toHaveAttribute('data-relay-draft', 'original'); expect(await directory.evaluate((input: HTMLInputElement) => [input.selectionStart, input.selectionEnd])).toEqual([3, 10]); await expect(probe.getByRole('status')).toHaveAttribute('data-probe-anchor', 'original'); expect(probes).toBe(2);
  await indicator.focus(); await indicator.evaluate((element) => { element.dataset.relayIndicator = 'original'; }); await pulse({ status: 'future_connection', retry_count: 7 }); await expect(indicator).toHaveAttribute('data-state', 'future_connection'); await expect(indicator).toHaveAttribute('data-tone', 'warning'); await expect(indicator).toBeFocused(); await expect(indicator).toHaveAttribute('data-relay-indicator', 'original');
  await pulse({ configured: false }); await expect(indicator).toHaveCount(0); await expect(diagnostics).toContainText('future_connection');
  await pulse({ configured: true, status: 'connected', retry_count: 0, last_error: '', last_connected_at: '2026-09-25T10:02:00Z' }); await expect(indicator).toHaveAttribute('data-tone', 'success'); await expect(diagnostics).toContainText('2026-09-25T10:02:00Z');
  await command(request, { cmd: 'update_global_settings', settings: { status_bar_visibility: { daemon_status: false } } }); await expect(indicator).toHaveCount(0); await command(request, { cmd: 'update_global_settings', settings: { status_bar_visibility: { daemon_status: true } } }); await expect(indicator).toBeVisible();
  await probe.getByRole('button', { name: 'Test connection', exact: true }).click(); await expect(probe.getByRole('alert')).toContainText('Fixture probe refused'); await probe.getByRole('button', { name: 'Retry connection test', exact: true }).click(); await expect(probe.getByRole('status')).toContainText('Authenticated fixture response.'); expect(probes).toBe(4);
  await expect(directory).toHaveValue('/private/tmp/retained-relay-status-draft'); await diagnostics.scrollIntoViewIfNeeded(); await page.screenshot({ path: test.info().outputPath('relay-status.png') });
  await page.setViewportSize({ width: 390, height: 844 }); await diagnostics.scrollIntoViewIfNeeded(); await expect(indicator).toBeVisible(); expect(await diagnostics.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true); expect(await footer.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true); await page.screenshot({ path: test.info().outputPath('relay-status-narrow.png') });
});
