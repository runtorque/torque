import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import { compactStateFixture } from '../src/protocol/fixtures';

test.beforeAll(async ({ request }) => {
  const response = await request.get('/api/runtime');
  const payload = await response.json() as { data: { runtime: { port: number; profile: string } } };
  expect(payload.data.runtime.port, 'Use an isolated daemon port').not.toBe(18932);
  expect(payload.data.runtime.profile, 'Use an isolated profile').not.toBe('default');
});

async function command(request: APIRequestContext, payload: Record<string, unknown>) {
  const response = await request.post('/api/cmd', { data: payload });
  const result = await response.json() as { ok: boolean; error?: string; data: Record<string, unknown> };
  expect(result.ok, result.error).toBe(true); return result.data;
}

async function fixtureWorkspace(page: Page, desktop = false, extra: Record<string, unknown> = {}) {
  const sent: Record<string, unknown>[] = [];
  const messages = Array.from({ length: 65 }, (_, index) => ({ id: `m-${index}`, message: `Peer message ${index}`, sender_id: 'e', sender_name: 'Evan', recipient_id: 'other', recipient_name: 'Other Engineer', context: { summary: 'Review evidence', task_ids: ['task-1'] } }));
  const frame = { ...compactStateFixture, active_group: 'Foundation', selected_agent_id: 'w', groups: { Foundation: ['a', 'e', 'w'] }, agents: { a: { id: 'a', name: 'Aria', kind: 'architect', group: 'Foundation', cell_type: 'agent' }, e: { id: 'e', name: 'Evan', kind: 'engineer', group: 'Foundation', hired_by_architect_id: 'a', cell_type: 'agent' }, w: { id: 'w', name: 'Wren', kind: 'worker', group: 'Foundation', owner_engineer_id: 'e', cell_type: 'agent' } }, agent_peer_threads: { recent: { thread_id: 'recent', title: 'Engineering review', messages, last_activity_at: 200 }, other: { thread_id: 'other', title: 'Other group discussion', messages: [], last_activity_at: 100 } }, ...extra };
  if (desktop) await page.addInitScript(() => {
    const target = window as Window & { __TAURI_INTERNALS__?: unknown; parityNativeCalls?: { command: string; args: unknown }[] };
    target.parityNativeCalls = [];
    target.__TAURI_INTERNALS__ = { invoke: (command: string, args: unknown) => { target.parityNativeCalls!.push({ command, args }); return Promise.resolve(command === 'detach' ? 'agents-parity' : command === 'list_detached' ? [] : null); } };
  });
  await page.routeWebSocket(/\/ws\?/, (socket) => {
    let seq = 10;
    socket.send(JSON.stringify(frame));
    socket.onMessage((raw) => {
      const payload = JSON.parse(String(raw)) as Record<string, unknown>; sent.push(payload);
      if (payload.cmd === 'ui_set_detached_panels') socket.send(JSON.stringify({ type: 'delta', seq: ++seq, ops: [{ op: 'ui_update', key: 'detached_panels', value: payload.detached_panels }] }));
      else socket.send(JSON.stringify({ type: 'ok' }));
    });
  });
  await page.goto('/'); await expect(page.getByText('connected', { exact: true })).toBeVisible();
  return sent;
}

test('Agents hierarchy, Live/Activity and both resize handles preserve composer draft', async ({ page }) => {
  await fixtureWorkspace(page);
  await page.getByRole('button', { name: /⌁ Agents/ }).click();
  await expect(page.getByRole('treeitem', { name: /Wren, worker/ })).toHaveAttribute('aria-level', '3');
  const composer = page.getByRole('textbox', { name: /Message Wren/ });
  await composer.fill('Keep this draft');
  const split = page.getByRole('separator', { name: 'Resize terminal and direct messages' });
  await split.focus(); const before = Number(await split.getAttribute('aria-valuenow'));
  await page.keyboard.press('ArrowUp');
  await expect.poll(async () => Number(await split.getAttribute('aria-valuenow'))).not.toBe(before);
  const composeSplit = page.getByRole('separator', { name: 'Resize message text box' });
  await composeSplit.focus(); await page.keyboard.press('ArrowUp');
  await page.getByRole('tab', { name: 'Activity', exact: true }).click();
  await expect(page.getByRole('tree', { name: 'Agent ownership hierarchy' })).toBeVisible();
  await page.getByRole('tab', { name: 'Live', exact: true }).click();
  await expect(composer).toHaveValue('Keep this draft');
  await page.getByRole('button', { name: 'Collapse Evan' }).click();
  await expect(page.getByRole('treeitem', { name: /Wren, worker/ })).toHaveCount(0);
});

