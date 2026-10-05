import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
type Row = Record<string, unknown>;
const fields = [
  ['notify_on_finish', 'Notify on finish', 'session_end', 'finished', 'notification'],
  ['notify_on_error', 'Notify on error', 'error', 'error', 'alert'],
  ['notify_on_attention', 'Notify on attention', 'waiting', 'needs attention', 'notification'],
] as const;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}

test('Notification settings persist independent event gates and retain Inbox history with desktop delivery off', async ({ page, request }) => {
  test.setTimeout(90_000);
  test.skip(!process.env.TORQUE_NOTIFICATION_EVENT_QA, 'Requires a disposable daemon with TORQUE_PROFILE_ENABLED=1');
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Notification settings ${Date.now()}`; const name = `Notification receiver ${Date.now()}`;
  let id = ''; let socket: WebSocketRoute | undefined; let connections = 0; let updates = 0; let refuse = true;
  const events: Row[] = []; const writes: Row[] = []; const observed: Row[] = [];
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: '/private/tmp', git_worktree: false, agent_provider: 'generic', notifications: false } });
  await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'settings' } });
  const original = (await command(request, { cmd: 'get_group_settings', group })).settings as Row;
  await page.routeWebSocket(/\/ws\?/, (client) => {
    socket = client; connections++; const server = client.connectToServer();
    server.onMessage((message) => { const frame = JSON.parse(String(message)) as Row; if (frame.type === 'delta' && Array.isArray(frame.ops)) for (const op of frame.ops as Row[]) { if (op.op === 'event_append') events.push(op); if (op.op === 'group_settings_update' && op.name === group) updates++; } client.send(message); });
  });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd === 'update_group_settings') { writes.push(data); if (refuse) { refuse = false; await route.fulfill({ json: { ok: false, error: 'QA notification settings refused' } }); return; } }
    await route.continue();
  });
  const open = async () => { const summary = page.getByText(`${group} execution, worktrees, notifications and sync`, { exact: true }); if (!(await summary.evaluate((node) => node.closest('details')?.open))) await summary.click(); };
  const save = async () => { await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible(); };
  const notices = async () => ((await command(request, { cmd: 'operator_notices_list', include_archived: true, limit: 500 })).notices as Row[]).filter((row) => row.agent_id === id && row.source === 'agent_event');
  const emit = async (event: string, phase: string) => {
    const marker = `${group}-${event}-${phase}`;
    if (event === 'session_end') {
      // Completion is accepted only for a live turn. Exercise the real user
      // message path to arm Running rather than bypassing the stale-end guard.
      await command(request, { cmd: 'user_agent_message', agent_id: id, message: `QA begin ${marker}`, idempotency_key: marker });
      await expect.poll(async () => (((await command(request, { cmd: 'get_state' })).agents as Record<string, Row>)[id])?.status).toBe('running');
    }
    const result = await request.post('/events', { headers: { 'X-Torque-Cell-Id': id }, data: { source: 'torque-profile-harness', event_id: marker, event_type: event, data: { summary: marker, error: marker, reason: marker } } }); expect(result.status()).toBe(200);
    // The real panel event proves this input was consumed before checking notice
    // absence; no arbitrary sleep or simulated notice response is used.
    await expect.poll(() => events.some((row) => row.cell_id === id && row.message === marker)).toBe(true);
    return notices();
  };
  try {
    await page.goto('/'); await open();
    const desktop = page.getByRole('combobox', { name: 'Desktop notifications', exact: true }); await expect(desktop).toHaveValue('false'); await expect(desktop).toHaveAccessibleDescription(/macOS.*Inbox history/);
    for (const [, label] of fields) { const input = page.getByRole('combobox', { name: label, exact: true }); await expect(input).toBeEnabled(); await input.selectOption('false'); }
    await desktop.selectOption('true'); await desktop.focus(); await desktop.evaluate((node) => node.setAttribute('data-retained', 'yes'));
    const beforeUpdates = updates; await command(request, { cmd: 'update_group_settings', group, settings: { max_agents: 7 } }); await expect.poll(() => updates).toBeGreaterThan(beforeUpdates);
    const before = connections; await socket!.close({ code: 1012, reason: 'Notification choices reconnect' }); await expect.poll(() => connections).toBeGreaterThan(before);
    await expect(desktop).toBeFocused(); await expect(desktop).toHaveAttribute('data-retained', 'yes'); await expect(desktop).toHaveValue('true'); expect(writes).toHaveLength(0);
    await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByRole('alert')).toContainText('QA notification settings refused'); await save(); expect(writes[1]).toEqual(writes[0]);
    const disabledEvents = Object.fromEntries(fields.map(([key]) => [key, false])); expect(writes[1]!.settings).toEqual({ notifications: true, ...disabledEvents });
    await page.reload(); await open(); await expect(desktop).toHaveValue('true'); for (const [, label] of fields) await expect(page.getByRole('combobox', { name: label, exact: true })).toHaveValue('false');
    // Toggle desktop delivery back off before generating events. Inbox recording
    // still uses the actual daemon; native banner delivery has backend coverage.
    await desktop.selectOption('false'); await save(); await page.reload(); await open(); await expect(desktop).toHaveValue('false');
    expect(writes.at(-1)?.settings).toEqual({ notifications: false });
    id = String((await command(request, { cmd: 'add_worker', group, name, provider: 'generic', command: '/bin/cat', directory: '/private/tmp', worktree: false })).id);
    for (const [, , event] of fields) expect(await emit(event, 'disabled')).toEqual([]);
    for (const [index, [key, label, event, suffix, noticeType]] of fields.entries()) {
      await page.getByRole('combobox', { name: label, exact: true }).selectOption('true'); await save(); expect(writes.at(-1)?.settings).toEqual({ [key]: true });
      const settings = (await command(request, { cmd: 'get_group_settings', group })).settings as Row; expect(settings[key]).toBe(true); expect(settings.notifications).toBe(false);
      await page.reload(); await open(); await expect(page.getByRole('combobox', { name: label, exact: true })).toHaveValue('true');
      const rows = await emit(event, 'enabled'); expect(rows).toHaveLength(index + 1); expect(rows.find((row) => row.title === `${name} ${suffix}`)).toMatchObject({ notice_type: noticeType }); observed.push({ key, settings, notices: rows });
    }
    await desktop.scrollIntoViewIfNeeded(); await page.screenshot({ path: test.info().outputPath('notification-settings.png') });
    await page.getByRole('button', { name: /^Inbox/ }).click(); const inbox = page.getByRole('dialog', { name: 'Inbox', exact: true });
    await inbox.getByRole('tab', { name: /^Alerts/ }).click(); await expect(inbox.getByText(`${name} error`, { exact: true })).toBeVisible();
    await inbox.getByRole('tab', { name: /^Notifications/ }).click(); for (const suffix of ['finished', 'needs attention']) await expect(inbox.getByText(`${name} ${suffix}`, { exact: true })).toBeVisible();
    await page.screenshot({ path: test.info().outputPath('notification-inbox.png') }); await page.keyboard.press('Escape');
    for (const [, label] of fields) await page.getByRole('combobox', { name: label, exact: true }).selectOption('false'); await save();
    const beforeDisabled = await notices(); for (const [, , event] of fields) await emit(event, 'disabled-again'); expect(await notices()).toEqual(beforeDisabled);
    await page.reload(); await open(); await expect(desktop).toHaveValue('false'); for (const [, label] of fields) await expect(page.getByRole('combobox', { name: label, exact: true })).toHaveValue('false');
    const path = test.info().outputPath('notification-settings-evidence.json'); await writeFile(path, JSON.stringify({ writes, observed, retained: await notices() }, null, 2)); await test.info().attach('notification-settings-evidence', { path, contentType: 'application/json' });
  } finally { await command(request, { cmd: 'update_group_settings', group, settings: Object.fromEntries(['notifications', ...fields.map(([key]) => key)].map((key) => [key, original[key]])) }); if (id) await command(request, { cmd: 'remove_agent', id }); }
});
