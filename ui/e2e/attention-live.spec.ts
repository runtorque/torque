import { expect, test, type APIRequestContext, type Locator, type Page, type WebSocketRoute } from '@playwright/test';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

type Row = Record<string, unknown>;

async function command(request: APIRequestContext, data: Row): Promise<Row> {
  const response = await request.post('/api/cmd', { data });
  const result = await response.json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true);
  return result.data;
}

test.beforeAll(async ({ request }) => {
  const result = await (await request.get('/api/runtime')).json() as { data: { runtime: { port: number; profile: string } } };
  expect(result.data.runtime.port, 'Use an isolated daemon port').not.toBe(18932);
  expect(result.data.runtime.profile, 'Use an isolated profile').not.toBe('default');
});

async function reconnectHarness(page: Page) {
  let connection: WebSocketRoute | undefined;
  let connections = 0;
  await page.routeWebSocket(/\/ws\?/, (socket) => {
    socket.connectToServer(); // Forward actual daemon snapshots and deltas.
    connection = socket;
    connections += 1;
  });
  return async () => {
    const before = connections;
    await connection!.close({ code: 1012, reason: 'Attention reconnect regression' });
    await expect.poll(() => connections).toBeGreaterThan(before);
  };
}

async function selectCaret(input: Locator) {
  await input.focus();
  await input.evaluate((element: HTMLTextAreaElement) => element.setSelectionRange(2, 7));
}

async function expectCaret(input: Locator, value: string) {
  await expect(input).toHaveValue(value);
  await expect(input).toBeFocused();
  expect(await input.evaluate((element: HTMLTextAreaElement) => [element.selectionStart, element.selectionEnd])).toEqual([2, 7]);
}

async function openControl(page: Page, group: string, tab: string) {
  await page.goto('/');
  await page.getByRole('button', { name: group, exact: true }).click();
  await page.getByRole('button', { name: /◎ Control/ }).click();
  await page.getByRole('button', { name: tab, exact: true }).click();
}

test('live PTY ask retries retain the draft and deliver once to the explicit target', async ({ page, request }) => {
  test.skip(!process.env.TORQUE_ATTENTION_PYTHON, 'Opt in with an absolute Python executable on the disposable daemon host');
  const directory = await mkdtemp(join(tmpdir(), 'torque-attention-e2e-'));
  const log = join(directory, 'received.jsonl');
  const script = join(directory, 'receiver.py');
  await writeFile(log, '');
  await writeFile(script, 'import json,sys\nprint("ATTENTION_READY",flush=True)\nfor line in sys.stdin:\n with open(sys.argv[1],"a") as output: output.write(json.dumps(line)+"\\n")\n print("ATTENTION_RX:"+line.rstrip(),flush=True)\n');
  const quote = (value: string) => "'" + value.replaceAll("'", "'\\''") + "'";
  const group = `Attention delivery ${Date.now()}`;
  let agentId = '';
  try {
    await command(request, { cmd: 'add_group', group });
    await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: directory, git_worktree: false, agent_provider: 'generic', worker_provider: 'generic' } });
    const frame = await command(request, { cmd: 'add_agent', group, name: 'Attention receiver', provider: 'generic', command: [process.env.TORQUE_ATTENTION_PYTHON!, '-u', script, log].map(quote).join(' '), directory, shell: '/bin/sh', worktree: false });
    const agent = Object.values(frame.agents as Record<string, Row>).find((row) => row.group === group)!;
    agentId = agent.id as string;
    expect(agent.session_id).toBeTruthy();
    const parentFrame = await command(request, { cmd: 'board_add_task', group, task: 'Parent without a reply session', lane: 'In Progress' });
    const parent = { id: parentFrame.task_id };
    const askFrame = await command(request, { cmd: 'board_add_task', group, task: 'Choose attention gate', lane: 'Backlog', labels: ['torque:human'], parent_task_id: parent.id });
    const ask = { id: askFrame.task_id };
    await command(request, { cmd: 'board_update_task', id: ask.id, reply_agent_id: agentId });
    const reconnect = await reconnectHarness(page);
    await openControl(page, group, 'Activity');
    const section = page.getByRole('region', { name: 'Response to Choose attention gate' });
    const answer = section.getByRole('textbox');
    const marker = `ATTENTION_DELIVER_${Date.now()}`;
    await answer.fill(marker);
    await selectCaret(answer);
    await command(request, { cmd: 'board_update_task', id: ask.id, description: 'Updated context from the daemon' });
    await expect(section).toContainText('Updated context from the daemon');
    await expectCaret(answer, marker);
    await reconnect();
    await expect(section.getByRole('button', { name: 'Resolve ask', exact: true })).toBeEnabled();
    await expectCaret(answer, marker);
    let failed = false;
    const outcomes: boolean[] = [];
    await page.route('**/api/cmd', async (route) => {
      const payload = route.request().postDataJSON() as Row;
      if (payload.cmd !== 'resolve_ask') { await route.continue(); return; }
      if (!failed) { failed = true; await route.abort('connectionreset'); return; }
      // Race the actual browser retry with a second client's HTTP request.
      const [browserReply, otherReply] = await Promise.all([
        route.fetch(),
        request.post('/api/cmd', { data: { ...payload, request_id: 'other-client' } }),
      ]);
      outcomes.push((await browserReply.json() as { ok: boolean }).ok, (await otherReply.json() as { ok: boolean }).ok);
      await route.fulfill({ response: browserReply });
    });
    await section.getByRole('button', { name: 'Resolve ask', exact: true }).click();
    await expect(section.getByRole('alert')).toBeVisible();
    await expect(answer).toHaveValue(marker); await expect(answer).toHaveAttribute('readonly', '');
    await expect(section.getByRole('button', { name: 'Resolve ask', exact: true })).toBeDisabled(); expect(outcomes).toHaveLength(0);
    await section.getByRole('button', { name: 'Refresh question', exact: true }).click();
    await expect(section.getByRole('button', { name: 'Resolve ask', exact: true })).toBeEnabled();
    await answer.press('Enter');
    await expect.poll(() => [...outcomes].sort()).toEqual([false, true]);
    await expect.poll(async () => (await readFile(log, 'utf8')).split(marker).length - 1).toBe(1);
    await expect(section).toHaveCount(0);
    const detail = await command(request, { cmd: 'task_detail', id: ask.id });
    expect(detail.task).toMatchObject({ lane: 'Done', reply_agent_id: agentId });
    expect((detail.task as Row).messages).toEqual(expect.arrayContaining([expect.objectContaining({ action: 'ask_reply', message: marker })]));
    const replay = await (await request.post('/api/cmd', { data: { cmd: 'resolve_ask', id: ask.id, answer: marker } })).json() as { ok: boolean };
    expect(replay.ok).toBe(false);
    expect((await readFile(log, 'utf8')).split(marker).length - 1).toBe(1);
    await page.screenshot({ path: test.info().outputPath('attention-delivered.png'), fullPage: true });
  } finally {
    if (agentId) await command(request, { cmd: 'remove_agent', id: agentId });
    await rm(directory, { recursive: true, force: true });
  }
});