test('aggregate Chat includes other groups and progressively reveals message context', async ({ page }) => {
  await fixtureWorkspace(page);
  await page.getByRole('button', { name: /◎ Control/ }).click();
  await page.getByRole('button', { name: 'Chat', exact: true }).click();
  await expect(page.getByRole('button', { name: /Other group discussion/ })).toBeVisible();
  await expect(page.getByText('Peer message 0', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: /Load older messages/ }).click();
  await expect(page.getByText('Peer message 0', { exact: true })).toBeVisible();
  const message = page.getByText('Peer message 0', { exact: true }).locator('..');
  await message.getByText('Context details').click();
  await expect(message.getByText('task-1')).toBeVisible();
  await expect(page.getByRole('button', { name: /^Send/ })).toHaveCount(0);
});

test('Control Center reads real bounded logs and supports target/filter/follow controls', async ({ page, request }) => {
  const logs = await request.get('/logs?target=daemon&limit=5');
  expect(logs.ok()).toBe(true);
  const payload = await logs.json() as { lines: unknown[]; target: string };
  expect(payload.target).toBe('daemon'); expect(payload.lines.length).toBeLessThanOrEqual(5);
  await page.route('**/logs?*', (route) => route.fulfill({ json: { target: new URL(route.request().url()).searchParams.get('target'), cursor: 5, lines: [{ level: 'INFO', message: 'Ready for QA' }, { level: 'ERROR', message: 'Synthetic error for filter QA' }] } }));
  await page.goto('/');
  await page.getByRole('button', { name: /◎ Control/ }).click();
  await page.getByRole('button', { name: 'Logs', exact: true }).click();
  await expect(page.getByText('Ready for QA')).toBeVisible();
  await page.getByRole('combobox', { name: 'Level', exact: true }).selectOption('ERROR');
  await expect(page.getByText('Ready for QA')).toHaveCount(0);
  await page.getByLabel('Follow', { exact: true }).uncheck();
  await page.getByLabel('Log target').selectOption('supervisor');
  await expect(page.getByRole('log', { name: 'supervisor log lines' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Reveal log folder' })).toHaveCount(0);
  await page.screenshot({ path: test.info().outputPath('parity-logs.png'), fullPage: true });
});

test('pipeline discovery renders a live action graph and opens its editor', async ({ page, request }) => {
  const group = `Pipelines ${Date.now()}`;
  await command(request, { cmd: 'add_group', group });
  await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: '/private/tmp/torque-react-parity-20260921/pipeline-project' } });
  for (const [name, transitions] of [['parity/build', [{ action: 'parity/review', when: 'Ready for review' }]], ['parity/review', [{ action: 'parity/build', when: 'Changes needed' }, { ask: true, when: 'Approval required' }]]] as const) await command(request, { cmd: 'save_action', group, name, scope: 'project', action: { prompt: '{{ TASK }}', description: name, transitions } });
  await page.goto('/'); await page.getByRole('button', { name: group, exact: true }).click();
  await page.getByRole('button', { name: /◎ Control/ }).click();
  await page.getByRole('button', { name: 'Pipelines', exact: true }).click();
  await page.getByRole('combobox', { name: 'Pipeline', exact: true }).selectOption({ label: 'parity/build' });
  const graph = page.getByRole('group', { name: 'Pipeline graph' }); await expect(graph).toBeVisible();
  await page.getByRole('button', { name: 'Zoom in pipeline' }).click(); await expect(page.getByText('125%')).toBeVisible();
  await page.getByRole('button', { name: 'Fit pipeline' }).click();
  await page.screenshot({ path: test.info().outputPath('parity-pipeline.png'), fullPage: true });
  await graph.getByRole('button', { name: 'Edit action parity/review' }).click();
  await expect(page.getByLabel('Name', { exact: true })).toHaveValue('parity/review');
  await expect(page.getByRole('textbox', { name: 'Prompt', exact: true })).toHaveValue('{{ TASK }}');
});

