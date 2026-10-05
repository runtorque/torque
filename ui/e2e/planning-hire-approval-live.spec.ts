import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}

test('hire approval retains a refused proposal and creates one owned Engineer after capacity is restored', async ({ page, request }) => {
  test.setTimeout(60_000); page.setDefaultTimeout(12_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Hire approval ${Date.now()}`; const writes: Row[] = []; const errors: string[] = [];
  let socket: WebSocketRoute | undefined; let connections = 0;
  page.on('pageerror', (error) => errors.push(error.message));
  await page.routeWebSocket(/\/ws\?/, (client) => {
    socket = client; connections++; const server = client.connectToServer();
    client.onMessage((raw) => { const data = JSON.parse(String(raw)) as Row; if (data.cmd === 'pending_hire_approve') writes.push(data); server.send(raw); });
    server.onMessage((raw) => client.send(raw));
  });
  const state = () => command(request, { cmd: 'get_state' });
  const ownedAgents = async () => Object.values((await state()).agents as Record<string, Row>).filter((agent) => agent.group === group);
  try {
    await command(request, { cmd: 'add_group', group });
    await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: '/private/tmp', default_agent_template: '', agent_provider: 'generic', agent_boot_command: '/bin/cat', git_worktree: false, notifications: false, max_agents: 1 } });
    const architect = String((await command(request, { cmd: 'add_architect', group, name: 'Approval Architect', command: '/bin/cat', provider: 'generic', directory: '/private/tmp' })).id);
    const hire = await command(request, { cmd: 'architect_engineer_hire', architect_id: architect, name: 'Approved Engineer', command: '/bin/cat', provider: 'generic', directory: '/private/tmp' });
    const storedHire = async () => ((await command(request, { cmd: 'pending_hire_list', architect_id: architect })).pending_hires as Row[]).find((item) => item.id === hire.hire_id);
    await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'planning', controlTab: 'mission' } });
    await page.goto('/'); await page.getByRole('button', { name: 'Hires & journals', exact: true }).click();
    const card = page.locator('article').filter({ has: page.getByRole('button', { name: 'Approve', exact: true }) });
    await expect(card).toContainText('Approved Engineer'); await card.getByRole('button', { name: 'Approve', exact: true }).click();
    await expect(page.getByText('Failed to create engineer', { exact: true })).toBeVisible();
    expect(await storedHire()).toMatchObject({ status: 'pending', created_engineer_id: '' }); expect(await ownedAgents()).toHaveLength(1);
    const before = connections; await socket!.close({ code: 1012, reason: 'Refused hire reconnect' }); await expect.poll(() => connections).toBeGreaterThan(before);
    await expect(card).toContainText('Approved Engineer'); expect(writes).toHaveLength(1);
    await command(request, { cmd: 'update_group_settings', group, settings: { max_agents: 2 } });
    await card.getByRole('button', { name: 'Approve', exact: true }).click();
    await expect.poll(async () => (await storedHire())?.status).toBe('approved'); await expect(card).toHaveCount(0);
    const accepted = (await storedHire())!; const engineer = String(accepted.created_engineer_id);
    expect((await ownedAgents()).find((agent) => agent.id === engineer)).toMatchObject({ name: 'Approved Engineer', kind: 'engineer', hired_by_architect_id: architect, group, command: '/bin/cat' });
    expect(await ownedAgents()).toHaveLength(2);
    const previous = connections; await socket!.close({ code: 1012, reason: 'Accepted hire reconnect' }); await expect.poll(() => connections).toBeGreaterThan(previous);
    await expect(card).toHaveCount(0); await page.reload(); await page.getByRole('button', { name: 'Hires & journals', exact: true }).click(); await expect(card).toHaveCount(0);
    expect(await ownedAgents()).toHaveLength(2); expect((await storedHire())?.created_engineer_id).toBe(engineer);
    expect(writes).toEqual([{ cmd: 'pending_hire_approve', id: hire.hire_id }, { cmd: 'pending_hire_approve', id: hire.hire_id }]); expect(errors).toEqual([]);
    await page.screenshot({ animations: 'disabled', path: test.info().outputPath('hire-approved.png') });
    const path = test.info().outputPath('hire-approval-evidence.json'); await writeFile(path, JSON.stringify({ writes, hire: accepted, agents: await ownedAgents() }, null, 2)); await test.info().attach('hire-approval-evidence', { path, contentType: 'application/json' });
  } finally { await command(request, { cmd: 'remove_group', group }); }
});