test('live behavior review preserves notes on reconnect and stale approval, then rejects safely', async ({ page, request }) => {
  const group = `Attention review ${Date.now()}`;
  await command(request, { cmd: 'add_group', group });
  const scope = { scope_kind: 'role', scope_group: group, scope_key: 'worker', group };
  const propose = (text: string, rationale: string) => command(request, { cmd: 'behavior_overlay_propose', ...scope, proposed_by_kind: 'user', proposed_by_agent_id: 'user', text, rationale });
  const firstRationale = `First reviewed proposal · ${group}`;
  const staleRationale = `Competing reviewed proposal · ${group}`;
  const first = await propose('Use focused tests before broad checks.', firstRationale);
  const stale = await propose('Include manual evidence in the report.', staleRationale);
  const reconnect = await reconnectHarness(page);
  await openControl(page, group, 'Catalog');
  await page.getByRole('button', { name: 'Refresh proposals', exact: true }).click();
  const review = async (rationale: string) => {
    await page.locator('article').filter({ hasText: rationale }).getByRole('button', { name: 'Review behavior diff' }).click();
    const modal = page.getByRole('dialog', { name: 'Review behavior diff' });
    await expect(modal.getByLabel('Behavior diff')).toBeVisible();
    return modal;
  };
  let modal = await review(firstRationale);
  await modal.getByLabel('Review note').fill('Approved after inspecting the diff');
  await modal.getByRole('button', { name: 'Approve behavior change' }).click();
  await expect(modal.getByText('Behavior change approved.')).toBeVisible();
  await expect(modal.getByText('applied · next none')).toBeVisible();
  let active = await command(request, { cmd: 'behavior_overlay_read', ...scope });
  expect(active.text).toBe('Use focused tests before broad checks.');
  expect((await command(request, { cmd: 'behavior_overlay_diff', proposal_id: first.proposal_id })).proposal).toMatchObject({ status: 'applied' });
  await modal.getByRole('button', { name: 'Close review' }).click();
  modal = await review(staleRationale);
  const note = modal.getByLabel('Review note');
  const draft = 'Keep this review note through reconnect';
  await note.fill(draft); await selectCaret(note);
  await reconnect();
  await expect(modal.getByLabel('Behavior diff')).toBeVisible();
  await expectCaret(note, draft);
  await modal.getByRole('button', { name: 'Approve behavior change' }).click();
  await expect(modal.getByRole('alert')).toContainText(/stale|base version/i);
  await expect(note).toHaveValue(draft);
  await expect(modal.getByRole('button', { name: 'Approve behavior change' })).toBeDisabled();
  await modal.getByRole('button', { name: 'Reload diff' }).click();
  await expect(modal.getByLabel('Behavior diff')).toContainText('Include manual evidence');
  await expect(note).toHaveValue(draft);
  await modal.getByRole('button', { name: 'Reject behavior change' }).click();
  await expect(modal.getByText('Behavior change rejected.')).toBeVisible();
  await expect(modal.getByText('rejected · next none')).toBeVisible();
  const rejected = await command(request, { cmd: 'behavior_overlay_diff', proposal_id: stale.proposal_id });
  expect(rejected.proposal).toMatchObject({ status: 'rejected', resolution_note: draft });
  active = await command(request, { cmd: 'behavior_overlay_read', ...scope });
  expect(active.text).toBe('Use focused tests before broad checks.');
  await page.screenshot({ path: test.info().outputPath('attention-review.png'), fullPage: true });
});