test('Planning creates, edits and links an initiative against the isolated daemon', async ({ page, request }) => {
  const group = `Planning ${Date.now()}`;
  await command(request, { cmd: 'add_group', group });
  await command(request, { cmd: 'board_add_task', group, task: 'Linked parity task', lane: 'Backlog' });
  await page.goto('/'); await page.getByRole('button', { name: group, exact: true }).click();
  await page.getByRole('button', { name: /◇ Planning/ }).click();
  await page.getByRole('button', { name: '＋ New', exact: true }).click();
  await page.getByLabel('Title', { exact: true }).fill('Parity initiative');
  await page.getByRole('textbox', { name: 'Summary', exact: true }).fill('Before edit');
  await page.getByRole('button', { name: 'Create', exact: true }).click();
  await page.getByRole('button', { name: /Parity initiative/ }).click();
  await page.getByRole('textbox', { name: 'Summary', exact: true }).fill('Edited through Planning');
  await page.getByLabel('Linked record').selectOption({ label: 'Linked parity task' });
  await page.getByRole('button', { name: 'Link', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Unlink', exact: true })).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Summary', exact: true })).toHaveValue('Edited through Planning');
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await page.getByRole('button', { name: /Parity initiative/ }).click();
  await expect(page.getByRole('textbox', { name: 'Summary', exact: true })).toHaveValue('Edited through Planning');
  await expect(page.getByRole('button', { name: 'Unlink', exact: true })).toBeVisible();
});

test('Tauri host boundary transfers detach ownership and reattaches through the adapter', async ({ page }) => {
  const sent = await fixtureWorkspace(page, true);
  await page.getByRole('button', { name: /⌁ Agents/ }).click();
  await page.getByRole('button', { name: 'Detach Agents workspace', exact: true }).click();
  await expect(page.getByText('Agents workspace detached', { exact: true })).toBeVisible();
  await expect(page.getByRole('tree', { name: 'Agent ownership hierarchy' })).toBeHidden();
  await page.getByRole('button', { name: 'Reattach workspace' }).click();
  await expect(page.getByRole('tree', { name: 'Agent ownership hierarchy' })).toBeVisible();
  const calls = await page.evaluate(() => (window as Window & { parityNativeCalls?: { command: string; args: unknown }[] }).parityNativeCalls || []);
  expect(calls).toContainEqual({ command: 'reattach', args: { label: 'agents-parity' } });
  expect(sent.filter((item) => item.cmd === 'ui_set_detached_panels')).toHaveLength(2);
});


test('attention review gates approval on a fetched diff and retains a rejected reply', async ({ page }) => {
  const ask = { id: 'ask', task: 'Choose release', group: 'Foundation', lane: 'Backlog', labels: ['torque:human'], reply_agent_id: 'w', parent_task_id: 'parent', description: 'Options: ship or wait. Recommended: wait.' };
  const approval = { ...ask, id: 'approval', task: 'Behavior approval', labels: ['torque:human', 'behavior-overlay-approval', 'proposal:proposal'] };
  const parent = { id: 'parent', task: 'Release parent', agent_id: 'w', description: 'Release context' };
  const requests: Record<string, unknown>[] = [];
  await page.route('**/api/cmd', async (route) => {
    const command = route.request().postDataJSON() as Record<string, unknown>; requests.push(command);
    if (command.cmd === 'resolve_ask') { await route.fulfill({ json: { ok: false, error: 'Synthetic delivery failure' } }); return; }
    const data = command.cmd === 'ui_set_react_workspace_state' ? { type: 'react_workspace_state', state: command.state }
      : command.cmd === 'task_detail' ? { type: 'task_detail', id: command.id, task: command.id === 'ask' ? ask : command.id === 'approval' ? approval : parent }
      : command.cmd === 'behavior_overlay_diff' ? { type: 'behavior_overlay_diff', proposal: { id: 'proposal', status: 'approved', next_actor_kind: 'user', proposed_text_sha256: 'reviewed-hash', base_version_id: 'base', rationale: 'Bounded change' }, diff: '-old rule\n+new rule' }
        : command.cmd === 'behavior_overlay_user_reject' ? { type: 'behavior_overlay_proposal', proposal_id: 'proposal', proposal: { id: 'proposal', status: 'rejected', proposed_text_sha256: 'reviewed-hash', base_version_id: 'base' } } : { type: 'ok' };
    await route.fulfill({ json: { ok: true, data } });
  });
  await fixtureWorkspace(page, false, { agents: { w: { id: 'w', name: 'Wren', group: 'Foundation', kind: 'worker', cell_type: 'agent', session_id: 'fixture-session', status: 'running' } }, board_tasks: { ask, approval, parent } });
  await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Activity', exact: true }).click();
  const answer = page.getByRole('textbox', { name: 'Answer Choose release' }); await answer.fill('Wait for review');
  await page.getByRole('button', { name: 'Resolve ask' }).click(); await expect(page.getByRole('alert')).toContainText('Synthetic delivery failure'); await expect(answer).toHaveValue('Wait for review');
  const review = page.getByRole('button', { name: 'Review behavior diff', exact: true }); await review.click();
  const modal = page.getByRole('dialog', { name: 'Review behavior diff' }); await expect(modal.getByLabel('Behavior diff')).toContainText('+new rule');
  await modal.getByLabel('Review note').fill('Needs revision'); await modal.getByRole('button', { name: 'Reject behavior change' }).click();
  await expect(modal.getByText('Behavior change rejected.')).toBeVisible();
  expect(requests.find((command) => command.cmd === 'behavior_overlay_user_reject')).toMatchObject({ expected_proposed_text_sha256: 'reviewed-hash', expected_base_version_id: 'base', note: 'Needs revision' });
  await modal.getByRole('button', { name: 'Close review' }).click(); await expect(answer).toHaveValue('Wait for review');
});

