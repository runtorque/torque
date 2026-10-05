import { execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, sep } from 'node:path';
import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
const fixture = { root: '', agent: '', task: '' };
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
test.afterEach(async ({ request }) => {
  if (fixture.agent) { await command(request, { cmd: 'remove_agent', id: fixture.agent }); await command(request, { cmd: 'purge_agent_now', id: fixture.agent }); }
  if (fixture.task) await command(request, { cmd: 'board_remove_task', id: fixture.task });
  if (fixture.root) rmSync(fixture.root, { recursive: true, force: true });
  fixture.root = ''; fixture.agent = ''; fixture.task = '';
});
test('PR review retains its confirmation and merge inherits reviewed group cleanup defaults', async ({ page, request }) => {
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.profile).not.toBe('default'); expect(runtime.port).not.toBe(18932);
  const root = mkdtempSync(join(tmpdir(), 'torque-worktree-review-')); fixture.root = root;
  const git = (...args: string[]) => execFileSync('git', ['-C', root, ...args], { encoding: 'utf8' }).trim();
  git('init', '-b', 'main'); git('config', 'user.name', 'Torque QA'); git('config', 'user.email', 'torque-qa@example.invalid');
  writeFileSync(join(root, '.gitignore'), '.torque/\n'); writeFileSync(join(root, 'sample.txt'), 'baseline\n'); git('add', '.'); git('commit', '-m', 'Baseline');
  const group = `Worktree review ${Date.now()}`;
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: root, git_worktree: true, agent_provider: 'generic', engineer_merge_mode: 'direct', worktree_merge_cleanup: 'close_remove', worktree_merge_preserve_diff: true } });
  const state = await command(request, { cmd: 'add_agent', group, name: 'Review QA worker', provider: 'generic', command: '/bin/cat', shell: '/bin/sh', directory: root, worktree: true });
  const row = Object.values(state.agents as Record<string, Row>).find((value) => value.group === group && value.name === 'Review QA worker')!;
  const agent = String(row.id); fixture.agent = agent; const worktree = realpathSync(String(row.worktree_path)); expect(worktree.startsWith(realpathSync(root) + sep)).toBe(true);
  writeFileSync(join(worktree, 'sample.txt'), 'reviewed merge content\n'); await command(request, { cmd: 'worktree_checkpoint', id: agent });
  fixture.task = String((await command(request, { cmd: 'board_add_task', group, task: 'Reviewed merge fixture', lane: 'In Progress', agent_id: agent })).task_id);
  await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_select_agent', id: agent }); await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'agents', controlTab: 'mission' } });
  let socket: WebSocketRoute | undefined; let connections = 0; const writes: Row[] = []; let outcome: 'refused' | 'lost' | 'ack' = 'refused';
  await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections++; });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (['worktree_create_pr', 'worktree_merge'].includes(String(data.cmd))) writes.push(data);
    // Exercise the external-PR response boundary without pushing a branch or contacting GitHub.
    if (data.cmd === 'worktree_create_pr') {
      if (outcome === 'refused') await route.fulfill({ json: { ok: true, data: { type: 'worktree_pr', id: agent, ok: false, error: 'Fixture PR refusal' } } });
      else if (outcome === 'lost') { outcome = 'ack'; await route.abort('failed'); }
      else await route.fulfill({ json: { ok: true, data: { type: 'worktree_pr', id: agent, ok: true, message: 'Fixture PR acknowledged', url: 'https://example.invalid/review/1' } } });
      return;
    }
    await route.continue();
  });
  await page.goto('/'); await page.getByRole('button', { name: 'Inspect diff', exact: true }).click();
  const inspector = page.getByRole('dialog', { name: 'Review QA worker worktree' });
  const close = inspector.getByRole('checkbox', { name: 'Close agent after merge' }); const remove = inspector.getByRole('checkbox', { name: 'Delete worktree after merge' }); const preserve = inspector.getByRole('checkbox', { name: 'Preserve boundary diff' });
  await expect(close).toBeChecked(); await expect(remove).toBeChecked(); await expect(preserve).toBeChecked(); await expect(inspector.getByText(/Cleanup options run only after/)).toBeVisible();
  await remove.uncheck(); await command(request, { cmd: 'update_group_settings', group, settings: { worktree_merge_cleanup: 'keep', worktree_merge_preserve_diff: false } }); await expect(close).not.toBeChecked(); await expect(preserve).not.toBeChecked(); await preserve.check();
  await command(request, { cmd: 'update_group_settings', group, settings: { worktree_merge_cleanup: 'close_remove' } }); await expect(close).toBeChecked(); await expect(remove).not.toBeChecked(); await expect(preserve).toBeChecked();
  const diff = inspector.getByRole('region', { name: 'Worktree diff files' }); await diff.evaluate((node) => { node.dataset.reviewAnchor = 'retained'; });
  await inspector.getByRole('button', { name: 'Create PR', exact: true }).click(); const review = page.getByRole('dialog', { name: 'Create pull request?' });
  await expect(review.getByText(String(row.worktree_branch), { exact: true })).toBeVisible(); await expect(review.getByText('main', { exact: true })).toBeVisible(); await expect(review.getByText(/pushed to origin/)).toBeVisible(); await expect(review.getByRole('button', { name: 'Cancel', exact: true })).toBeFocused(); expect(writes).toHaveLength(0);
  await page.screenshot({ animations: 'disabled', path: test.info().outputPath('worktree-pr-review.png') });
  await review.getByRole('button', { name: 'Cancel', exact: true }).click(); await expect(inspector.getByRole('button', { name: 'Create PR', exact: true })).toBeFocused(); await expect(diff).toHaveAttribute('data-review-anchor', 'retained'); expect(writes).toHaveLength(0);
  await inspector.getByRole('button', { name: 'Create PR', exact: true }).click(); await review.getByRole('button', { name: 'Push branch and create PR' }).click(); await expect(review.getByRole('alert')).toHaveText('Fixture PR refusal');
  outcome = 'lost'; await review.getByRole('button', { name: 'Push branch and create PR' }).click(); await expect(review.getByRole('alert')).toContainText('outcome is unknown'); await expect(review.getByRole('button', { name: 'Cancel', exact: true })).toBeDisabled();
  const beforeReconnect = connections; await socket!.close({ code: 1012, reason: 'PR recovery acceptance' }); await expect.poll(() => connections).toBeGreaterThan(beforeReconnect); expect(writes).toHaveLength(2); await expect(review).toBeVisible();
  await review.getByRole('button', { name: 'Retry PR operation' }).click(); await expect(inspector.getByText('Fixture PR acknowledged', { exact: true })).toBeVisible(); expect(writes[2]).toEqual(writes[1]); expect(writes[1]?.idempotency_key).not.toBe(writes[0]?.idempotency_key); await expect(remove).not.toBeChecked(); await expect(preserve).toBeChecked(); await expect(diff).toHaveAttribute('data-review-anchor', 'retained');
  await inspector.getByRole('button', { name: 'Close', exact: true }).click(); await command(request, { cmd: 'update_group_settings', group, settings: { worktree_merge_preserve_diff: true } }); await page.getByRole('button', { name: 'Inspect diff', exact: true }).click(); await expect(close).toBeChecked(); await expect(remove).toBeChecked(); await expect(preserve).toBeChecked();
  await inspector.getByRole('checkbox', { name: 'Force direct local merge' }).check(); await inspector.getByRole('textbox', { name: 'Merge message' }).fill('Merge with inherited cleanup');
  const merged = page.waitForResponse((response) => response.url().endsWith('/api/cmd') && (response.request().postDataJSON() as Row).cmd === 'worktree_merge');
  await inspector.getByRole('button', { name: 'Create PR & merge', exact: true }).click(); const result = await (await merged).json() as { ok: boolean; data: Row }; expect(result.ok).toBe(true); expect(result.data.ok).toBe(true);
  expect(writes.at(-1)).toMatchObject({ cmd: 'worktree_merge', id: agent, close_agent_on_merge: true, remove_worktree_on_merge: true, preserve_merge_diff: true, force_direct: true });
  expect(readFileSync(join(root, 'sample.txt'), 'utf8')).toBe('reviewed merge content\n'); await expect.poll(() => existsSync(worktree)).toBe(false);
  await expect(inspector.getByRole('button', { name: 'Close', exact: true })).toBeEnabled(); await page.screenshot({ animations: 'disabled', path: test.info().outputPath('worktree-inherited-cleanup.png') });
});
