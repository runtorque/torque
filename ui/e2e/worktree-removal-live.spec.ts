import { execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, sep } from 'node:path';
import { expect, test, type APIRequestContext } from '@playwright/test';
type Row = Record<string, unknown>;
const fixture = { root: '', agents: [] as string[] };
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
test.afterEach(async ({ request }) => {
  for (const id of fixture.agents.reverse()) { await command(request, { cmd: 'remove_agent', id }); await command(request, { cmd: 'purge_agent_now', id }); }
  if (fixture.root) rmSync(fixture.root, { recursive: true, force: true });
  fixture.root = ''; fixture.agents = [];
});
test('reviewed unlink preserves a live peer and lost-ack retry; physical removal retains an unmerged branch', async ({ page, request }) => {
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.profile).not.toBe('default'); expect(runtime.port).not.toBe(18932);
  const root = mkdtempSync(join(tmpdir(), 'torque-removal-live-')); fixture.root = root;
  const git = (directory: string, ...args: string[]) => execFileSync('git', ['-C', directory, ...args], { encoding: 'utf8' }).trim();
  git(root, 'init', '-b', 'main'); git(root, 'config', 'user.name', 'Torque QA'); git(root, 'config', 'user.email', 'torque-qa@example.invalid');
  writeFileSync(join(root, '.gitignore'), '.torque/\nignored.txt\n'); writeFileSync(join(root, 'sample.txt'), 'baseline\n'); git(root, 'add', '.'); git(root, 'commit', '-m', 'Baseline');
  const group = `Removal QA ${Date.now()}`;
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: root, git_worktree: true, agent_provider: 'generic' } });
  async function add(name: string, directory = root, worktree = true) {
    const state = await command(request, { cmd: 'add_agent', group, name, provider: 'generic', command: '/bin/cat', shell: '/bin/sh', directory, worktree });
    const row = Object.values(state.agents as Record<string, Row>).find((value) => value.group === group && value.name === name)!;
    fixture.agents.push(String(row.id)); return row;
  }
  async function stop(id: string) { await command(request, { cmd: 'remove_agent', id }); await command(request, { cmd: 'restore_agent', id }); }
  async function select(id: string) {
    await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_select_agent', id });
    await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'agents', controlTab: 'mission' } });
    await page.goto('/'); await page.getByRole('button', { name: 'Inspect diff', exact: true }).click();
  }
  const owner = await add('Shared owner'); const id = String(owner.id); const path = realpathSync(String(owner.worktree_path));
  expect(path.startsWith(realpathSync(root) + sep)).toBe(true);
  writeFileSync(join(path, 'sample.txt'), 'checkpoint\n'); await command(request, { cmd: 'worktree_checkpoint', id });
  writeFileSync(join(path, 'sample.txt'), 'unsaved shared content\n'); writeFileSync(join(path, 'ignored.txt'), 'ignored shared data\n');
  const writes: Row[] = []; let drop = true;
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd === 'worktree_remove') { writes.push(data); if (drop) { drop = false; const response = await route.fetch(); expect(((await response.json()) as { data: Row }).data.ok).toBe(true); await route.abort('failed'); return; } }
    await route.continue();
  });
  await select(id); await page.getByRole('button', { name: 'Delete worktree…' }).click();
  let review = page.getByRole('dialog', { name: 'Remove worktree?' });
  await expect(review.getByRole('alert')).toContainText('active/fresh'); await expect(review.getByRole('button', { name: 'Delete worktree', exact: true })).toBeDisabled();
  await review.getByRole('button', { name: 'Cancel' }).click(); expect(writes).toHaveLength(0);
  await page.getByRole('button', { name: 'Close', exact: true }).click(); await stop(id);
  const deleteReview = (await command(request, { cmd: 'worktree_remove_preview', id })).review;
  const peer = await add('Live shared peer', path, false); const peerId = String(peer.id);
  const refused = await command(request, { cmd: 'worktree_remove', id, removal_review: deleteReview, relaunch: false });
  expect(refused.ok).toBe(false); expect(String(refused.error)).toContain('sharing changed'); expect(existsSync(path)).toBe(true);
  const list = git(root, 'worktree', 'list', '--porcelain'); const branch = String(owner.worktree_branch); const tip = git(root, 'rev-parse', branch);
  await select(id); await page.getByRole('button', { name: 'Delete worktree…' }).click(); review = page.getByRole('dialog', { name: 'Remove worktree link?' });
  await expect(review.getByText(/Live shared peer/)).toBeVisible(); await expect(review.getByText(/uncommitted changes/)).toBeVisible(); await expect(review.getByText(/1 commit ahead/)).toBeVisible(); await expect(review.getByText(/will not start or restart/)).toBeVisible();
  await review.getByRole('button', { name: 'Cancel' }).click(); expect(writes).toHaveLength(0); expect(git(root, 'worktree', 'list', '--porcelain')).toBe(list);
  await page.getByRole('button', { name: 'Delete worktree…' }).click(); await review.getByRole('button', { name: 'Remove link', exact: true }).click();
  await expect(review.getByRole('alert')).toContainText('outcome is unknown'); await expect(review.getByRole('button', { name: 'Cancel' })).toBeDisabled();
  expect(readFileSync(join(path, 'sample.txt'), 'utf8')).toBe('unsaved shared content\n'); expect(readFileSync(join(path, 'ignored.txt'), 'utf8')).toBe('ignored shared data\n'); expect(git(root, 'worktree', 'list', '--porcelain')).toBe(list); expect(git(root, 'rev-parse', branch)).toBe(tip);
  const agents = (await command(request, { cmd: 'get_state' })).agents as Record<string, Row>;
  expect(agents[peerId]?.session_id).toBe(peer.session_id); expect(agents[peerId]?.directory).toBe(path); expect(agents[id]?.worktree_path).toBe(''); expect(agents[id]?.session_id).toBeNull();
  await review.getByRole('button', { name: 'Retry removal' }).click(); await expect(review.getByRole('status')).toContainText('Shared worktree and branch retained'); expect(writes[1]).toEqual(writes[0]);
  const assertFits = async () => {
    const body = review.locator('[data-dialog-body-layout]');
    expect(await body.evaluate((node) => node.scrollWidth <= node.clientWidth && node.scrollLeft === 0)).toBe(true);
    const box = (await review.boundingBox())!;
    for (const paragraph of await review.locator('p:visible').all()) {
      const bounds = (await paragraph.boundingBox())!;
      expect(bounds.x).toBeGreaterThanOrEqual(box.x); expect(bounds.x + bounds.width).toBeLessThanOrEqual(box.x + box.width);
    }
  };
  await assertFits(); await page.setViewportSize({ width: 390, height: 844 }); await assertFits();
  await page.screenshot({ animations: 'disabled', path: test.info().outputPath('shared-unlink-mobile.png') });
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.screenshot({ animations: 'disabled', path: test.info().outputPath('shared-unlink-result.png') }); await review.getByRole('button', { name: 'Done' }).click();
  const unique = await add('Unique removal'); const uniqueId = String(unique.id); const uniquePath = realpathSync(String(unique.worktree_path)); const uniqueBranch = String(unique.worktree_branch);
  writeFileSync(join(uniquePath, 'sample.txt'), 'unique checkpoint\n'); await command(request, { cmd: 'worktree_checkpoint', id: uniqueId }); await stop(uniqueId);
  writeFileSync(join(uniquePath, 'sample.txt'), 'discard reviewed edit\n'); writeFileSync(join(uniquePath, 'ignored.txt'), 'discard ignored data\n');
  await select(uniqueId); await page.getByRole('button', { name: 'Delete worktree…' }).click(); review = page.getByRole('dialog', { name: 'Remove worktree?' });
  await expect(review.getByText(/These changes will be permanently discarded/)).toBeVisible(); await expect(review.getByText(/Ignored files.*permanently discarded/)).toBeVisible();
  await review.getByRole('button', { name: 'Cancel' }).click(); expect(existsSync(uniquePath)).toBe(true); expect(readFileSync(join(uniquePath, 'sample.txt'), 'utf8')).toBe('discard reviewed edit\n');
  await page.getByRole('button', { name: 'Delete worktree…' }).click(); await review.getByRole('button', { name: 'Delete worktree', exact: true }).click();
  await expect(review.getByRole('status')).toContainText(`Worktree removed. Branch retained: ${uniqueBranch}`); expect(existsSync(uniquePath)).toBe(false); expect(git(root, 'show', `${uniqueBranch}:sample.txt`)).toBe('unique checkpoint');
  await assertFits(); await expect(review.getByRole('button', { name: 'Retry removal' })).toHaveCount(0); await page.screenshot({ animations: 'disabled', path: test.info().outputPath('physical-removal-result.png') }); await review.getByRole('button', { name: 'Done' }).click();
});
