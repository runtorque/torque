import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
for (const [section, label] of [['context', 'Context'], ['logs', 'Logs'], ['help', 'Help']] as const) {
  test(`detached Control hands off ${label} before reads and preserves local navigation across reconnect`, async ({ page, context, request }) => {
    const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
    expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
    const group = `Detached ${label} ${Date.now()}`;
    await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
    await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: section } });
    await page.addInitScript(() => {
      const target = window as Window & { __TAURI_INTERNALS__?: unknown; detachCalls?: { command: string; args: Record<string, unknown> }[] };
      target.detachCalls = []; let detached: { panel: unknown; label: string }[] = [];
      target.__TAURI_INTERNALS__ = { invoke: (command: string, args: Record<string, unknown>) => {
        target.detachCalls!.push({ command, args });
        if (command === 'detach') { detached = [{ panel: args.panel, label: 'control-section-qa' }]; return Promise.resolve('control-section-qa'); }
        if (command === 'list_detached') return Promise.resolve(detached);
        if (command === 'reattach') detached = [];
        return Promise.resolve(null);
      } };
    });
    await page.goto('/'); await expect(page.getByRole('button', { name: label, exact: true })).toHaveAttribute('aria-current', 'page');
    await page.evaluate(() => (window as Window & { detachActivePanel?: () => void }).detachActivePanel!());
    await expect.poll(() => page.evaluate(() => (window as Window & { detachCalls?: { command: string; args: Row }[] }).detachCalls?.find((item) => item.command === 'detach')?.args)).toMatchObject({ panel: 'control', section });
    await expect(page.getByRole('button', { name: 'Reattach workspace', exact: true })).toBeVisible();
    // The browser harness exercises the host boundary; Rust tests verify native URL construction.
    const detached = await context.newPage(); const reads: Row[] = []; let socket: WebSocketRoute | undefined; let connections = 0;
    await detached.routeWebSocket(/\/ws\?/, (connection) => { socket = connection; connections++; connection.connectToServer(); });
    await detached.route('**/api/cmd', async (route) => { reads.push(route.request().postDataJSON() as Row); await route.continue(); });
    await detached.goto(`/?panel=control&window=control-section-qa&section=${section}`);
    await expect(detached.getByRole('button', { name: label, exact: true })).toHaveAttribute('aria-current', 'page');
    expect(reads.some((data) => ['get_system_health_metrics', 'get_metrics_history'].includes(String(data.cmd)))).toBe(false);
    const next = section === 'help' ? 'Logs' : 'Help';
    await detached.getByRole('button', { name: next, exact: true }).click();
    await expect(detached.getByRole('button', { name: next, exact: true })).toHaveAttribute('aria-current', 'page');
    const before = connections; await socket!.close({ code: 1012, reason: 'Detached section reconnect' });
    await expect.poll(() => connections).toBeGreaterThan(before);
    await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'board', controlTab: 'mission' } });
    await expect(detached.getByRole('button', { name: next, exact: true })).toHaveAttribute('aria-current', 'page');
    expect(reads.some((data) => data.cmd === 'ui_set_react_workspace_state')).toBe(false);
    await detached.reload(); await expect(detached.getByRole('button', { name: label, exact: true })).toHaveAttribute('aria-current', 'page');
    if (section === 'context') await expect(detached.getByText('No shared context', { exact: true })).toBeVisible();
    if (section === 'help') await expect(detached.getByRole('button', { name: 'Torque README.md', exact: true })).toBeVisible();
    if (section === 'logs') await expect(detached.getByRole('log')).toContainText('ui_set_react_workspace_state');
    await detached.screenshot({ animations: 'disabled', path: test.info().outputPath(`detached-${section}.png`) });
    await detached.close(); await page.getByRole('button', { name: 'Reattach workspace', exact: true }).click();
    await expect(page.getByRole('button', { name: label, exact: true })).toHaveAttribute('aria-current', 'page');
  });
}
