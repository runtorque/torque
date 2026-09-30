import { expect, test, type APIRequestContext, type Page, type WebSocketRoute } from '@playwright/test';
import { chmod, mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const response = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(response.ok, response.error).toBe(true); expect(response.data.type).not.toBe('error'); return response.data;
}
async function setup(page: Page, request: APIRequestContext, prefix: string) {
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `${prefix} ${Date.now()}`;
  await command(request, { cmd: 'add_group', group });
  await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: '/private/tmp', default_agent_template: '', agent_provider: 'generic', agent_boot_command: '/bin/cat', git_worktree: false, notifications: false, agent_idle_timeout: 5 } });
  await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'settings' } });
  await page.goto('/'); return group;
}
async function setting(page: Page, label: string) {
  await page.getByRole('button', { name: /◎ Control/ }).click();
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByRole('searchbox', { name: 'Search settings' }).fill(label);
  await page.getByRole('button', { name: new RegExp(`^${label} — `) }).click();
  return page.getByLabel(label, { exact: true });
}
async function save(page: Page) {
  await page.getByRole('button', { name: 'Save changes', exact: true }).click();
  await expect(page.getByText('Saved', { exact: true })).toBeVisible();
}
async function evidence(name: string, rows: Row[]) {
  const path = test.info().outputPath(`${name}.json`); await writeFile(path, JSON.stringify(rows, null, 2));
  await test.info().attach(name, { path, contentType: 'application/json' });
}

test('Group behavior approval persists and governs Architect-only versus separate operator approval', async ({ page, request }) => {
  test.setTimeout(90_000); page.setDefaultTimeout(12_000);
  const group = await setup(page, request, 'Behavior approval policy'); const ids: string[] = []; const results: Row[] = [];
  const label = 'Engineer behavior requires user approval';
  try {
    const architect = String((await command(request, { cmd: 'add_architect', group, name: 'Policy Architect' })).id); ids.push(architect);
    const engineer = String((await command(request, { cmd: 'add_engineer', group, name: 'Policy Engineer', hired_by_architect_id: architect })).id); ids.push(engineer);
    for (const required of [false, true]) {
      const input = await setting(page, label);
      if (await input.inputValue() === String(required)) { await input.selectOption(String(!required)); await save(page); }
      await input.selectOption(String(required)); await save(page); await page.reload();
      await expect(await setting(page, label)).toHaveValue(String(required));
      expect((await command(request, { cmd: 'get_group_settings', group })).settings).toMatchObject({ engineer_behavior_requires_user_approval: required });
      const original = (await command(request, { cmd: 'behavior_overlay_read', group, agent_id: engineer, seed: true })).text;
      const instructions = `Policy requires user approval: ${String(required)}.`;
      const proposed = await command(request, { cmd: 'behavior_overlay_propose', group, agent_id: engineer, proposed_by_agent_id: engineer, proposed_by_kind: 'engineer', text: instructions, rationale: instructions });
      expect(proposed).toMatchObject({ approval_route: required ? 'architect_then_user' : 'architect', status: 'proposed', next_actor_kind: 'architect' });
      // Changing the setting does not rewrite the approval contract of an existing proposal.
      await (await setting(page, label)).selectOption(String(!required)); await save(page);
      const endorsed = await command(request, { cmd: 'behavior_overlay_architect_approve', proposal_id: proposed.proposal_id, architect_id: architect });
      expect(endorsed.proposal).toMatchObject({ approval_route: required ? 'architect_then_user' : 'architect', status: required ? 'approved' : 'applied', next_actor_kind: required ? 'user' : '' });
      const afterArchitect = await command(request, { cmd: 'behavior_overlay_read', group, agent_id: engineer });
      expect(afterArchitect.text).toBe(required ? original : instructions);
      await page.getByRole('button', { name: 'Catalog', exact: true }).click();
      const region = page.getByRole('region', { name: 'Dynamic Behavior', exact: true });
      await region.getByRole('combobox', { name: 'Behavior scope', exact: true }).selectOption('agent');
      await region.getByRole('combobox', { name: 'Behavior target', exact: true }).selectOption(engineer);
      await expect(region.getByRole('textbox', { name: 'Behavior instructions', exact: true })).toHaveValue(required ? original as string : instructions);
      if (required) {
        const card = region.locator('article').filter({ hasText: instructions });
        await card.getByRole('button', { name: 'Review behavior diff', exact: true }).click();
        const dialog = page.getByRole('dialog', { name: 'Review behavior diff', exact: true });
        await expect(dialog.getByLabel('Behavior diff', { exact: true })).toContainText(`+${instructions}`);
        await dialog.getByRole('button', { name: 'Approve behavior change', exact: true }).click();
        await expect(dialog.getByText('Behavior change approved.', { exact: true })).toBeVisible();
        await dialog.getByRole('button', { name: 'Close review', exact: true }).click();
      }
      await page.reload();
      const final = await command(request, { cmd: 'behavior_overlay_read', group, agent_id: engineer });
      expect(final.text).toBe(instructions); expect(final.version).toMatchObject({ approver_kind: required ? 'user' : 'architect' });
      results.push({ required, proposed, endorsed, afterArchitect, final });
    }
    await evidence('behavior-approval-policy', results);
  } finally { for (const id of ids.reverse()) await command(request, { cmd: 'remove_agent', id }); }
});