test('behavior review recovers from real read and decision deadlines without replaying an applied approval', async ({ page, request }) => {
  test.setTimeout(90_000);
  const group = `Review deadlines ${Date.now()}`; await command(request, { cmd: 'add_group', group });
  const scope = { scope_kind: 'role', scope_group: group, scope_key: 'worker', group };
  const proposed = await command(request, { cmd: 'behavior_overlay_propose', ...scope, proposed_by_kind: 'user', proposed_by_agent_id: 'user', text: 'Persist one reviewed approval.', rationale: 'Deadline review fixture' });
  await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'catalog' } });
  const reconnect = await reconnectHarness(page); let firstRead = true; let heldRead = false; let releaseRead = () => {}; let heldDecision = false; let releaseDecision = () => {}; let decisionReads = 0; const writes: Row[] = [];
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd === 'behavior_overlay_diff' && data.proposal_id === proposed.proposal_id) { decisionReads++; if (firstRead) { firstRead = false; heldRead = true; await new Promise<void>((resolve) => { releaseRead = resolve; }); } }
    if (data.cmd === 'behavior_overlay_user_approve') { writes.push(data); const response = await route.fetch(); heldDecision = true; await new Promise<void>((resolve) => { releaseDecision = resolve; }); await route.fulfill({ response }).catch(() => {}); return; }
    await route.continue().catch(() => {});
  });
  try {
    await page.goto('/'); await page.getByRole('button', { name: 'Refresh proposals', exact: true }).click(); const card = page.locator('article').filter({ hasText: 'Deadline review fixture' }); await expect(card.getByText('Set text', { exact: true })).toBeVisible(); await expect(card.getByText('user', { exact: true })).toBeVisible(); await card.getByRole('button', { name: 'Review behavior diff', exact: true }).click();
    const modal = page.getByRole('dialog', { name: 'Review behavior diff', exact: true }); const note = modal.getByRole('textbox', { name: 'Review note', exact: true }); const approve = modal.getByRole('button', { name: 'Approve behavior change', exact: true });
    await expect.poll(() => heldRead).toBe(true); await note.fill('Keep this note through both deadlines'); await selectCaret(note); await expect(modal.getByRole('alert')).toContainText('refresh timed out', { timeout: 20_000 }); await expectCaret(note, 'Keep this note through both deadlines'); await expect(approve).toBeDisabled(); await modal.getByRole('button', { name: 'Reload diff', exact: true }).click(); await expect(approve).toBeEnabled(); releaseRead(); await expect(modal.getByLabel('Behavior diff', { exact: true })).toContainText('+Persist one reviewed approval.');
    await approve.click(); await expect.poll(() => heldDecision).toBe(true); await expect(note).toBeDisabled(); const count = decisionReads; await reconnect(); expect(decisionReads).toBe(count); expect(writes).toHaveLength(1); expect((await command(request, { cmd: 'behavior_overlay_read', ...scope })).text).toBe('Persist one reviewed approval.');
    await expect(modal.getByRole('alert')).toContainText('outcome is unknown', { timeout: 35_000 }); await expect(note).toHaveValue('Keep this note through both deadlines'); await expect(note).toBeEnabled(); await expect(approve).toBeDisabled(); await expect(modal.getByLabel('Behavior diff', { exact: true })).toContainText('+Persist one reviewed approval.'); await expect(modal.getByRole('button', { name: 'Close review', exact: true })).toBeEnabled(); releaseDecision(); await expect(modal.getByText('Behavior change approved.', { exact: true })).toHaveCount(0);
    await page.screenshot({ path: test.info().outputPath('behavior-decision-unknown.png') }); await page.setViewportSize({ width: 390, height: 844 }); await modal.getByRole('button', { name: 'Reload diff', exact: true }).scrollIntoViewIfNeeded(); expect(await modal.evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true); await page.screenshot({ path: test.info().outputPath('behavior-decision-unknown-narrow.png') });
    await modal.getByRole('button', { name: 'Reload diff', exact: true }).click(); await expect(modal.getByText('applied · next none', { exact: true })).toBeVisible(); await expect(modal.getByText('This proposal is not awaiting an operator decision.', { exact: true })).toBeVisible(); await expect(approve).toBeDisabled(); expect(writes).toHaveLength(1); expect((await command(request, { cmd: 'behavior_overlay_diff', proposal_id: proposed.proposal_id })).proposal).toMatchObject({ status: 'applied', resolution_note: 'Keep this note through both deadlines' });
  } finally { releaseRead(); releaseDecision(); }
});
