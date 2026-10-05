import { expect, test, type APIRequestContext } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, sep } from 'node:path';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}
const git = (directory: string, ...args: string[]) => execFileSync('git', ['-C', directory, ...args], { encoding: 'utf8' }).trim();

for (const kind of ['engineer', 'architect'] as const) test(`${kind} creation omits ignored worktree overrides and preserves reviewed post-launch worktree creation`, async ({ page, request }) => {
  test.setTimeout(90_000); page.setDefaultTimeout(12_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const directory = await realpath(await mkdtemp(join(tmpdir(), 'torque-principal-worktree-')));
  git(directory, 'init', '-b', 'main'); git(directory, 'config', 'user.name', 'Torque QA'); git(directory, 'config', 'user.email', 'torque-qa@example.invalid');
  await writeFile(join(directory, '.gitignore'), '.torque/\n'); await writeFile(join(directory, 'baseline.txt'), 'Principal worktree acceptance\n'); git(directory, 'add', '.'); git(directory, 'commit', '-m', 'QA baseline');
  const group = `Principal worktree ${kind} ${Date.now()}`; const name = `Worktree ${kind}`; const ids: string[] = []; const writes: Row[] = []; const operations: Row[] = [];
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: directory, agent_provider: 'generic', agent_boot_command: '/bin/cat', git_worktree: true } });
  await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'agents', controlTab: 'settings' } });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd === 'worktree_create') operations.push(data);
    if (data.cmd !== `add_${kind}`) { await route.continue(); return; }
    writes.push(data); const response = await route.fetch(); const body = await response.json() as { ok: boolean; error?: string; data: Row };
    expect(body.ok, body.error).toBe(true); if (typeof body.data.id === 'string') ids.push(body.data.id); await route.fulfill({ response });
  });
  try {
    await page.goto('/'); await page.getByRole('button', { name: 'Create agent or terminal' }).click();
    await page.getByRole('menuitem', { name: `New ${kind === 'engineer' ? 'Engineer' : 'Architect'}…` }).click();
    const creation = page.getByRole('dialog', { name: `New ${kind}` });
    await expect(creation.getByRole('checkbox', { name: 'Create an isolated worktree', exact: true })).toHaveCount(0);
    await creation.getByLabel('Name', { exact: true }).fill(name);
    await page.screenshot({ path: test.info().outputPath(`${kind}-creation.png`), animations: 'disabled' });
    await creation.getByRole('button', { name: `Create ${kind}`, exact: true }).click(); await expect(creation).toHaveCount(0);
    const id = ids[0]!; expect(id).toBeTruthy();
    for (const key of ['worktree', 'worktree_base_dir', 'worktree_base_branch', 'worktree_name', 'worktree_auto_checkpoint', 'checkpoint_on_progress', 'worktree_merge_squash']) expect(writes[0]).not.toHaveProperty(key);
    const cell = async () => ((await command(request, { cmd: 'get_state' })).agents as Record<string, Row>)[id]!;
    const initial = await cell(); expect(initial).toMatchObject({ kind, worktree_path: '', directory }); expect(initial.session_id).toBeTruthy();
    await page.locator(`[role="treeitem"][data-agent-id="${id}"]`).click();
    const focused = page.getByRole('region', { name: `Focused agent ${name}` });
    await focused.getByRole('button', { name: 'Create', exact: true }).click();
    const worktree = page.getByRole('dialog', { name: `Create worktree for ${name}?` });
    await expect(worktree.getByText(/current conversation will be lost/)).toBeVisible();
    await worktree.getByRole('button', { name: 'Cancel', exact: true }).click(); expect(operations).toHaveLength(0); expect((await cell()).session_id).toBe(initial.session_id);
    await focused.getByRole('button', { name: 'Create', exact: true }).click();
    await worktree.getByRole('button', { name: 'Create and restart agent', exact: true }).click();
    await expect(worktree.getByText('Worktree created and agent relaunched', { exact: true })).toBeVisible();
    const final = await cell(); expect(final.session_id).toBeTruthy(); expect(final.session_id).not.toBe(initial.session_id);
    const path = await realpath(String(final.worktree_path)); expect(path.startsWith(directory + sep)).toBe(true); expect(final.directory).toBe(path);
    expect(await readFile(join(path, 'baseline.txt'), 'utf8')).toBe('Principal worktree acceptance\n'); expect(git(directory, 'worktree', 'list', '--porcelain')).toContain(path);
    await page.screenshot({ path: test.info().outputPath(`${kind}-manual-worktree.png`), animations: 'disabled' });
    await worktree.getByRole('button', { name: 'Close', exact: true }).click();
    // Workers retain launch-time worktree selection even when principals omit it.
    await page.getByRole('button', { name: 'Create agent or terminal' }).click(); await page.getByRole('menuitem', { name: 'New Worker…' }).click();
    const worker = page.getByRole('dialog', { name: 'New worker' }); await expect(worker.getByRole('checkbox', { name: 'Create an isolated worktree', exact: true })).toBeChecked(); await worker.getByRole('button', { name: 'Cancel', exact: true }).click();
    const evidence = test.info().outputPath(`${kind}-worktree-evidence.json`); await writeFile(evidence, JSON.stringify({ initial, final, writes, operations }, null, 2)); await test.info().attach('principal-worktree-evidence', { path: evidence, contentType: 'application/json' });
  } finally { for (const id of ids) { await command(request, { cmd: 'remove_agent', id }); await command(request, { cmd: 'purge_agent_now', id }); } await rm(directory, { recursive: true, force: true }); }
});