test('Area lifecycle, initiative/Area links and note edit/archive persist on the daemon', async ({ page, request }) => {
  const group = `Areas ${Date.now()}`; await command(request, { cmd: 'add_group', group });
  const initiative = await command(request, { cmd: 'initiative_create', group, title: 'Area initiative' });
  const linkedArea = await command(request, { cmd: 'area_create', group, title: 'Related Area' });
  const initiativeId = (initiative.initiative as Record<string, unknown>).id as string;
  const linkedAreaId = (linkedArea.area as Record<string, unknown>).id as string;
  await page.goto('/'); await page.getByRole('button', { name: group, exact: true }).click(); await page.getByRole('button', { name: /◇ Planning/ }).click();
  await page.getByRole('button', { name: 'Areas', exact: true }).click(); await page.getByRole('button', { name: '＋ New', exact: true }).click();
  await page.getByLabel('Title', { exact: true }).fill('Area parity'); await page.getByRole('button', { name: 'Create', exact: true }).click();
  await page.getByRole('button', { name: /Area parity/ }).click();
  const modal = page.getByRole('dialog', { name: 'Area', exact: true }); await modal.getByLabel('Lifecycle').selectOption('stable');
  await modal.getByLabel('Summary', { exact: true }).fill('Preserve this Area draft');
  await modal.getByLabel('Relationship type').selectOption('initiative'); await modal.getByLabel('Relationship target').selectOption(initiativeId); await modal.getByRole('button', { name: 'Link', exact: true }).click();
  await expect(modal.getByRole('button', { name: `Unlink initiative ${initiativeId}` })).toBeVisible();
  await modal.getByLabel('Relationship type').selectOption('area'); await modal.getByLabel('Relationship target').selectOption(linkedAreaId); await modal.getByLabel('Relationship label').selectOption('depends_on'); await modal.getByRole('button', { name: 'Link', exact: true }).click();
  await expect(modal.getByRole('button', { name: `Unlink area ${linkedAreaId}` })).toBeVisible();
  await modal.getByLabel('Note title').fill('Constraint'); await modal.getByLabel('Note body').fill('Initial detail'); await modal.getByLabel('Note type').selectOption('invariant');
  await modal.getByLabel('Note target type').selectOption('initiative'); await modal.getByLabel('Note target', { exact: true }).selectOption(initiativeId); await modal.getByRole('button', { name: 'Add note', exact: true }).click();
  await modal.getByRole('button', { name: 'Edit note Constraint' }).click(); await modal.getByLabel('Note body').fill('Edited detail'); await modal.getByRole('button', { name: 'Save note', exact: true }).click(); await expect(modal.getByText('Edited detail', { exact: true })).toBeVisible();
  await expect(modal.getByLabel('Summary', { exact: true })).toHaveValue('Preserve this Area draft'); await modal.getByRole('button', { name: 'Save', exact: true }).click();
  await page.getByRole('button', { name: /Area parity/ }).click(); await expect(modal.getByLabel('Lifecycle')).toHaveValue('stable'); await expect(modal.getByText('Edited detail', { exact: true })).toBeVisible();
  await modal.getByRole('button', { name: 'Archive note Constraint' }).click(); await expect(modal.getByRole('button', { name: 'Edit note Constraint' })).toHaveCount(0);
  await modal.getByRole('button', { name: `Unlink area ${linkedAreaId}` }).click(); await expect(modal.getByRole('button', { name: `Unlink area ${linkedAreaId}` })).toHaveCount(0);
  await page.screenshot({ path: test.info().outputPath('parity-area.png'), fullPage: true });
});

