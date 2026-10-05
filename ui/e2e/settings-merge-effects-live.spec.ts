import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
type Row = Record<string, unknown>;
const git = (directory: string, ...args: string[]) => execFileSync('git', ['-C', directory, ...args], { encoding: 'utf8' }).trim();
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}

test('Saved merge modes, squash, preserved diff and every cleanup option drive real local merges', async ({ page, request }) => {
  test.setTimeout(180_000); page.setDefaultTimeout(10_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'torque-merge-settings-')));
  git(root, 'init', '-b', 'main'); git(root, 'config', 'user.name', 'Torque QA'); git(root, 'config', 'user.email', 'torque-qa@example.invalid');
  writeFileSync(join(root, '.gitignore'), '.torque/\n'); writeFileSync(join(root, 'work.txt'), 'baseline\n'); git(root, 'add', '.'); git(root, 'commit', '-m', 'Merge baseline');
  const group = `Merge settings ${Date.now()}`; const ids: string[] = []; const tasks: string[] = []; const evidence: Row[] = []; const writes: Row[] = []; const merges: Row[] = [];
  let socket: WebSocketRoute | undefined; let connections = 0; let refuse = true; let refuseMerge = true;
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: root, agent_provider: 'generic', agent_boot_command: '/bin/cat', git_worktree: true, worktree_auto_checkpoint: false, checkpoint_on_progress: false, notifications: false } });
  await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'settings' } });
  await page.routeWebSocket(/\/ws\?/, (client) => { socket = client; connections++; client.connectToServer(); });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd === 'update_group_settings') { writes.push(data); if (refuse) { refuse = false; await route.fulfill({ json: { ok: false, error: 'QA merge settings refused' } }); return; } }
    if (data.cmd === 'worktree_merge') { merges.push(data); if (refuseMerge) { refuseMerge = false; await route.fulfill({ json: { ok: true, data: { type: 'worktree_merge', id: data.id, ok: false, error: 'QA merge refused' } } }); return; } } await route.continue();
  });
  const settings = async () => {
    await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click();
    const summary = page.getByText(`${group} execution, worktrees, notifications and sync`, { exact: true }); if (!(await summary.evaluate((node) => node.closest('details')?.open))) await summary.click();
  };
  const save = async () => { await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible(); };
  const cell = async (id: string) => ((await command(request, { cmd: 'get_state' })).agents as Record<string, Row>)[id]!;
  try {
    await page.goto('/');
    const cases = [['keep', false, false, true, false], ['close', true, false, false, false], ['remove', false, true, true, false], ['remove', false, true, false, true], ['close_remove', true, true, false, false], ['auto_sweep', true, true, true, false]] as const;
    for (const [index, [cleanup, close, remove, squash, stopped]] of cases.entries()) {
      await settings(); const mode = index === cases.length - 1 ? 'engineer-choice' : 'direct'; const preserve = index % 2 === 0;
      await page.getByRole('combobox', { name: 'Merge mode', exact: true }).selectOption(mode);
      await page.getByRole('combobox', { name: 'Worktree merge squash', exact: true }).selectOption(String(squash));
      await page.getByRole('combobox', { name: 'Worktree merge preserve diff', exact: true }).selectOption(String(preserve));
      const input = page.getByRole('combobox', { name: 'Worktree merge cleanup', exact: true }); await input.selectOption(cleanup); await input.focus();
      const before = connections; await socket!.close({ code: 1012, reason: 'Merge policy draft' }); await expect.poll(() => connections).toBeGreaterThan(before); await expect(input).toBeFocused(); await expect(input).toHaveValue(cleanup);
      if (index === 0) { await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByRole('alert')).toContainText('QA merge settings refused'); }
      await save(); if (index === 0) expect(writes[1]).toEqual(writes[0]);
      const configured = { engineer_merge_mode: mode, worktree_merge_cleanup: cleanup, worktree_merge_squash: squash, worktree_merge_preserve_diff: preserve };
      expect((await command(request, { cmd: 'get_group_settings', group })).settings).toMatchObject(configured);
      await page.reload(); await settings();
      for (const [label, value] of [['Merge mode', mode], ['Worktree merge cleanup', cleanup], ['Worktree merge squash', String(squash)], ['Worktree merge preserve diff', String(preserve)]]) await expect(page.getByRole('combobox', { name: label!, exact: true })).toHaveValue(value!);
      const name = `Merge worker ${cleanup}${stopped ? ' stopped' : ''}`; const id = String((await command(request, { cmd: 'add_worker', group, name })).id); ids.push(id);
      const initial = await cell(id); expect(initial.worktree_merge_squash).toBe(squash); const worktree = String(initial.worktree_path); const baseline = git(root, 'rev-parse', 'HEAD');
      const taskId = String((await command(request, { cmd: 'board_add_task', group, task: `Merge fixture ${cleanup}`, lane: 'In Progress', agent_id: id })).task_id); tasks.push(taskId);
      for (const version of [1, 2]) { writeFileSync(join(worktree, 'work.txt'), `${cleanup} version ${version}\n`); git(worktree, 'add', 'work.txt'); git(worktree, 'commit', '-m', `${cleanup} commit ${version}`); }
      const workerHead = git(worktree, 'rev-parse', 'HEAD');
      await command(request, { cmd: 'ai_report', cell_id: id, task_id: taskId, action: 'done', message: `Prepared ${cleanup} merge fixture`, terminal_declaration: 'No further work is needed; I will not derive after this.' });
      expect(((await command(request, { cmd: 'task_detail', id: taskId })).task as Row).worktree_boundary).toMatchObject({ status: 'open', commit_sha: workerHead });
      if (stopped) { await command(request, { cmd: 'remove_agent', id }); await command(request, { cmd: 'restore_agent', id }); await expect.poll(async () => (await cell(id)).status).toBe('stopped'); }
      if (index === 0) {
        // Verify the PR-only policy locally: its refusal happens before any
        // external GitHub operation. Actual PR hosting has separate coverage.
        await page.getByRole('combobox', { name: 'Merge mode', exact: true }).selectOption('pr'); await save(); await page.reload(); await settings(); await expect(page.getByRole('combobox', { name: 'Merge mode', exact: true })).toHaveValue('pr');
        const refused = await command(request, { cmd: 'worktree_merge', id, force_direct: true }); expect(refused).toMatchObject({ ok: false, code: 'force_direct_disallowed' }); expect(git(root, 'rev-parse', 'HEAD')).toBe(baseline);
        await page.getByRole('combobox', { name: 'Merge mode', exact: true }).selectOption('direct'); await save();
      }
      await command(request, { cmd: 'ui_select_agent', id }); await page.getByRole('button', { name: /⌁ Agents/ }).click(); await page.getByRole('button', { name: 'Inspect diff', exact: true }).click();
      const inspector = page.getByRole('dialog', { name: `${name} worktree` });
      for (const [label, expected] of [['Close agent after merge', close], ['Delete worktree after merge', remove], ['Preserve boundary diff', preserve]] as const) { const box = inspector.getByRole('checkbox', { name: label, exact: true }); if (expected) await expect(box).toBeChecked(); else await expect(box).not.toBeChecked(); }
      if (mode === 'engineer-choice') await inspector.getByRole('checkbox', { name: 'Force direct local merge' }).check();
      const attribution = inspector.getByRole('combobox', { name: 'Merge task', exact: true }); await attribution.selectOption(taskId);
      if (index === 0) {
        await attribution.focus(); await attribution.evaluate((node) => node.setAttribute('data-retained', 'yes')); const before = connections;
        await socket!.close({ code: 1012, reason: 'Released merge attribution reconnect' }); await expect.poll(() => connections).toBeGreaterThan(before);
        await expect(attribution).toHaveValue(taskId); await expect(attribution).toBeFocused(); await expect(attribution).toHaveAttribute('data-retained', 'yes');
        await inspector.getByRole('button', { name: 'Create PR & merge', exact: true }).click(); await expect(inspector.getByRole('alert')).toContainText('QA merge refused'); await expect(attribution).toHaveValue(taskId);
        await page.screenshot({ path: test.info().outputPath('released-task-selection.png') });
      }
      await inspector.getByRole('textbox', { name: 'Merge message', exact: true }).fill(`Settings merge ${cleanup}`);
      const merged = page.waitForResponse((response) => response.url().endsWith('/api/cmd') && (response.request().postDataJSON() as Row).cmd === 'worktree_merge');
      await inspector.getByRole('button', { name: 'Create PR & merge', exact: true }).click(); const response = await (await merged).json() as { ok: boolean; data: Row }; expect(response.ok).toBe(true); expect(response.data.ok, JSON.stringify(response.data)).toBe(true);
      expect(merges.at(-1)).toMatchObject({ id, merge_task_id: taskId, close_agent_on_merge: close, remove_worktree_on_merge: remove, preserve_merge_diff: preserve });
      if (mode === 'direct') expect(merges.at(-1)).not.toHaveProperty('force_direct'); else expect(merges.at(-1)?.force_direct).toBe(true);
      const mergeHead = git(root, 'rev-parse', 'HEAD'); expect(mergeHead).not.toBe(baseline); expect(readFileSync(join(root, 'work.txt'), 'utf8')).toBe(`${cleanup} version 2\n`);
      const parents = git(root, 'show', '-s', '--format=%P', 'HEAD').split(' '); expect(parents).toEqual(squash ? [baseline] : [baseline, workerHead]); expect(Number(git(root, 'rev-list', '--count', `${baseline}..HEAD`))).toBe(squash ? 1 : 3);
      const removed = remove && (close || stopped); expect(response.data.cleanup).toMatchObject({ close_agent: close, remove_worktree: remove, agent_closed: close, worktree_removed: removed });
      if (remove && !removed) { expect((response.data.cleanup as Row).errors).toEqual([expect.stringContaining('attached session')]); await expect(inspector.getByText(/Cleanup needs attention:/)).toContainText('attached session'); }
      else if (removed && squash) {
        // Direct local cleanup does not carry verified origin-merge evidence.
        // Keep the non-ancestral branch while removing its worktree.
        expect((response.data.cleanup as Row).local_branch_cleanup).toMatchObject({ branch_deleted: false, reason: 'merge_commit_missing', origin_verified: false });
        expect((response.data.cleanup as Row).errors).toEqual(['Worktree removed, but branch deletion did not complete', `Worktree removal mismatch for '${name}': branch_delete_failed`]);
        expect(git(root, 'rev-parse', `refs/heads/${String(initial.worktree_branch)}`)).toBe(workerHead); await expect(inspector.getByText(/Cleanup needs attention:/)).toContainText('branch deletion');
      } else expect((response.data.cleanup as Row).errors).toEqual([]);
      expect(existsSync(worktree)).toBe(!removed); const final = await cell(id); expect(Boolean(final.deleted_at)).toBe(close);
      if (removed && !close) { expect(final.worktree_path).toBe(''); expect(realpathSync(String(final.directory))).toBe(root); expect(final.status).toBe('stopped'); }
      if (remove && !removed) { expect(final.worktree_path).toBe(worktree); expect(final.session_id).toBeTruthy(); }
      const task = (await command(request, { cmd: 'task_detail', id: taskId })).task as Row; const artifacts = (task.artifacts as Row[]).filter((artifact) => (artifact.metadata as Row | undefined)?.preserved_on_merge);
      expect(artifacts).toHaveLength(preserve ? 1 : 0); if (preserve) { expect(artifacts[0]!.metadata).toMatchObject({ merge_commit_sha: mergeHead, boundary_task_id: taskId }); expect(readFileSync(String(artifacts[0]!.path), 'utf8')).toContain(`+${cleanup} version 2`); }
      evidence.push({ configured, stopped, baseline, workerHead, mergeHead, parents, result: response.data, deleted: Boolean(final.deleted_at), artifacts });
      await expect(inspector.getByRole('button', { name: 'Close', exact: true })).toBeEnabled(); if (index === cases.length - 1) await page.screenshot({ path: test.info().outputPath('merge-settings-result.png') }); await inspector.getByRole('button', { name: 'Close', exact: true }).click();
      if (!close) await command(request, { cmd: 'remove_agent', id }); await command(request, { cmd: 'purge_agent_now', id }); ids.pop();
    }
    const path = test.info().outputPath('merge-settings-evidence.json'); writeFileSync(path, JSON.stringify({ writes, merges, evidence }, null, 2)); await test.info().attach('merge-settings-evidence', { path, contentType: 'application/json' });
  } finally {
    for (const id of ids) { if (!(await cell(id)).deleted_at) await command(request, { cmd: 'remove_agent', id }); await command(request, { cmd: 'purge_agent_now', id }); }
    for (const id of tasks) await command(request, { cmd: 'board_remove_task', id }); rmSync(root, { recursive: true, force: true });
  }
});
