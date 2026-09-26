import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}
test('Attention question and delivery deadlines retain reviewed answers and recover through an explicit same-answer retry', async ({ page, request }) => {
  test.setTimeout(110_000); test.skip(!process.env.TORQUE_ATTENTION_PYTHON, 'Requires an explicit disposable generic PTY receiver');
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime; expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const directory = await mkdtemp(join(tmpdir(), 'torque-attention-recovery-')); const log = join(directory, 'received.jsonl'); const script = join(directory, 'receiver.py'); const group = `Attention recovery ${Date.now()}`;
  await writeFile(log, ''); await writeFile(script, 'import json,sys\nprint("ATTENTION_READY",flush=True)\nfor line in sys.stdin:\n with open(sys.argv[1],"a") as output: output.write(json.dumps(line)+"\\n")\n print("ATTENTION_RX:"+line.rstrip(),flush=True)\n');
  const quote = (value: string) => "'" + value.replaceAll("'", "'\\''") + "'";
  let agentId = ''; let releaseRead = () => {}; let releaseWrite = () => {}; const writes: Row[] = []; let socket: WebSocketRoute | undefined; let connections = 0;
  try {
    await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: directory, git_worktree: false, agent_provider: 'generic', worker_provider: 'generic' } });
    const frame = await command(request, { cmd: 'add_agent', group, name: 'Recovery receiver', provider: 'generic', command: [process.env.TORQUE_ATTENTION_PYTHON!, '-u', script, log].map(quote).join(' '), directory, shell: '/bin/sh', worktree: false });
    const agent = Object.values(frame.agents as Record<string, Row>).find((item) => item.group === group)!; agentId = String(agent.id); expect(agent.session_id).toBeTruthy();
    const parentId = String((await command(request, { cmd: 'board_add_task', group, task: 'Reviewed parent', description: 'Full parent context', lane: 'In Progress' })).task_id);
    const askId = String((await command(request, { cmd: 'board_add_task', group, task: 'Choose recovery path', description: 'Original question context', labels: ['torque:human'], parent_task_id: parentId })).task_id);
    await command(request, { cmd: 'board_update_task', id: askId, reply_agent_id: agentId }); await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'activity' } });
    let holdRead = true; let holdWrite = true; let readHeld = false; let writeHeld = false; const reads: Row[] = [];
    await page.routeWebSocket(/\/ws\?/, (route) => { route.connectToServer(); socket = route; connections++; });
    await page.route('**/api/cmd', async (route) => {
      const data = route.request().postDataJSON() as Row;
      if (data.cmd === 'task_detail') { reads.push(data); if (data.id === askId && holdRead) { holdRead = false; readHeld = true; await new Promise<void>((resolve) => { releaseRead = resolve; }); await route.fulfill({ json: { ok: true, data: { type: 'task_detail', id: askId, task: { id: askId, description: 'Obsolete question', lane: 'Done', labels: [] } } } }); return; } }
      if (data.cmd === 'resolve_ask') { writes.push(data); if (holdWrite) { holdWrite = false; writeHeld = true; await new Promise<void>((resolve) => { releaseWrite = resolve; }); await route.fulfill({ json: { ok: true, data: { type: 'ok', command: 'resolve_ask', task_id: askId, request_id: data.request_id } } }); return; } }
      await route.continue();
    });
    await page.goto('/'); const section = page.getByRole('region', { name: 'Response to Choose recovery path', exact: true }); const answer = section.getByRole('textbox'); const marker = `RECOVERED_ANSWER_${Date.now()}`;
    await expect.poll(() => readHeld).toBe(true); await answer.fill(marker); await answer.focus(); await answer.evaluate((node: HTMLTextAreaElement) => { node.dataset.owner = 'answer'; node.setSelectionRange(2, 8); });
    await expect(section.getByRole('alert')).toContainText('timed out', { timeout: 20_000 }); await expect(answer).toHaveValue(marker); await expect(answer).toBeFocused(); expect(await answer.evaluate((node: HTMLTextAreaElement) => [node.selectionStart, node.selectionEnd])).toEqual([2, 8]);
    await section.getByRole('button', { name: 'Refresh question', exact: true }).click(); await expect(section.getByRole('button', { name: 'Resolve ask', exact: true })).toBeEnabled(); releaseRead(); await expect(section).toContainText('Original question context');
    await section.getByText('Parent: Reviewed parent', { exact: true }).click(); await expect(section).toContainText('Full parent context');
    await section.getByRole('button', { name: 'Resolve ask', exact: true }).click(); await expect.poll(() => writeHeld).toBe(true); await expect(section.getByRole('alert')).toContainText('outcome is unknown', { timeout: 35_000 });
    await expect(answer).toHaveValue(marker); await expect(answer).toHaveAttribute('readonly', ''); await expect(answer).toHaveAttribute('data-owner', 'answer'); await expect(section.getByRole('button', { name: 'Resolve ask', exact: true })).toBeDisabled(); await expect(section.getByRole('button', { name: 'Refresh question', exact: true })).toBeEnabled();
    releaseWrite(); await expect(section).toBeVisible(); await expect(section.getByText('Answer delivered.', { exact: true })).toHaveCount(0); expect(await readFile(log, 'utf8')).not.toContain(marker);
    await page.setViewportSize({ width: 760, height: 650 }); await section.scrollIntoViewIfNeeded(); await page.screenshot({ path: test.info().outputPath('attention-unknown-delivery.png') });
    const before = connections; await socket!.close({ code: 1012, reason: 'Review unknown answer' }); await expect.poll(() => connections).toBeGreaterThan(before); await expect(section.getByRole('button', { name: 'Resolve ask', exact: true })).toBeEnabled(); expect(writes).toHaveLength(1); await expect(answer).toHaveValue(marker);
    await section.getByRole('button', { name: 'Resolve ask', exact: true }).click(); await expect(section).toHaveCount(0); expect(writes).toHaveLength(2); expect(writes[1]).toEqual(writes[0]);
    await expect.poll(async () => (await readFile(log, 'utf8')).split(marker).length - 1).toBe(1); const saved = (await command(request, { cmd: 'task_detail', id: askId })).task as Row; expect(saved.lane).toBe('Done'); expect((saved.messages as Row[]).filter((row) => row.message === marker)).toHaveLength(1);
    const count = reads.length; const connected = connections; await socket!.close({ code: 1012, reason: 'Closed question must stay quiet' }); await expect.poll(() => connections).toBeGreaterThan(connected); await expect(page.getByText('connected', { exact: true })).toBeVisible(); expect(reads).toHaveLength(count); expect(writes).toHaveLength(2);
  } finally { releaseRead(); releaseWrite(); if (agentId) await command(request, { cmd: 'remove_agent', id: agentId }); await rm(directory, { recursive: true, force: true }); }
});
