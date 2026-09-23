import { execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, mkdirSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, sep } from 'node:path';
import { expect, test, type APIRequestContext } from '@playwright/test';
type Row = Record<string, unknown>;
const fixture = { agent: '', task: '', root: '', release: () => {} };
test.afterEach(async ({ request }) => {
  fixture.release();
  if (fixture.agent) { await command(request, { cmd: 'remove_agent', id: fixture.agent }); await command(request, { cmd: 'purge_agent_now', id: fixture.agent }); }
  if (fixture.task) await command(request, { cmd: 'board_remove_task', id: fixture.task });
  if (fixture.root) rmSync(fixture.root, { recursive: true, force: true });
  fixture.agent = ''; fixture.task = ''; fixture.root = '';
});
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
test('worktree dialogs retain failures and recover a lost acknowledgement without repeating Git writes', async ({ page, request }) => {
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime; expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const root = mkdtempSync(join(tmpdir(), 'torque-worktree-ui-')); const project = join(root, 'project'); const hooks = join(root, 'hooks'); mkdirSync(project); mkdirSync(hooks);
  const git = (directory: string, ...args: string[]) => execFileSync('git', ['-C', directory, ...args], { encoding: 'utf8' }).trim();
  const group = `Worktree UI ${Date.now()}`; let release = () => {}; fixture.root = root;
  try {
    git(project, 'init', '-b', 'main'); git(project, 'config', 'user.name', 'Torque QA'); git(project, 'config', 'user.email', 'torque-qa@example.invalid');
    writeFileSync(join(project, '.gitignore'), '.torque/\n'); writeFileSync(join(project, 'sample.txt'), 'baseline\n'); git(project, 'add', '.'); git(project, 'commit', '-m', 'Baseline'); const baseline = git(project, 'rev-parse', 'HEAD');
    await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: project, git_worktree: true, agent_provider: 'generic' } });
    const state = await command(request, { cmd: 'add_agent', group, name: 'Mutation QA worker', provider: 'generic', command: '/bin/cat', directory: project, shell: '/bin/sh', worktree: true });
    const row = Object.values(state.agents as Record<string, Row>).find((value) => value.group === group && value.name === 'Mutation QA worker')!; const agent = String(row.id); fixture.agent = agent; const worktree = realpathSync(String(row.worktree_path)); expect(worktree.startsWith(realpathSync(project) + sep)).toBe(true);
    git(project, 'config', 'core.hooksPath', hooks); const hook = join(hooks, 'pre-commit'); writeFileSync(hook, '#!/bin/sh\necho "QA checkpoint refused" >&2\nexit 1\n', { mode: 0o755 }); writeFileSync(join(worktree, 'sample.txt'), 'first change\n');
    await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_select_agent', id: agent }); await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'agents', controlTab: 'mission' } });
    const writes: Row[] = []; let drop = false; let held = false; let refuseRollback = true;
    const hold = new Promise<void>((resolve) => { release = resolve; fixture.release = resolve; });
    await page.route('**/api/cmd', async (route) => {
      const data = route.request().postDataJSON() as Row;
      if (['worktree_checkpoint', 'worktree_rollback', 'worktree_remove', 'worktree_rebase', 'worktree_merge'].includes(String(data.cmd))) writes.push(data);
      if (data.cmd === 'worktree_checkpoint' && drop) { drop = false; const response = await route.fetch(); expect((await response.json() as Row).ok).toBe(true); held = true; await hold; await route.abort('failed'); return; }
      if (data.cmd === 'worktree_rollback' && refuseRollback) { refuseRollback = false; await route.fulfill({ json: { ok: false, error: 'Injected rollback refusal' } }); return; }
      await route.continue();
    });
    await page.goto('/'); await page.getByRole('button', { name: 'Inspect diff', exact: true }).click(); const dialog = page.getByRole('dialog', { name: 'Mutation QA worker worktree' });
    const draft = dialog.getByRole('textbox', { name: 'Merge message' }); await draft.fill('Retain this operation draft');
    await dialog.getByRole('button', { name: 'Checkpoint', exact: true }).click(); await expect(dialog.getByRole('alert')).toContainText('QA checkpoint refused'); expect(git(worktree, 'rev-parse', 'HEAD')).toBe(baseline); await expect(draft).toHaveValue('Retain this operation draft');
    writeFileSync(hook, '#!/bin/sh\nexit 0\n', { mode: 0o755 }); drop = true;
    await dialog.getByRole('button', { name: 'Checkpoint', exact: true }).click(); await expect.poll(() => held).toBe(true);
    await expect(dialog.getByRole('button', { name: 'Checkpoint', exact: true })).toBeDisabled(); await expect(dialog.getByRole('button', { name: 'Close', exact: true })).toBeDisabled(); await page.keyboard.press('Escape'); await expect(dialog).toBeVisible();
    expect(git(worktree, 'rev-list', '--count', 'HEAD')).toBe('2'); release(); await expect(dialog.getByRole('alert')).toContainText('outcome is unknown');
    writeFileSync(join(worktree, 'sample.txt'), 'unsaved after lost response\n'); await dialog.getByRole('button', { name: 'Retry operation', exact: true }).click(); await expect(dialog.getByText('Checkpoint created', { exact: true })).toBeVisible();
    expect(git(worktree, 'rev-list', '--count', 'HEAD')).toBe('2'); expect(readFileSync(join(worktree, 'sample.txt'), 'utf8')).toBe('unsaved after lost response\n');
    const checkpoints = writes.filter((data) => data.cmd === 'worktree_checkpoint'); expect(checkpoints).toHaveLength(3); expect(checkpoints[2]).toEqual(checkpoints[1]); expect(checkpoints[1]?.idempotency_key).not.toBe(checkpoints[0]?.idempotency_key);
    const rollbackTarget = git(worktree, 'rev-parse', 'HEAD');
    await dialog.getByRole('button', { name: 'Checkpoint', exact: true }).click(); await expect.poll(() => git(worktree, 'rev-list', '--count', 'HEAD')).toBe('3');
    const nav = dialog.getByRole('navigation', { name: 'Worktree views' }); await expect(nav.getByRole('button', { name: 'History 2', exact: true })).toBeVisible(); await nav.getByRole('button', { name: /History/ }).click(); await dialog.getByRole('button', { name: 'Rollback…', exact: true }).click();
    const rollback = page.getByRole('dialog', { name: 'Rollback worktree?' }); await rollback.getByRole('button', { name: 'Rollback', exact: true }).click(); await expect(rollback.getByRole('alert')).toHaveText('Injected rollback refusal'); await expect(rollback).toBeVisible();
    await rollback.getByRole('button', { name: 'Rollback', exact: true }).click(); await expect(rollback).toBeHidden(); expect(git(worktree, 'rev-parse', 'HEAD')).toBe(rollbackTarget); expect(readFileSync(join(worktree, 'sample.txt'), 'utf8')).toBe('first change\n');
    await dialog.getByRole('button', { name: 'Delete worktree…', exact: true }).click(); const removal = page.getByRole('dialog', { name: 'Remove worktree?' }); await expect(removal.getByRole('alert')).toContainText('active/fresh'); await expect(removal.getByRole('button', { name: 'Delete worktree', exact: true })).toBeDisabled(); expect(existsSync(worktree)).toBe(true); await removal.getByRole('button', { name: 'Cancel', exact: true }).click();
    writeFileSync(join(worktree, 'sample.txt'), 'final merge content\n'); await dialog.getByRole('button', { name: 'Checkpoint', exact: true }).click(); await expect(dialog.getByText('Checkpoint created', { exact: true })).toBeVisible();
    writeFileSync(join(project, 'base.txt'), 'advanced main\n'); git(project, 'add', 'base.txt'); git(project, 'commit', '-m', 'Advance base');
    await nav.getByRole('button', { name: 'Refresh', exact: true }).click(); await dialog.getByRole('button', { name: 'Rebase onto base', exact: true }).click(); await expect(dialog.getByText('Worktree rebased.', { exact: true })).toBeVisible(); expect(readFileSync(join(worktree, 'base.txt'), 'utf8')).toBe('advanced main\n');
    await dialog.getByRole('checkbox', { name: 'Force direct local merge' }).check(); await dialog.getByRole('checkbox', { name: 'Close agent after merge' }).check(); await dialog.getByRole('button', { name: 'Create PR & merge', exact: true }).click();
    await expect(dialog.getByRole('alert')).toContainText("engineer_merge_mode='pr' forbids"); await expect(draft).toHaveValue('Retain this operation draft');
    await command(request, { cmd: 'update_group_settings', group, settings: { engineer_merge_mode: 'direct' } });
    fixture.task = String((await command(request, { cmd: 'board_add_task', group, task: 'Merge fixture change', lane: 'In Progress', agent_id: agent })).task_id);
    await dialog.getByRole('button', { name: 'Create PR & merge', exact: true }).click();
    await expect.poll(() => readFileSync(join(project, 'sample.txt'), 'utf8')).toBe('final merge content\n'); await expect(dialog.getByRole('button', { name: 'Close', exact: true })).toBeEnabled(); await expect(draft).toHaveValue('Retain this operation draft');
    await page.screenshot({ animations: 'disabled', path: test.info().outputPath('worktree-mutation-result.png') }); await dialog.getByRole('button', { name: 'Close', exact: true }).click(); await expect(dialog).toBeHidden();
  } finally { release(); }
});