test('settings save sparse typed edits and stage daemon-default resets before persistence', async ({ page, request }) => {
  const group = `Settings ${Date.now()}`;
  await command(request, { cmd: 'add_group', group });
  await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'update_group_settings', group, settings: { max_agents: 3, worker_provider: 'codex', board_sync_github: {} } });
  await page.goto('/');
  await page.getByRole('button', { name: /◎ Control/ }).click();
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  const maximum = page.getByRole('spinbutton', { name: 'Maximum agents', exact: true });
  await expect(maximum).toHaveValue('3');
  await maximum.fill('7');
  await page.getByText(`${group} execution, worktrees, notifications and sync`, { exact: true }).click();
  await page.getByLabel('Board sync github: Github close issues via pr', { exact: true }).selectOption('false');
  await page.getByText('Engineer behavior defaults', { exact: true }).click();
  await page.getByLabel('Restrict to created agents', { exact: true }).selectOption('true');
  await page.getByLabel('Push interval', { exact: true }).selectOption('120');
  // An external update after hydration must survive an unrelated form save.
  await command(request, { cmd: 'update_group_settings', group, settings: { agent_model: 'external-model' } });
  await page.getByRole('button', { name: 'Save changes', exact: true }).click();
  await expect(page.getByText('Saved', { exact: true })).toBeVisible();
  const saved = await command(request, { cmd: 'get_group_settings', group });
  expect(saved.settings).toMatchObject({ max_agents: 7, agent_model: 'external-model', worker_provider: 'codex', board_sync_github: { github_close_issues_via_pr: false } });
  expect(saved.engineer_settings).toMatchObject({ restrict_to_created_agents: true, push_interval: 120 });
  await page.getByRole('button', { name: 'Reset group defaults', exact: true }).click();
  await page.getByRole('button', { name: 'Reset Engineer defaults', exact: true }).click();
  await expect(maximum).toHaveValue('0');
  expect((await command(request, { cmd: 'get_group_settings', group })).settings).toMatchObject({ max_agents: 7 });
  await page.getByRole('button', { name: 'Save changes', exact: true }).click();
  await expect(page.getByText('Saved', { exact: true })).toBeVisible();
  await page.reload();
  await page.getByRole('button', { name: /◎ Control/ }).click();
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await expect(page.getByRole('spinbutton', { name: 'Maximum agents', exact: true })).toHaveValue('0');
  const reset = await command(request, { cmd: 'get_group_settings', group });
  expect(reset.settings).toMatchObject({ max_agents: 0, agent_model: '', worker_provider: '', board_sync_github: {} });
  expect(reset.engineer_settings).toMatchObject({ restrict_to_created_agents: false, push_interval: 60 });
  await page.screenshot({ path: test.info().outputPath('parity-settings.png'), fullPage: true });
});

test('sidebar overflow keeps the final group clickable above its footer', async ({ page }) => {
  const names = Array.from({ length: 25 }, (_, index) => `Overflow ${String(index).padStart(2, '0')}`);
  const sent = await fixtureWorkspace(page, false, { groups: Object.fromEntries(names.map((name) => [name, []])), group_order: names, active_group: names[0] });
  const last = page.getByRole('button', { name: names.at(-1)!, exact: true });
  await last.click({ timeout: 5000 });
  await expect.poll(() => sent.some((command) => command.cmd === 'ui_select_group' && command.group === names.at(-1))).toBe(true);
  const bounds = await last.boundingBox();
  const footer = await page.getByText('connected', { exact: true }).boundingBox();
  expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(footer!.y);
  await last.focus(); await last.press('Enter'); await expect(last).toBeFocused();
});
