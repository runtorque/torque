import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}
test('Control Center counts exclude deleted agents and cascading terminals and recover on restore', async ({ page, request }) => {
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Control counts ${Date.now()}`; let id = ''; let removed = false; let socket: WebSocketRoute | undefined; let connections = 0;
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: '/private/tmp', git_worktree: false } });
  await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'mission' } });
  await page.routeWebSocket(/\/ws\?/, (client) => { client.connectToServer(); socket = client; connections++; });
  const root = page.getByRole('region', { name: 'Control Center', exact: true });
  const header = root.locator(':scope > header');
  const metric = root.getByRole('article').filter({ has: page.getByText('Agents', { exact: true }) }).locator('strong');
  const count = async (agents: number, terminals = 0) => {
    await expect(header).toContainText(`${agents} ${agents === 1 ? 'agent' : 'agents'}${terminals ? ` · ${terminals} terminal` : ''} ·`);
    if (!terminals) await expect(header).not.toContainText('terminal');
    await expect(metric).toHaveText(String(agents));
  };
  try {
    await page.goto('/'); await count(0);
    const created = await command(request, { cmd: 'add_engineer', group, name: 'Count receiver', provider: 'generic', command: '/bin/cat', directory: '/private/tmp' }); id = String(created.id);
    await command(request, { cmd: 'add_terminal', group, parent_id: id, name: 'Count companion', command: '/bin/cat', directory: '/private/tmp', shell: '/bin/sh' });
    await count(1, 1);
    await command(request, { cmd: 'remove_agent', id }); removed = true; await count(0);
    const before = connections; await socket!.close({ code: 1012, reason: 'Deleted count reconnect' }); await expect.poll(() => connections).toBeGreaterThan(before); await count(0);
    await page.reload(); await count(0);
    await command(request, { cmd: 'restore_agent', id }); removed = false; await count(1, 1);
    await page.reload(); await count(1, 1);
    await page.screenshot({ path: test.info().outputPath('control-restored-counts.png') });
    await command(request, { cmd: 'remove_agent', id }); removed = true; await count(0);
  } finally { if (id && !removed) await command(request, { cmd: 'remove_agent', id }); }
});
