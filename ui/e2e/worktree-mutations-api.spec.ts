import { execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, mkdirSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, sep } from 'node:path';
import { expect, test, type APIRequestContext } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const response = await request.post('/api/cmd', { data }); const body = await response.json() as { ok: boolean; error?: string; data: Row }; expect(body.ok, body.error).toBe(true); return body.data;
}
const quote = (value: string) => `'${value.replaceAll("'", "'\\''")}'`;
test('worktree writes acknowledge Git outcomes and overlapping keyed retries execute once', async ({ request }) => {
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime; expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const root = mkdtempSync(join(tmpdir(), 'torque-worktree-ack-')); const project = join(root, 'project'); const hooks = join(root, 'hooks'); mkdirSync(project); mkdirSync(hooks);
  const git = (directory: string, ...args: string[]) => execFileSync('git', ['-C', directory, ...args], { encoding: 'utf8' }).trim();
  const group = `Worktree acknowledgement ${Date.now()}`; let agent = '';
  try {
    git(project, 'init', '-b', 'main'); git(project, 'config', 'user.name', 'Torque QA'); git(project, 'config', 'user.email', 'torque-qa@example.invalid');
    writeFileSync(join(project, '.gitignore'), '.torque/\n'); writeFileSync(join(project, 'sample.txt'), 'baseline\n'); git(project, 'add', '.'); git(project, 'commit', '-m', 'Baseline'); const baseline = git(project, 'rev-parse', 'HEAD');
    await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: project, git_worktree: true, agent_provider: 'generic' } });
    const state = await command(request, { cmd: 'add_agent', group, name: 'Acknowledgement worker', provider: 'generic', command: '/bin/cat', directory: project, shell: '/bin/sh', worktree: true });
    const row = Object.values(state.agents as Record<string, Row>).find((value) => value.group === group && value.name === 'Acknowledgement worker')!; agent = String(row.id); const worktree = realpathSync(String(row.worktree_path)); expect(worktree.startsWith(realpathSync(project) + sep)).toBe(true);
    expect(await command(request, { cmd: 'worktree_checkpoint', id: agent })).toMatchObject({ type: 'worktree_checkpoint', id: agent, ok: true, created: false, sha: '' });
    git(project, 'config', 'core.hooksPath', hooks); const hook = join(hooks, 'pre-commit'); writeFileSync(hook, '#!/bin/sh\necho "QA commit refusal" >&2\nexit 1\n', { mode: 0o755 }); writeFileSync(join(worktree, 'sample.txt'), 'checkpoint change\n');
    const mutation = { cmd: 'worktree_checkpoint', id: agent, idempotency_key: `checkpoint-${group}` };
    const refused = await (await request.post('/api/cmd', { data: mutation })).json() as Row; expect(refused.ok).toBe(false); expect(refused.error).toContain('QA commit refusal'); expect(git(worktree, 'rev-parse', 'HEAD')).toBe(baseline);
    const marker = join(root, 'started'); writeFileSync(hook, `#!/bin/sh\necho commit >> ${quote(marker)}\nsleep 1\n`, { mode: 0o755 });
    const first = request.post('/api/cmd', { data: mutation }); await expect.poll(() => existsSync(marker)).toBe(true);
    const second = request.post('/api/cmd', { data: mutation }); const conflict = await request.post('/api/cmd', { data: { ...mutation, message: 'different intent' } }); expect(conflict.status()).toBe(409);
    const [one, two] = await Promise.all([first, second]); const acknowledged = await one.json() as { ok: boolean; data: Row }; expect(await two.json()).toEqual(acknowledged); expect(acknowledged.ok).toBe(true); expect(acknowledged.data).toMatchObject({ type: 'worktree_checkpoint', id: agent, ok: true, created: true });
    expect(readFileSync(marker, 'utf8').trim().split('\n')).toHaveLength(1); expect(git(worktree, 'rev-list', '--count', 'HEAD')).toBe('2'); expect(acknowledged.data.sha).toBe(git(worktree, 'rev-parse', 'HEAD'));
    writeFileSync(join(worktree, 'sample.txt'), 'not a second checkpoint\n'); expect(await command(request, mutation)).toEqual(acknowledged.data); expect(readFileSync(join(worktree, 'sample.txt'), 'utf8')).toBe('not a second checkpoint\n'); expect(git(worktree, 'rev-list', '--count', 'HEAD')).toBe('2');
    const failedRollback = await (await request.post('/api/cmd', { data: { cmd: 'worktree_rollback', id: agent, sha: 'f'.repeat(40) } })).json() as Row; expect(failedRollback.ok).toBe(false); expect(failedRollback.error).toContain('Rollback failed');
    const rollback = { cmd: 'worktree_rollback', id: agent, sha: baseline, idempotency_key: `rollback-${group}` }; expect(await command(request, rollback)).toMatchObject({ type: 'worktree_rollback', id: agent, ok: true, sha: baseline }); expect(readFileSync(join(worktree, 'sample.txt'), 'utf8')).toBe('baseline\n');
    writeFileSync(join(worktree, 'sample.txt'), 'preserve after rollback replay\n'); await command(request, rollback); expect(readFileSync(join(worktree, 'sample.txt'), 'utf8')).toBe('preserve after rollback replay\n'); git(worktree, 'reset', '--hard', baseline);
    const removal = { cmd: 'worktree_remove', id: agent, relaunch: false, idempotency_key: `remove-${group}` };
    const activeRemoval = await (await request.post('/api/cmd', { data: removal })).json() as Row; expect(activeRemoval.ok).toBe(false); expect(activeRemoval.error).toContain('active/fresh'); expect(existsSync(worktree)).toBe(true);
    await command(request, { cmd: 'remove_agent', id: agent });
    const removed = await command(request, removal); expect(removed).toMatchObject({ type: 'worktree_remove', id: agent, worktree_removed: true }); expect(existsSync(worktree)).toBe(false); expect(await command(request, removal)).toEqual(removed);
  } finally { if (agent) { await command(request, { cmd: 'remove_agent', id: agent }); await command(request, { cmd: 'purge_agent_now', id: agent }); } rmSync(root, { recursive: true, force: true }); }
});
