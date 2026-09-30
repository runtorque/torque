import { expect, test, type APIRequestContext, type Page, type WebSocketRoute } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, sep } from 'node:path';
type Row = Record<string, unknown>;
const git = (directory: string, ...args: string[]) => execFileSync('git', ['-C', directory, ...args], { encoding: 'utf8' }).trim();
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}
async function save(page: Page) { await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible(); }
const controls = [
  ['git_worktree', 'Worktrees'],
  ['worktree_auto_checkpoint', 'Worktree auto checkpoint'],
  ['checkpoint_on_progress', 'Checkpoint on progress'],
  ['worktree_merge_squash', 'Worktree merge squash'],
  ['worktree_symlink_gitignored_paths', 'Worktree symlink gitignored paths'],
] as const;

test('Worktree settings drive actual branch, directory, linked files and nested submodule creation', async ({ page, request }) => {
  test.setTimeout(120_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.profile).not.toBe('default'); expect(runtime.port).not.toBe(18932);
  const fixture = realpathSync(mkdtempSync(join(tmpdir(), 'torque-worktree-settings-'))); const root = join(fixture, 'superproject'); const library = join(fixture, 'library');
  for (const directory of [root, library]) { mkdirSync(directory); git(directory, 'init', '-b', 'main'); git(directory, 'config', 'user.name', 'Torque QA'); git(directory, 'config', 'user.email', 'torque-qa@example.invalid'); }
  writeFileSync(join(library, 'library.txt'), 'nested library content\n'); git(library, 'add', '.'); git(library, 'commit', '-m', 'Local library baseline');
  writeFileSync(join(root, '.gitignore'), '.torque/\nshared cache/\n.env.local\n'); writeFileSync(join(root, 'branch.txt'), 'qa base\n');
  git(root, '-c', 'protocol.file.allow=always', 'submodule', 'add', library, 'deps/library'); git(root, 'add', '.'); git(root, 'commit', '-m', 'Superproject baseline with local submodule'); git(root, 'branch', 'qa-base');
  const baseHead = git(root, 'rev-parse', 'qa-base'); const libraryHead = git(library, 'rev-parse', 'HEAD');
  writeFileSync(join(root, 'branch.txt'), 'main tip\n'); git(root, 'add', 'branch.txt'); git(root, 'commit', '-m', 'Advance main independently'); const mainHead = git(root, 'rev-parse', 'HEAD');
  mkdirSync(join(root, 'shared cache')); writeFileSync(join(root, 'shared cache', 'cache.txt'), 'QA shared cache\n'); writeFileSync(join(root, '.env.local'), 'QA_ONLY=local fixture\n');
  const group = `Worktree settings ${Date.now()}`; const ids: string[] = []; const writes: Row[] = []; const creates: Row[] = []; const evidence: Row[] = [];
  let socket: WebSocketRoute | undefined; let connections = 0; let updates = 0; let refuse = true;
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: root, agent_provider: 'generic', agent_boot_command: '/bin/cat', default_agent_template: '', git_worktree: false } });
  await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'settings' } });
  const original = (await command(request, { cmd: 'get_group_settings', group })).settings as Row;
  await page.routeWebSocket(/\/ws\?/, (client) => {
    socket = client; connections++; const server = client.connectToServer();
    server.onMessage((message) => { const frame = JSON.parse(String(message)) as Row; if (frame.type === 'delta' && Array.isArray(frame.ops)) updates += (frame.ops as Row[]).filter((op) => op.op === 'group_settings_update' && op.name === group).length; client.send(message); });
  });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd === 'update_group_settings') { writes.push(data); if (refuse) { refuse = false; await route.fulfill({ json: { ok: false, error: 'QA worktree settings refused' } }); return; } }
    if (data.cmd === 'add_worker') { creates.push(data); const response = await route.fetch(); const body = await response.json() as { data: Row }; if (typeof body.data.id === 'string') ids.push(body.data.id); await route.fulfill({ response }); return; }
    await route.continue();
  });
  const open = async () => { await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click(); const summary = page.getByText(`${group} execution, worktrees, notifications and sync`, { exact: true }); if (!(await summary.evaluate((node) => node.closest('details')?.open))) await summary.click(); };
  const launch = async (phase: string, settings: Row) => {
    await page.getByRole('button', { name: /⌁ Agents/ }).click(); await page.getByRole('button', { name: 'Create agent or terminal' }).click(); await page.getByRole('menuitem', { name: 'New Worker…' }).click();
    const dialog = page.getByRole('dialog', { name: 'New worker', exact: true }); await expect(dialog.getByRole('button', { name: 'Create worker', exact: true })).toBeDisabled();
    await dialog.getByRole('textbox', { name: 'Name', exact: true }).fill(`Settings worker ${phase}`); await expect(dialog.getByRole('textbox', { name: 'Boot command', exact: true })).toHaveValue('/bin/cat');
    const isolation = dialog.getByRole('checkbox', { name: 'Create an isolated worktree', exact: true }); if (settings.git_worktree) {
      await expect(isolation).toBeChecked(); await expect(dialog.getByRole('textbox', { name: 'Base directory', exact: true })).toHaveValue(String(settings.worktree_base_dir)); await expect(dialog.getByRole('textbox', { name: 'Base branch', exact: true })).toHaveValue(String(settings.worktree_base_branch));
      for (const [label, key] of [['Checkpoint on stop', 'worktree_auto_checkpoint'], ['Checkpoint on progress', 'checkpoint_on_progress'], ['Squash merge', 'worktree_merge_squash']]) { const box = dialog.getByRole('checkbox', { name: label!, exact: true }); if (settings[key!]) await expect(box).toBeChecked(); else await expect(box).not.toBeChecked(); }
    } else { await expect(isolation).not.toBeChecked(); await expect(dialog.getByRole('textbox', { name: 'Base branch', exact: true })).toHaveCount(0); }
    await dialog.getByRole('button', { name: 'Create worker', exact: true }).click(); await expect(dialog).toHaveCount(0);
    const id = ids.at(-1)!; const cell = ((await command(request, { cmd: 'get_state' })).agents as Record<string, Row>)[id]!;
    if (settings.git_worktree) {
      expect(creates.at(-1)).toMatchObject({ worktree: true, worktree_auto_checkpoint: settings.worktree_auto_checkpoint, checkpoint_on_progress: settings.checkpoint_on_progress, worktree_merge_squash: settings.worktree_merge_squash });
      expect(cell).toMatchObject({ worktree_auto_checkpoint: settings.worktree_auto_checkpoint, checkpoint_on_progress: settings.checkpoint_on_progress, worktree_merge_squash: settings.worktree_merge_squash });
    }
    evidence.push({ phase, settings, cell, request: creates.at(-1) }); return cell;
  };
  const remove = async () => { const id = ids.at(-1)!; await command(request, { cmd: 'remove_agent', id }); await command(request, { cmd: 'purge_agent_now', id }); ids.pop(); };
  try {
    await page.goto('/'); await open();
    const first = { git_worktree: true, worktree_auto_checkpoint: false, checkpoint_on_progress: true, worktree_merge_squash: true, worktree_symlink_gitignored_paths: false, worktree_base_dir: '.torque/qa worktrees', worktree_base_branch: 'qa-base', worktree_symlinks: ['shared cache'], worktree_submodules: ['deps/library'] };
    for (const [key, label] of controls) await page.getByRole('combobox', { name: label, exact: true }).selectOption(key === 'git_worktree' ? (first[key] ? 'on' : 'off') : String(first[key]));
    const textFields = [['Worktree base dir', '  .torque/qa worktrees  '], ['Worktree base branch', '  qa-base  '], ['Worktree symlinks', 'shared cache\n'], ['Worktree submodules', 'deps/library\n']] as const;
    for (const [index, [label, draft]] of textFields.entries()) {
      const input = page.getByRole('textbox', { name: label, exact: true }); await input.fill(draft); await input.evaluate((node: HTMLInputElement) => { node.setSelectionRange(1, 5); node.dataset.retained = 'yes'; });
      const beforeUpdates = updates; await command(request, { cmd: 'update_group_settings', group, settings: { max_agents: index + 4 } }); await expect.poll(() => updates).toBeGreaterThan(beforeUpdates);
      const before = connections; await socket!.close({ code: 1012, reason: 'Worktree setting draft' }); await expect.poll(() => connections).toBeGreaterThan(before);
      await expect(input).toHaveValue(draft); await expect(input).toBeFocused(); await expect(input).toHaveAttribute('data-retained', 'yes'); expect(await input.evaluate((node: HTMLInputElement) => [node.selectionStart, node.selectionEnd])).toEqual([1, 5]);
    }
    expect(writes).toHaveLength(0); await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByRole('alert')).toContainText('QA worktree settings refused');
    for (const [label, draft] of textFields) await expect(page.getByRole('textbox', { name: label, exact: true })).toHaveValue(draft);
    await save(page); expect(writes[1]).toEqual(writes[0]); expect(writes[1]!.settings).toEqual(Object.fromEntries(Object.entries(first).filter(([key, value]) => JSON.stringify(original[key]) !== JSON.stringify(value))));
    expect((await command(request, { cmd: 'get_group_settings', group })).settings).toMatchObject(first); await page.reload(); await open();
    for (const [key, label] of controls) await expect(page.getByRole('combobox', { name: label, exact: true })).toHaveValue(key === 'git_worktree' ? (first[key] ? 'on' : 'off') : String(first[key]));
    for (const [label, value] of [['Worktree base dir', first.worktree_base_dir], ['Worktree base branch', first.worktree_base_branch], ['Worktree symlinks', first.worktree_symlinks.join('\n')], ['Worktree submodules', first.worktree_submodules.join('\n')]]) await expect(page.getByRole('textbox', { name: label!, exact: true })).toHaveValue(value!);
    const one = await launch('configured', first); const worktree = realpathSync(String(one.worktree_path)); expect(worktree.startsWith(join(root, first.worktree_base_dir) + sep)).toBe(true); expect(git(worktree, 'rev-parse', 'HEAD')).toBe(baseHead); expect(readFileSync(join(worktree, 'branch.txt'), 'utf8')).toBe('qa base\n');
    expect(lstatSync(join(worktree, 'shared cache')).isSymbolicLink()).toBe(true); expect(realpathSync(join(worktree, 'shared cache'))).toBe(join(root, 'shared cache')); expect(existsSync(join(worktree, '.env.local'))).toBe(false);
    const nested = join(worktree, 'deps/library'); expect(git(nested, 'rev-parse', 'HEAD')).toBe(libraryHead); expect(readFileSync(join(nested, 'library.txt'), 'utf8')).toBe('nested library content\n'); expect(git(nested, 'rev-parse', '--git-common-dir')).toBe(git(join(root, 'deps/library'), 'rev-parse', '--git-common-dir'));
    await remove(); await open();
    const second = { ...first, worktree_auto_checkpoint: true, checkpoint_on_progress: false, worktree_merge_squash: false, worktree_symlink_gitignored_paths: true, worktree_base_dir: '.torque/worktrees', worktree_base_branch: '', worktree_symlinks: [], worktree_submodules: [] };
    for (const [key, label] of controls) await page.getByRole('combobox', { name: label, exact: true }).selectOption(key === 'git_worktree' ? (second[key] ? 'on' : 'off') : String(second[key]));
    for (const [label] of textFields) await page.getByRole('textbox', { name: label, exact: true }).fill(''); await save(page); expect((await command(request, { cmd: 'get_group_settings', group })).settings).toMatchObject(second);
    await page.reload(); await open(); await expect(page.getByRole('textbox', { name: 'Worktree base dir', exact: true })).toHaveValue('.torque/worktrees');
    const two = await launch('fallback', second); const fallback = realpathSync(String(two.worktree_path)); expect(fallback.startsWith(join(root, second.worktree_base_dir) + sep)).toBe(true); expect(git(fallback, 'rev-parse', 'HEAD')).toBe(mainHead);
    for (const path of ['shared cache', '.env.local']) { expect(lstatSync(join(fallback, path)).isSymbolicLink()).toBe(true); expect(realpathSync(join(fallback, path))).toBe(join(root, path)); }
    expect(existsSync(join(fallback, 'deps/library/library.txt'))).toBe(false); await remove(); await open();
    await page.getByRole('combobox', { name: 'Worktrees', exact: true }).selectOption('off'); await save(page); expect(writes.at(-1)?.settings).toEqual({ git_worktree: false }); await page.reload(); await open();
    await page.getByRole('combobox', { name: 'Worktrees', exact: true }).scrollIntoViewIfNeeded(); await page.screenshot({ path: test.info().outputPath('worktree-settings.png') });
    const three = await launch('disabled', { ...second, git_worktree: false }); expect(three.worktree_path || '').toBe(''); expect(realpathSync(String(three.directory))).toBe(root); expect(creates.at(-1)).toMatchObject({ worktree: false }); await remove();
    const path = test.info().outputPath('worktree-settings-evidence.json'); writeFileSync(path, JSON.stringify({ writes, evidence, baseHead, mainHead, libraryHead }, null, 2)); await test.info().attach('worktree-settings-evidence', { path, contentType: 'application/json' });
  } finally { for (const id of ids) { await command(request, { cmd: 'remove_agent', id }); await command(request, { cmd: 'purge_agent_now', id }); } rmSync(fixture, { recursive: true, force: true }); }
});