test('Saved idle timeout flags a silent running agent while zero disables the real health alert', async ({ page, request }) => {
  test.setTimeout(160_000); page.setDefaultTimeout(12_000);
  test.skip(!process.env.TORQUE_RUNTIME_POLICY_QA, 'Requires a disposable PTY-enabled daemon with TORQUE_PROFILE_ENABLED=1');
  let socket: WebSocketRoute | undefined; let connections = 0;
  await page.routeWebSocket(/\/ws\?/, (client) => { socket = client; connections++; client.connectToServer(); });
  const group = await setup(page, request, 'Idle timeout policy'); const ids: string[] = []; const results: Row[] = [];
  const cell = async (id: string) => ((await command(request, { cmd: 'get_state' })).agents as Record<string, Row>)[id]!;
  try {
    for (const minutes of [0, 1]) {
      const input = await setting(page, 'Agent idle timeout'); await input.fill(String(minutes)); await input.focus();
      const before = connections; await socket!.close({ code: 1012, reason: 'Idle timeout draft' });
      await expect.poll(() => connections).toBeGreaterThan(before); await expect(input).toHaveValue(String(minutes)); await expect(input).toBeFocused();
      await save(page); await page.reload(); await expect(await setting(page, 'Agent idle timeout')).toHaveValue(String(minutes));
      expect((await command(request, { cmd: 'get_group_settings', group })).settings).toMatchObject({ agent_idle_timeout: minutes });
      const id = String((await command(request, { cmd: 'add_worker', group, name: `Idle ${minutes}` })).id); ids.push(id);
      await command(request, { cmd: 'user_agent_message', agent_id: id, message: `Start idle policy ${minutes}`, idempotency_key: `${group}-${minutes}` });
      await expect.poll(async () => (await cell(id)).status).toBe('running');
      const response = await request.post('/events', { headers: { 'X-Torque-Cell-Id': id }, data: { source: 'torque-profile-harness', event_id: `${group}-${minutes}-tool`, event_type: 'tool_start', data: { tool: 'idle-policy-probe', detail: `idle policy ${minutes}` } } });
      expect(response.status()).toBe(200);
      await expect.poll(async () => (await cell(id)).activity).toBe('tool_call');
      results.push({ minutes, id, initial: await cell(id), armedAt: Date.now() });
    }
    await page.getByRole('button', { name: /⌁ Agents/ }).click();
    // The actual 30-second health loop must cross the one-minute threshold.
    // Both agents were armed before this wait; the zero case has been silent longer.
    await expect.poll(async () => (await cell(ids[1]!)).needs_attention, { timeout: 100_000, intervals: [1_000] }).toBe(true);
    const enabled = await cell(ids[1]!); const disabled = await cell(ids[0]!);
    expect(enabled).toMatchObject({ status: 'running', needs_attention: true, error_message: 'No activity for 1 minute' });
    expect(disabled).toMatchObject({ status: 'running', needs_attention: false, error_message: '' });
    const row = page.locator(`[role="treeitem"][data-agent-id="${ids[1]!}"]`);
    await expect(row.getByText('attention', { exact: true })).toBeVisible();
    await expect(page.locator(`[role="treeitem"][data-agent-id="${ids[0]!}"]`).getByText('attention', { exact: true })).toHaveCount(0);
    results.push({ observedAt: Date.now(), enabled, disabled });
    await page.screenshot({ path: test.info().outputPath('idle-timeout-attention.png'), animations: 'disabled' });
    await evidence('idle-timeout-policy', results);
  } finally { for (const id of ids.reverse()) await command(request, { cmd: 'remove_agent', id }); }
});


