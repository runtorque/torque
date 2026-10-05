import { execFileSync } from 'node:child_process';
import { mkdtempSync, writeFileSync, rmSync, realpathSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, sep } from 'node:path';
import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) { const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row }; expect(result.ok, result.error).toBe(true); return result.data; }
test('large worktree diffs progressively mount lines and retain disclosure across refresh and tabs', async ({ page, request }) => {
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime; expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const project = mkdtempSync(join(tmpdir(), 'torque-diff-parity-')); const group = `Worktree diff ${Date.now()}`; let agent = '';
  try {
    const git = (...args: string[]) => execFileSync('git', ['-C', project, ...args], { encoding: 'utf8' });
    git('init', '-b', 'main'); git('config', 'user.name', 'Torque QA'); git('config', 'user.email', 'torque-qa@example.invalid');
    writeFileSync(join(project, '.gitignore'), '.torque/\n'); writeFileSync(join(project, 'large.txt'), 'baseline\n'); writeFileSync(join(project, 'small.txt'), 'small baseline\n'); git('add', '.'); git('commit', '-m', 'QA baseline');
    await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: project, git_worktree: true, agent_provider: 'generic' } });
    const state = await command(request, { cmd: 'add_agent', group, name: 'Diff QA worker', provider: 'generic', command: '/bin/cat', directory: project, shell: '/bin/sh', worktree: true });
    const row = Object.values(state.agents as Record<string, Row>).find((value) => value.name === 'Diff QA worker' && value.group === group)!; agent = String(row.id);
    const worktree = realpathSync(String(row.worktree_path)); expect(worktree.startsWith(realpathSync(project) + sep)).toBe(true);
    const large = 'baseline\n' + Array.from({ length: 2050 }, (_, index) => `change ${index + 1}\n`).join(''); writeFileSync(join(worktree, 'large.txt'), large); writeFileSync(join(worktree, 'small.txt'), 'small baseline\nsmall addition\n');
    await command(request, { cmd: 'worktree_checkpoint', id: agent });
    await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_select_agent', id: agent }); await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'agents', controlTab: 'mission' } });
    let socket: WebSocketRoute | undefined; let connections = 0; let refused = false; const reads: string[] = [];
    await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections++; });
    await page.route('**/api/cmd', async (route) => {
      const data = route.request().postDataJSON() as Row;
      if (['worktree_diff_full', 'worktree_check_merge', 'worktree_history'].includes(String(data.cmd))) {
        reads.push(String(data.cmd));
        if (refused && data.cmd !== 'worktree_history') { await route.fulfill({ json: { ok: false, error: 'Injected worktree read refusal' } }); return; }
      }
      await route.continue();
    });
    await page.goto('/'); await page.getByRole('button', { name: 'Inspect diff', exact: true }).click(); const dialog = page.getByRole('dialog', { name: 'Diff QA worker worktree' });
    const region = dialog.getByRole('region', { name: 'Worktree diff files' }); const largeFile = region.locator('[data-diff-path="large.txt"]'); const smallFile = region.locator('[data-diff-path="small.txt"]');
    await expect(largeFile.getByRole('button').first()).toHaveAttribute('aria-expanded', 'false'); await expect(smallFile.getByRole('button').first()).toHaveAttribute('aria-expanded', 'true'); await expect(largeFile.locator('pre span')).toHaveCount(0);
    await largeFile.getByRole('button').first().focus(); await page.keyboard.press('Enter'); await expect(largeFile.locator('pre span')).toHaveCount(400);
    await largeFile.getByRole('button', { name: 'Show 400 more lines' }).click(); await expect(largeFile.locator('pre span')).toHaveCount(800);
    const nav = dialog.getByRole('navigation', { name: 'Worktree views' }); await nav.getByRole('button', { name: /History/ }).click(); await expect(region).toBeHidden(); await nav.getByRole('button', { name: 'Changes' }).click(); await expect(largeFile.locator('pre span')).toHaveCount(800);
    await smallFile.getByRole('button').first().click(); await largeFile.getByRole('button').first().evaluate((node) => { node.dataset.diffAnchor = 'original'; }); await region.evaluate((node) => { node.scrollTop = 170; });
    writeFileSync(join(worktree, 'large.txt'), large + 'latest refresh marker\n'); await command(request, { cmd: 'worktree_checkpoint', id: agent }); await nav.getByRole('button', { name: 'Refresh', exact: true }).click(); await expect(largeFile.getByRole('button').first()).toContainText('+2051');
    await expect(largeFile.locator('pre span')).toHaveCount(800); await expect(smallFile.getByRole('button').first()).toHaveAttribute('aria-expanded', 'false'); await expect(largeFile.getByRole('button').first()).toHaveAttribute('data-diff-anchor', 'original'); expect(await region.evaluate((node) => node.scrollTop)).toBe(170);
    const draft = dialog.getByRole('textbox', { name: 'Merge message' }); await draft.fill('Retained merge draft'); await draft.focus(); await draft.evaluate((node: HTMLTextAreaElement) => { node.setSelectionRange(2, 7); });
    refused = true; const before = connections; const readsBefore = reads.length; await socket!.close({ code: 1012, reason: 'Worktree reading workspace reconnect' });
    await expect.poll(() => connections).toBeGreaterThan(before); await expect.poll(() => reads.length).toBe(readsBefore + 3); await expect(dialog.getByRole('alert')).toHaveCount(2);
    await expect(draft).toHaveValue('Retained merge draft'); await expect(draft).toBeFocused(); expect(await draft.evaluate((node: HTMLTextAreaElement) => [node.selectionStart, node.selectionEnd])).toEqual([2, 7]);
    await expect(largeFile.getByRole('button').first()).toHaveAttribute('data-diff-anchor', 'original'); await expect(largeFile.locator('pre span')).toHaveCount(800); expect(await region.evaluate((node) => node.scrollTop)).toBe(170); await expect(dialog.getByRole('button', { name: 'Create PR & merge' })).toBeDisabled();
    writeFileSync(join(worktree, 'large.txt'), large + 'latest refresh marker\nreconnect marker\n'); await command(request, { cmd: 'worktree_checkpoint', id: agent });
    refused = false; await dialog.getByRole('button', { name: 'Retry changes' }).click(); await expect(dialog.getByRole('alert')).toHaveCount(0); await expect(largeFile.getByRole('button').first()).toContainText('+2052'); await expect(dialog.getByRole('button', { name: 'Create PR & merge' })).toBeEnabled();
    await expect(largeFile.locator('pre span')).toHaveCount(800); await expect(smallFile.getByRole('button').first()).toHaveAttribute('aria-expanded', 'false'); await expect(draft).toHaveValue('Retained merge draft');
    await dialog.getByRole('button', { name: 'Collapse all', exact: true }).click(); await expect(region.locator('pre span')).toHaveCount(0); await dialog.getByRole('button', { name: 'Expand all', exact: true }).click(); await expect(largeFile.locator('pre span')).toHaveCount(800);
    await page.setViewportSize({ width: 760, height: 800 }); await region.scrollIntoViewIfNeeded(); await page.screenshot({ animations: 'disabled', path: test.info().outputPath('worktree-diff-compact.png') });
    const count = (await command(request, { cmd: 'worktree_diff_full', id: agent })).files as Row[]; expect(count.find((value) => value.path === 'large.txt')?.insertions).toBe(2052);
    await dialog.getByRole('button', { name: 'Close', exact: true }).click(); const afterClose = reads.length; const closedConnections = connections; await socket!.close({ code: 1012, reason: 'Closed inspector must stay idle' }); await expect.poll(() => connections).toBeGreaterThan(closedConnections); expect(reads).toHaveLength(afterClose);
  } finally { if (agent) { await command(request, { cmd: 'remove_agent', id: agent }); await command(request, { cmd: 'purge_agent_now', id: agent }); } rmSync(project, { recursive: true, force: true }); }
});
