import { execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, mkdirSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, sep } from 'node:path';
import { expect, test, type APIRequestContext } from '@playwright/test';
type Row = Record<string, unknown>;
const fixture = { root: '', agent: '', release: () => {} };
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
test.afterEach(async ({ request }) => {
  fixture.release();
  if (fixture.agent) { await command(request, { cmd: 'remove_agent', id: fixture.agent }); await command(request, { cmd: 'purge_agent_now', id: fixture.agent }); }
  if (fixture.root) rmSync(fixture.root, { recursive: true, force: true });
  fixture.agent = ''; fixture.root = '';
});
test('toolbar creation confirms session loss, resumes a failed relaunch, and surfaces checkpoint/preflight outcomes', async ({ page, request }) => {
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime; expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const root = mkdtempSync(join(tmpdir(), 'torque-worktree-toolbar-')); fixture.root = root; const project = join(root, 'project'); const hooks = join(root, 'hooks'); mkdirSync(project); mkdirSync(hooks);
  const deniedShell = join(root, 'denied-shell'); writeFileSync(deniedShell, '#!/bin/sh\nexit 1\n', { mode: 0o644 });
  const git = (directory: string, ...args: string[]) => execFileSync('git', ['-C', directory, ...args], { encoding: 'utf8' }).trim();
  const group = `Worktree toolbar ${Date.now()}`;
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: project, git_worktree: false, agent_provider: 'generic', agent_boot_command: '/bin/cat', agent_shell: '/bin/sh' } });
  const state = await command(request, { cmd: 'add_agent', group, name: 'Toolbar QA worker', provider: 'generic', command: '/bin/cat', directory: project, shell: '/bin/sh', worktree: false });
  const row = Object.values(state.agents as Record<string, Row>).find((value) => value.group === group && value.name === 'Toolbar QA worker')!; const agent = String(row.id); fixture.agent = agent; const originalSession = String(row.session_id);
  const stale = await command(request, { cmd: 'worktree_create', id: agent, relaunch: true, expected_session_id: 'wrong-session' }); expect(stale).toMatchObject({ ok: false, created: false, phase: 'session_changed', session_id: originalSession });
  await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_select_agent', id: agent }); await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'agents', controlTab: 'mission' } });
  const writes: Row[] = []; const reads: Row[] = []; let loseCreation = false; let loseRelaunch = false; let held = false; let partial: Row = {}; let completed: Row = {};
  const hold = new Promise<void>((resolve) => { fixture.release = resolve; });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (['worktree_create', 'worktree_checkpoint'].includes(String(data.cmd))) writes.push(data);
    if (['worktree_diff_full', 'worktree_check_merge', 'worktree_history'].includes(String(data.cmd))) reads.push(data);
    if (data.cmd === 'worktree_create' && loseCreation) { loseCreation = false; const response = await route.fetch(); partial = (await response.json() as { data: Row }).data; held = true; await hold; await route.abort('failed'); return; }
    if (data.cmd === 'worktree_create' && data.resume_worktree_path && loseRelaunch) { loseRelaunch = false; const response = await route.fetch(); completed = (await response.json() as { data: Row }).data; await route.abort('failed'); return; }
    await route.continue();
  });
  await page.goto('/'); const focused = page.getByRole('region', { name: 'Focused agent Toolbar QA worker' }); await focused.getByRole('button', { name: 'Create', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Create worktree for Toolbar QA worker?' }); await expect(dialog.getByText(/current conversation will be lost/)).toBeVisible(); expect(writes).toHaveLength(0); await dialog.getByRole('button', { name: 'Cancel', exact: true }).click(); expect(writes).toHaveLength(0);
  await focused.getByRole('button', { name: 'Create', exact: true }).click(); await dialog.getByRole('button', { name: 'Create and restart agent', exact: true }).click(); await expect(dialog.getByRole('alert')).toContainText('not in a Git repository');
  expect(writes).toHaveLength(1); expect(((await command(request, { cmd: 'get_state' })).agents as Record<string, Row>)[agent]?.session_id).toBe(originalSession);
  git(project, 'init', '-b', 'main'); git(project, 'config', 'user.name', 'Torque QA'); git(project, 'config', 'user.email', 'torque-qa@example.invalid'); writeFileSync(join(project, '.gitignore'), '.torque/\n'); writeFileSync(join(project, 'sample.txt'), 'baseline\n'); git(project, 'add', '.'); git(project, 'commit', '-m', 'Baseline');
  await command(request, { cmd: 'update_group_settings', group, settings: { agent_shell: deniedShell } }); loseCreation = true;
  await dialog.getByRole('button', { name: 'Create and restart agent', exact: true }).click(); await expect.poll(() => held).toBe(true); await expect(dialog.getByRole('button', { name: 'Cancel', exact: true })).toBeDisabled(); expect(partial).toMatchObject({ type: 'worktree_create', id: agent, ok: false, created: true, relaunched: false, resume_available: true });
  const worktree = realpathSync(String(partial.worktree_path)); expect(worktree.startsWith(realpathSync(project) + sep)).toBe(true); const listing = git(project, 'worktree', 'list', '--porcelain'); fixture.release(); await expect(dialog.getByRole('alert')).toContainText('outcome is unknown');
  await dialog.getByRole('button', { name: 'Retry operation', exact: true }).click(); await expect(dialog.getByRole('alert')).toContainText('relaunch failed'); await expect(dialog.getByText(worktree, { exact: true })).toBeVisible(); expect(writes[2]).toEqual(writes[1]);
  await command(request, { cmd: 'update_group_settings', group, settings: { agent_shell: '/bin/sh' } }); loseRelaunch = true;
  await dialog.getByRole('button', { name: 'Retry relaunch', exact: true }).click(); await expect(dialog.getByRole('alert')).toContainText('outcome is unknown'); expect(completed).toMatchObject({ type: 'worktree_create', id: agent, ok: true, created: true, relaunched: true }); expect(completed.session_id).not.toBe(originalSession);
  await dialog.getByRole('button', { name: 'Retry operation', exact: true }).click(); await expect(dialog.getByText('Worktree created and agent relaunched', { exact: true })).toBeVisible(); expect(writes[4]).toEqual(writes[3]); expect(writes[3]).toMatchObject({ resume_worktree_path: worktree, expected_session_id: '', relaunch: true }); expect(git(project, 'worktree', 'list', '--porcelain')).toBe(listing);
  expect(((await command(request, { cmd: 'get_state' })).agents as Record<string, Row>)[agent]?.session_id).toBe(completed.session_id);
  await page.screenshot({ animations: 'disabled', path: test.info().outputPath('worktree-created-recovered.png') }); await dialog.getByRole('button', { name: 'Close', exact: true }).click();
  git(project, 'config', 'core.hooksPath', hooks); const hook = join(hooks, 'pre-commit'); writeFileSync(hook, '#!/bin/sh\necho "Toolbar checkpoint refused" >&2\nexit 1\n', { mode: 0o755 }); writeFileSync(join(worktree, 'sample.txt'), 'toolbar checkpoint content\n');
  await focused.getByRole('button', { name: 'Checkpoint', exact: true }).click(); const checkpoint = page.getByRole('dialog', { name: 'Checkpoint Toolbar QA worker' }); await expect(checkpoint.getByRole('alert')).toContainText('Toolbar checkpoint refused'); expect(git(worktree, 'rev-list', '--count', 'HEAD')).toBe('1');
  rmSync(hook); await checkpoint.getByRole('button', { name: 'Retry checkpoint', exact: true }).click(); await expect(checkpoint.getByText('Checkpoint created', { exact: true })).toBeVisible(); expect(git(worktree, 'rev-list', '--count', 'HEAD')).toBe('2'); await checkpoint.getByRole('button', { name: 'Close', exact: true }).click();
  await focused.getByRole('button', { name: 'Checkpoint', exact: true }).click(); await expect(checkpoint.getByText('No changes to checkpoint', { exact: true })).toBeVisible(); await checkpoint.getByRole('button', { name: 'Close', exact: true }).click();
  await focused.getByRole('button', { name: 'Preflight merge', exact: true }).click(); const inspector = page.getByRole('dialog', { name: 'Toolbar QA worker worktree' }); await expect(inspector.getByText('Clean merge', { exact: true })).toBeVisible(); await expect(inspector.getByRole('region', { name: 'Worktree diff files' })).toContainText('toolbar checkpoint content'); expect(reads).toEqual(['worktree_diff_full', 'worktree_check_merge', 'worktree_history'].map((cmd) => ({ cmd, id: agent })));
  expect(existsSync(worktree)).toBe(true); expect(readFileSync(join(worktree, 'sample.txt'), 'utf8')).toBe('toolbar checkpoint content\n'); await inspector.getByRole('button', { name: 'Close', exact: true }).click();
});