test('Session resume setting drives actual provider adapter relaunch arguments in both directions', async ({ page, request }) => {
  test.setTimeout(90_000); page.setDefaultTimeout(12_000);
  test.skip(!process.env.TORQUE_RUNTIME_POLICY_QA || !process.env.TORQUE_PTY_PYTHON, 'Requires a disposable event harness and explicit local Python');
  const directory = await realpath(await mkdtemp(join(tmpdir(), 'torque-session-policy-')));
  const executable = join(directory, 'codex');
  await writeFile(executable, `#!${process.env.TORQUE_PTY_PYTHON!}\n${await readFile(fileURLToPath(new URL('./fixtures/provider_resume_probe.py', import.meta.url)), 'utf8')}`); await chmod(executable, 0o700);
  const events: Row[] = []; const ids: string[] = []; const results: Row[] = [];
  await page.routeWebSocket(/\/ws\?/, (client) => {
    const server = client.connectToServer(); server.onMessage((message) => {
      const frame = JSON.parse(String(message)) as Row;
      if (frame.type === 'delta' && Array.isArray(frame.ops)) events.push(...(frame.ops as Row[]).filter((op) => op.op === 'event_append'));
      client.send(message);
    });
  });
  const group = await setup(page, request, 'Session resume policy');
  const cell = async (id: string) => ((await command(request, { cmd: 'get_state' })).agents as Record<string, Row>)[id]!;
  try {
    await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: directory, agent_provider: 'codex', agent_boot_command: executable, agent_idle_timeout: 0 } });
    await page.reload();
    // The process is an inert local recorder named codex, exercising Torque's
    // real adapter/config/shim without contacting any provider service.
    const id = String((await command(request, { cmd: 'add_worker', group, name: 'Resume recorder' })).id); ids.push(id);
    const launches = async () => { try { return (await readFile(join(directory, `${id}.jsonl`), 'utf8')).trim().split('\n').map((line) => JSON.parse(line) as Row); } catch { return []; } };
    await expect.poll(async () => (await launches()).length).toBe(1);
    expect((await launches())[0]).toMatchObject({ resume: false, sessions: [], directory });
    const sessionId = `qa-session-${Date.now()}`;
    for (const [event_type, data] of [['session_start', { session_id: sessionId }], ['waiting', { reason: sessionId }]] as const) {
      const response = await request.post('/events', { headers: { 'X-Torque-Cell-Id': id }, data: { source: 'torque-profile-harness', event_id: `${sessionId}-${event_type}`, event_type, data } }); expect(response.status()).toBe(200);
    }
    await expect.poll(() => events.some((event) => event.cell_id === id && event.message === sessionId)).toBe(true);
    for (const enabled of [false, true, false]) {
      await (await setting(page, 'Agent session resume')).selectOption(String(enabled)); await save(page); await page.reload();
      await expect(await setting(page, 'Agent session resume')).toHaveValue(String(enabled));
      expect((await command(request, { cmd: 'get_group_settings', group })).settings).toMatchObject({ agent_session_resume: enabled });
      const count = (await launches()).length;
      // Normal delete/restore stops the PTY and restores the durable record
      // without resetting its provider session, unlike Restart/Clear context.
      await command(request, { cmd: 'remove_agent', id }); await command(request, { cmd: 'restore_agent', id });
      await expect.poll(async () => (await cell(id)).status).toBe('stopped');
      await page.getByRole('button', { name: /⌁ Agents/ }).click();
      await page.locator(`[role="treeitem"][data-agent-id="${id}"]`).getByRole('button', { name: 'Actions for Resume recorder', exact: true }).click();
      await page.getByRole('menuitem', { name: 'Relaunch', exact: true }).click();
      await expect.poll(async () => (await launches()).length).toBe(count + 1);
      const process = (await launches()).at(-1)!;
      expect(process).toMatchObject({ resume: enabled, sessions: enabled ? [sessionId] : [], directory }); results.push({ enabled, process });
    }
    await evidence('session-resume-policy', results);
  } finally { for (const id of ids) await command(request, { cmd: 'remove_agent', id }); await rm(directory, { recursive: true, force: true }); }
});
