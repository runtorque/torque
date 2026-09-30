import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
import { mkdtemp, mkdir, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) { const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row }; expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data; }
async function isolated(request: APIRequestContext) { const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime; expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default'); }

test('Inbox Mark all read persists across reload without resolving or deleting notices', async ({ page, request }) => {
  await isolated(request); const ids: string[] = []; const prefix = `Inbox command ${Date.now()}`;
  for (let i = 0; i < 2; i++) ids.push(String(((await command(request, { cmd: 'operator_notice_report_client_error', source: 'qa-command-audit', title: `${prefix} ${i}`, message: `Persisted unread notice ${i}`, dedupe_key: `${prefix}-${i}` })).notice as Row).id));
  await page.goto('/'); await page.getByRole('button', { name: /^Inbox/ }).click(); const inbox = page.getByRole('dialog', { name: 'Inbox', exact: true });
  for (let i = 0; i < 2; i++) await expect(inbox.getByText(`${prefix} ${i}`, { exact: true })).toBeVisible();
  await inbox.getByRole('button', { name: 'Mark all read', exact: true }).click(); await expect(inbox.getByRole('button', { name: 'Mark all read', exact: true })).toBeDisabled(); await expect(inbox.getByRole('button', { name: 'Read', exact: true })).toHaveCount(0);
  const notices = ((await command(request, { cmd: 'operator_notices_list', include_archived: true, limit: 500 })).notices as Row[]).filter((row) => ids.includes(String(row.id)));
  expect(notices).toHaveLength(2); for (const notice of notices) { expect(Number(notice.read_at)).toBeGreaterThan(0); expect(Number(notice.resolved_at ?? 0)).toBe(0); expect(Number(notice.archived_at ?? 0)).toBe(0); }
  await page.reload(); await page.getByRole('button', { name: 'Inbox', exact: true }).click(); await expect(inbox.getByRole('button', { name: 'Mark all read', exact: true })).toBeDisabled(); for (let i = 0; i < 2; i++) await expect(inbox.getByText(`${prefix} ${i}`, { exact: true })).toBeVisible();
  await page.screenshot({ path: test.info().outputPath('inbox-mark-all-read.png'), animations: 'disabled' }); await writeFile(test.info().outputPath('inbox-read.json'), JSON.stringify(notices, null, 2));
});

test('sidebar keyboard and pointer sizing persist across reconnect and reload without overwriting an active drag', async ({ page, request }) => {
  await isolated(request); let socket: WebSocketRoute | undefined; let connections = 0;
  await page.routeWebSocket(/\/ws\?/, (client) => { socket = client; connections++; client.connectToServer(); });
  await page.goto('/'); const separator = page.getByRole('separator', { name: 'Resize workspace sidebar', exact: true });
  await separator.focus(); await separator.press('Home'); await expect(separator).toHaveAttribute('aria-valuenow', '150'); await separator.press('End'); await expect(separator).toHaveAttribute('aria-valuenow', '360'); await separator.press('ArrowLeft'); await expect(separator).toHaveAttribute('aria-valuenow', '350');
  const box = (await separator.boundingBox())!; const x = box.x + box.width / 2, y = box.y + box.height / 2; await page.mouse.move(x, y); await page.mouse.down(); await page.mouse.move(x - 70, y); await expect(separator).toHaveAttribute('aria-valuenow', '280');
  await command(request, { cmd: 'ui_set_workspace_sidebar_width', width: 170 }); await expect(separator).toHaveAttribute('aria-valuenow', '280'); await page.mouse.up();
  await expect.poll(async () => (await command(request, { cmd: 'get_state' })).workspace_sidebar_width).toBe(280);
  const before = connections; await socket!.close({ code: 1012, reason: 'Sidebar persistence' }); await expect.poll(() => connections).toBeGreaterThan(before); await expect(separator).toHaveAttribute('aria-valuenow', '280'); await page.reload(); await expect(separator).toHaveAttribute('aria-valuenow', '280');
  expect(Math.round((await page.getByRole('complementary', { name: 'Torque workspace navigation' }).boundingBox())!.width)).toBe(280); await page.screenshot({ path: test.info().outputPath('sidebar-persisted.png'), animations: 'disabled' });
});

test('Clear context review cancels safely and resets only the confirmed local Worker without replacing its PTY', async ({ page, request }) => {
  test.setTimeout(60_000); await isolated(request); const group = `Clear context ${Date.now()}`; const directory = await realpath(await mkdtemp(join(tmpdir(), 'torque-clear-context-'))); let task = ''; const writes: Row[] = [];
  await mkdir(join(directory, '.torque', 'actions'), { recursive: true });
  await page.routeWebSocket(/\/ws\?/, (client) => { const server = client.connectToServer(); client.onMessage((raw) => { const frame = JSON.parse(String(raw)) as Row; if (frame.cmd === 'clear_agent_context') writes.push(frame); server.send(raw); }); server.onMessage((raw) => client.send(raw)); });
  const agents = async () => (await command(request, { cmd: 'get_state' })).agents as Record<string, Row>;
  try {
    await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: directory, default_agent_template: '', agent_provider: 'generic', agent_boot_command: '/bin/cat', git_worktree: false, notifications: false } });
    await command(request, { cmd: 'save_action', group, scope: 'project', name: 'qa/context', action: { prompt: 'Local context fixture: {{ TASK }}' } });
    const add = async (name: string) => String((await command(request, { cmd: 'add_worker', group, name, provider: 'generic', command: '/bin/cat', directory })).id);
    const id = await add('Context Worker'); const other = await add('Untouched Worker'); task = String((await command(request, { cmd: 'board_add_task', group, task: 'Context fixture', lane: 'Backlog', action_name: 'qa/context' })).task_id); await command(request, { cmd: 'dispatch_task', id: task, agent_id: id });
    await expect.poll(async () => Number((await agents())[id]?.tasks_dispatched)).toBeGreaterThan(0); const initial = (await agents())[id]!; const peer = (await agents())[other]!; expect(initial.current_task_id).toBe(task);
    await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_select_agent', id }); await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'agents', controlTab: 'mission' } }); await page.goto('/');
    const open = async () => { await page.getByRole('button', { name: 'Lifecycle actions for Context Worker', exact: true }).click(); await page.getByRole('menuitem', { name: 'Clear context', exact: true }).click(); };
    await open(); const dialog = page.getByRole('dialog', { name: 'Clear agent context?', exact: true }); await expect(dialog).toContainText('Context Worker'); await expect(dialog.getByRole('button', { name: 'Cancel', exact: true })).toBeFocused(); await page.keyboard.press('Escape'); await expect(dialog).toHaveCount(0); expect(writes).toHaveLength(0); expect((await agents())[id]?.current_task_id).toBe(task);
    await open(); await dialog.getByRole('button', { name: 'Clear context', exact: true }).click(); await expect(dialog).toHaveCount(0); await expect.poll(async () => Number((await agents())[id]?.tasks_dispatched)).toBe(0); await expect.poll(async () => (await agents())[id]?.current_task_id).toBe('');
    const cleared = (await agents())[id]!; expect(cleared.session_id).toBe(initial.session_id); expect((await agents())[other]?.session_id).toBe(peer.session_id); expect(writes).toEqual([{ cmd: 'clear_agent_context', id }]);
    await page.screenshot({ path: test.info().outputPath('clear-context.png'), animations: 'disabled' }); await writeFile(test.info().outputPath('clear-context.json'), JSON.stringify({ initial, cleared, writes }, null, 2));
  } finally { if (task) await command(request, { cmd: 'board_remove_task', id: task }); await command(request, { cmd: 'remove_group', group }); await rm(directory, { recursive: true, force: true }); }
});
