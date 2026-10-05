import { expect, test, type APIRequestContext } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const response = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(response.ok, response.error).toBe(true); expect(response.data.type).not.toBe('error'); return response.data;
}
const edits = [
  ['global', 'default_command', 'Default command', '  cat  ', 'cat'],
  ['group', 'default_directory', 'Default directory', '  /tmp/my project  ', '/tmp/my project'],
  ['group', 'agent_directory', 'Agent directory', '  /tmp/agent project  ', '/tmp/agent project'],
  ['group', 'agent_env_file', 'Agent env file', '  .env.local  ', '.env.local'],
  ['group', 'worktree_base_dir', 'Worktree base dir', '  ', '.torque/worktrees'],
  ['group', 'worktree_base_branch', 'Worktree base branch', '  release  ', 'release'],
  ['group', 'board_default_action', 'Board default action', '  feature/implement  ', 'feature/implement'],
  ['group', 'board_default_lane', 'Board default lane', '  In Review  ', 'In Review'],
  ['group', 'dispatch_lane', 'Dispatch lane', '  ', 'In Progress'],
  ['group', 'board_default_labels', 'Board default labels', '  first  \n  second label  \n ', ['first', 'second label']],
  ['engineer', 'engineer_directory', 'Engineer directory', '  /tmp/engineer project  ', '/tmp/engineer project'],
  ['engineer', 'engineer_boot_command', 'Engineer boot command', '  cat  ', 'cat'],
  ['engineer', 'custom_instructions', 'Custom instructions', '  first line\n  second line\n', '  first line\n  second line\n'],
  ['architect', 'architect_directory', 'Architect directory', '  /tmp/architect project  ', '/tmp/architect project'],
  ['architect', 'architect_boot_command', 'Architect boot command', '  cat  ', 'cat'],
  ['architect', 'architect_custom_instructions', 'Architect custom instructions', '  first line\n  second line\n', '  first line\n  second line\n'],
] as const;

test('Settings text matches Classic save boundaries across scopes and retains exact drafts on partial failure', async ({ page, request }) => {
  test.setTimeout(90_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const original = (await command(request, { cmd: 'get_global_settings' })).settings as Row;
  const group = `Text boundaries ${Date.now()}`;
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'settings' } });
  const writes: Row[] = []; let refuseGroup = true;
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (/^(update_global_settings|update_group_settings|engineer_update_settings|update_architect_settings)$/.test(String(data.cmd))) writes.push(data);
    if (data.cmd === 'update_group_settings' && refuseGroup) { refuseGroup = false; await route.fulfill({ json: { ok: true, data: { type: 'error', message: 'Text fixture refused' } } }); return; }
    await route.continue();
  });
  try {
    await page.goto('/'); await expect(page.getByLabel('Default directory', { exact: true })).toBeVisible();
    for (const [, , label, draft] of edits) {
      await page.getByRole('searchbox', { name: 'Search settings' }).fill(label);
      await page.getByRole('button', { name: new RegExp(`^${label} — `) }).click();
      await page.getByLabel(label, { exact: true }).fill(draft);
    }
    await page.getByRole('button', { name: 'Save changes', exact: true }).click();
    await expect(page.getByRole('alert')).toContainText('Text fixture refused');
    await expect(page.getByLabel('Default directory', { exact: true })).toHaveValue('  /tmp/my project  ');
    await expect(page.getByLabel('Custom instructions', { exact: true })).toHaveValue('  first line\n  second line\n');
    expect(writes.map((row) => row.cmd)).toEqual(['update_global_settings', 'update_group_settings']);
    await page.getByRole('button', { name: 'Save changes', exact: true }).click();
    await expect(page.getByText('Saved', { exact: true })).toBeVisible();
    expect(writes.map((row) => row.cmd)).toEqual(['update_global_settings', 'update_group_settings', 'update_group_settings', 'engineer_update_settings', 'update_architect_settings']);
    const global = (await command(request, { cmd: 'get_global_settings' })).settings as Row;
    const frame = await command(request, { cmd: 'get_group_settings', group });
    const scopes: Record<string, Row> = { global, group: frame.settings as Row, engineer: frame.engineer_settings as Row, architect: frame.architect_settings as Row };
    for (const [scope, key, , , expected] of edits) {
      const cmd = scope === 'engineer' ? 'engineer_update_settings' : `update_${scope}_settings`;
      const write = writes.filter((row) => row.cmd === cmd).at(-1)!;
      expect((scope === 'engineer' ? write : write.settings as Row)[key], `${scope}.${key} payload`).toEqual(expected);
      // The existing Architect backend trims outer instruction whitespace;
      // the client preserves the authored payload, as Classic does.
      const stored = key === 'architect_custom_instructions' ? String(expected).trim() : expected;
      expect(scopes[scope]?.[key], `${scope}.${key}`).toEqual(stored);
    }
    await page.reload(); await expect(page.getByLabel('Default directory', { exact: true })).toHaveValue('/tmp/my project');
    for (const [, key, label, , expected] of edits) await expect(page.getByLabel(label, { exact: true })).toHaveValue(key === 'architect_custom_instructions' ? String(expected).trim() : Array.isArray(expected) ? expected.join('\n') : expected as string);
    await page.getByLabel('Default directory', { exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: test.info().outputPath('settings-text-boundaries.png') });
  } finally {
    await command(request, { cmd: 'update_global_settings', settings: { default_command: original.default_command } });
  }
});
