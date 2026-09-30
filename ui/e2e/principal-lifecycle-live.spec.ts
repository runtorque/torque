import { expect, test, type APIRequestContext, type Page, type WebSocketRoute } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const response = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(response.ok, response.error).toBe(true); expect(response.data.type).not.toBe('error'); return response.data;
}
async function lifecycle(page: Page, name: string, action: string) { await page.getByRole('button', { name: `Lifecycle actions for ${name}`, exact: true }).click(); await page.getByRole('menuitem', { name: action, exact: true }).click(); }
for (const kind of ['architect', 'engineer']) test(`${kind} lifecycle preserves identity through dismiss/rehire and reviewed delete/restore without affecting another agent`, async ({ page, request }) => {
  test.setTimeout(90_000); page.setDefaultTimeout(12_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Principal lifecycle ${kind} ${Date.now()}`; const name = `Lifecycle ${kind}`; const writes: Row[] = []; let socket: WebSocketRoute | undefined; let connections = 0;
  await page.routeWebSocket(/\/ws\?/, (client) => { socket = client; connections++; const server = client.connectToServer(); client.onMessage((raw) => { const frame = JSON.parse(String(raw)) as Row; if ([`${kind}_dismiss`, `${kind}_rehire`, 'remove_agent', 'restore_agent', 'relaunch_agent'].includes(String(frame.cmd))) writes.push(frame); server.send(raw); }); server.onMessage((raw) => client.send(raw)); });
  const agents = async () => (await command(request, { cmd: 'get_state' })).agents as Record<string, Row>;
  try {
    await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: '/private/tmp', agent_provider: 'generic', agent_boot_command: '/bin/cat', default_agent_template: '', git_worktree: false, notifications: false } });
    const id = String((await command(request, { cmd: `add_${kind}`, group, name, provider: 'generic', command: '/bin/cat', directory: '/private/tmp' })).id);
    const other = String((await command(request, { cmd: 'add_worker', group, name: 'Unaffected Worker', provider: 'generic', command: '/bin/cat', directory: '/private/tmp' })).id);
    const initial = (await agents())[id]!; const peer = (await agents())[other]!; expect(initial.session_id).toBeTruthy();
    await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_select_agent', id }); await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'agents', controlTab: 'mission' } }); await page.goto('/');
    await lifecycle(page, name, 'Dismiss'); await expect.poll(async () => Number((await agents())[id]?.dismissed_at ?? 0)).toBeGreaterThan(0); await expect.poll(async () => Boolean((await agents())[id]?.session_id)).toBe(false);
    await lifecycle(page, name, 'Rehire'); await expect.poll(async () => Number((await agents())[id]?.dismissed_at ?? 0)).toBe(0); await expect.poll(async () => Boolean((await agents())[id]?.session_id)).toBe(true);
    const rehired = (await agents())[id]!; expect(rehired.id).toBe(initial.id); expect(rehired.slug).toBe(initial.slug); expect(rehired.session_id).not.toBe(initial.session_id);
    await lifecycle(page, name, 'Delete…'); const dialog = page.getByRole('dialog', { name: 'Delete agent?', exact: true }); await expect(dialog).toContainText(name); await page.keyboard.press('Escape'); await expect(dialog).toHaveCount(0); expect(writes.filter((frame) => frame.cmd === 'remove_agent')).toHaveLength(0); expect(Number((await agents())[id]?.deleted_at ?? 0)).toBe(0);
    await lifecycle(page, name, 'Delete…'); await dialog.getByRole('button', { name: 'Delete agent', exact: true }).click(); await expect.poll(async () => Number((await agents())[id]?.deleted_at ?? 0)).toBeGreaterThan(0); await expect(page.locator(`[role="treeitem"][data-agent-id="${id}"]`)).toHaveCount(0);
    await page.getByRole('button', { name: /^Recently deleted/ }).click(); const deleted = page.getByRole('dialog', { name: 'Recently deleted', exact: true }); const card = deleted.getByRole('article').filter({ hasText: name }); await card.getByRole('button', { name: 'Restore', exact: true }).click(); await expect(card).toHaveCount(0); await expect.poll(() => deleted.evaluate((node) => node.contains(document.activeElement))).toBe(true); await page.keyboard.press('Escape'); await expect(deleted).toHaveCount(0);
    await expect.poll(async () => Number((await agents())[id]?.deleted_at ?? 0)).toBe(0); const restored = (await agents())[id]!; expect(restored.id).toBe(initial.id); expect(restored.slug).toBe(initial.slug); expect(restored.status).toBe('stopped');
    await page.locator(`[role="treeitem"][data-agent-id="${id}"]`).click(); await lifecycle(page, name, 'Relaunch'); await expect.poll(async () => Boolean((await agents())[id]?.session_id)).toBe(true);
    const before = connections; await socket!.close({ code: 1012, reason: 'Principal lifecycle persistence' }); await expect.poll(() => connections).toBeGreaterThan(before); await expect(page.locator(`[role="treeitem"][data-agent-id="${id}"]`)).toBeVisible();
    expect((await agents())[other]?.session_id).toBe(peer.session_id); expect(writes.filter((frame) => frame.cmd === `${kind}_dismiss`)).toHaveLength(1); expect(writes.filter((frame) => frame.cmd === `${kind}_rehire`)).toHaveLength(1); expect(writes.filter((frame) => frame.cmd === 'remove_agent')).toEqual([expect.objectContaining({ id })]); expect(writes.filter((frame) => frame.cmd === 'restore_agent')).toEqual([expect.objectContaining({ id })]);
    await page.screenshot({ path: test.info().outputPath(`${kind}-lifecycle.png`), animations: 'disabled' }); await writeFile(test.info().outputPath(`${kind}-lifecycle.json`), JSON.stringify({ initial, rehired, restored, final: (await agents())[id], writes }, null, 2));
  } finally { await command(request, { cmd: 'remove_group', group }); }
});
