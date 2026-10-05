import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
type Row = Record<string, unknown>;
const git = (directory: string, ...args: string[]) => execFileSync('git', ['-C', directory, ...args], { encoding: 'utf8' }).trim();
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}

test('Saved checkpoint switches independently gate real progress and end-of-turn commits', async ({ page, request }) => {
  test.setTimeout(120_000);
  test.skip(!process.env.TORQUE_CHECKPOINT_EVENT_QA, 'Requires a disposable daemon with TORQUE_PROFILE_ENABLED=1');
  page.setDefaultTimeout(10_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'torque-checkpoint-settings-')));
  git(root, 'init', '-b', 'main'); git(root, 'config', 'user.name', 'Torque QA'); git(root, 'config', 'user.email', 'torque-qa@example.invalid');
  writeFileSync(join(root, '.gitignore'), '.torque/\n'); writeFileSync(join(root, 'work.txt'), 'baseline\n'); git(root, 'add', '.'); git(root, 'commit', '-m', 'Checkpoint baseline');
  const group = `Checkpoint settings ${Date.now()}`; const ids: string[] = []; const tasks: string[] = []; const evidence: Row[] = []; const events: Row[] = []; const writes: Row[] = [];
  let socket: WebSocketRoute | undefined; let connections = 0;
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: root, agent_provider: 'generic', agent_boot_command: '/bin/cat', git_worktree: false, notifications: false } });
  await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'settings' } });
  await page.routeWebSocket(/\/ws\?/, (client) => {
    socket = client; connections++; const server = client.connectToServer();
    server.onMessage((message) => { const frame = JSON.parse(String(message)) as Row; if (frame.type === 'delta' && Array.isArray(frame.ops)) events.push(...(frame.ops as Row[]).filter((op) => op.op === 'event_append')); client.send(message); });
  });
  await page.route('**/api/cmd', async (route) => { const data = route.request().postDataJSON() as Row; if (data.cmd === 'update_group_settings') writes.push(data); await route.continue(); });
  const settings = async () => {
    await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click();
    const summary = page.getByText(`${group} execution, worktrees, notifications and sync`, { exact: true }); if (!(await summary.evaluate((node) => node.closest('details')?.open))) await summary.click();
  };
  const cell = async (id: string) => ((await command(request, { cmd: 'get_state' })).agents as Record<string, Row>)[id]!;
  try {
    await page.goto('/');
    for (const [auto, progress] of [[false, false], [false, true], [true, false], [true, true]] as const) {
      const label = `${Number(auto)}${Number(progress)}`; await settings();
      await page.getByRole('combobox', { name: 'Worktrees', exact: true }).selectOption('on');
      await page.getByRole('combobox', { name: 'Worktree auto checkpoint', exact: true }).selectOption(String(auto));
      const input = page.getByRole('combobox', { name: 'Checkpoint on progress', exact: true }); await input.selectOption(String(progress)); await input.focus();
      const before = connections; await socket!.close({ code: 1012, reason: 'Checkpoint policy draft' }); await expect.poll(() => connections).toBeGreaterThan(before); await expect(input).toBeFocused(); await expect(input).toHaveValue(String(progress));
      await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible();
      expect((await command(request, { cmd: 'get_group_settings', group })).settings).toMatchObject({ git_worktree: true, worktree_auto_checkpoint: auto, checkpoint_on_progress: progress });
      await page.reload(); await settings(); await expect(page.getByRole('combobox', { name: 'Worktree auto checkpoint', exact: true })).toHaveValue(String(auto)); await expect(page.getByRole('combobox', { name: 'Checkpoint on progress', exact: true })).toHaveValue(String(progress));
      const id = String((await command(request, { cmd: 'add_worker', group, name: `Checkpoint ${label}` })).id); ids.push(id);
      const initial = await cell(id); expect(initial).toMatchObject({ worktree_auto_checkpoint: auto, checkpoint_on_progress: progress }); const worktree = String(initial.worktree_path); expect(worktree).not.toBe('');
      const taskId = String((await command(request, { cmd: 'board_add_task', group, task: `Checkpoint task ${label}`, lane: 'In Progress', agent_id: id })).task_id); tasks.push(taskId);
      const baseline = git(worktree, 'rev-parse', 'HEAD'); const marker = `${group}-${label}`;
      writeFileSync(join(worktree, 'work.txt'), `first progress ${label}\n`);
      await command(request, { cmd: 'ai_report', cell_id: id, task_id: taskId, action: 'progress', message: `${marker}-first` });
      const firstHead = git(worktree, 'rev-parse', 'HEAD'); expect(firstHead !== baseline).toBe(progress);
      expect((await cell(id)).worktree_checkpoints).toBe(Number(progress));
      if (progress) { expect(git(worktree, 'show', 'HEAD:work.txt')).toBe(`first progress ${label}`); expect(git(worktree, 'log', '-1', '--format=%B')).toContain(`${marker}-first`); }
      writeFileSync(join(worktree, 'work.txt'), `second progress ${label}\n`);
      await command(request, { cmd: 'ai_report', cell_id: id, task_id: taskId, action: 'progress', message: `${marker}-second` });
      expect(git(worktree, 'rev-parse', 'HEAD')).toBe(firstHead); expect(git(worktree, 'status', '--porcelain')).toContain('work.txt');
      await command(request, { cmd: 'user_agent_message', agent_id: id, message: `Begin completion ${marker}`, idempotency_key: marker }); await expect.poll(async () => (await cell(id)).status).toBe('running');
      for (const [event_type, data] of [['session_end', { summary: `${marker}-end` }], ['waiting', { reason: `${marker}-barrier` }]] as const) {
        const response = await request.post('/events', { headers: { 'X-Torque-Cell-Id': id }, data: { source: 'torque-profile-harness', event_id: `${marker}-${event_type}`, event_type, data } }); expect(response.status()).toBe(200);
      }
      // The drainer awaits each event's callbacks before consuming the next.
      // This second event proves end-of-turn checkpointing has finished even
      // when the expected result is no new commit.
      await expect.poll(() => events.some((event) => event.cell_id === id && event.message === `${marker}-barrier`)).toBe(true);
      const finalHead = git(worktree, 'rev-parse', 'HEAD'); expect(finalHead !== firstHead).toBe(auto);
      const final = await cell(id); expect(final.worktree_checkpoints).toBe(Number(progress) + Number(auto));
      if (auto) { expect(git(worktree, 'show', 'HEAD:work.txt')).toBe(`second progress ${label}`); expect(git(worktree, 'status', '--porcelain')).toBe(''); }
      else expect(git(worktree, 'status', '--porcelain')).toContain('work.txt');
      evidence.push({ auto, progress, baseline, firstHead, finalHead, checkpoints: final.worktree_checkpoints, status: git(worktree, 'status', '--porcelain'), log: git(worktree, 'log', '--format=%H %s', `${baseline}..HEAD`) });
      await command(request, { cmd: 'remove_agent', id }); await command(request, { cmd: 'purge_agent_now', id }); ids.pop();
    }
    await page.getByRole('combobox', { name: 'Checkpoint on progress', exact: true }).scrollIntoViewIfNeeded(); await page.screenshot({ path: test.info().outputPath('checkpoint-settings.png') });
    const path = test.info().outputPath('checkpoint-settings-evidence.json'); writeFileSync(path, JSON.stringify({ writes, evidence }, null, 2)); await test.info().attach('checkpoint-settings-evidence', { path, contentType: 'application/json' });
  } finally {
    for (const id of ids) { await command(request, { cmd: 'remove_agent', id }); await command(request, { cmd: 'purge_agent_now', id }); }
    for (const id of tasks) await command(request, { cmd: 'board_remove_task', id }); rmSync(root, { recursive: true, force: true });
  }
});
