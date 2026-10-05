import { expect, test, type APIRequestContext } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}
const fields = [
  ['Default terminal backend', 'legacy-metadata'], ['Profile', 'Group profile'], ['Tab color', '#112233'],
  ['Agent terminal profile', 'Agent profile'], ['Agent tab color', '#223344'],
  ['Terminal profile', 'Terminal profile'], ['Terminal tab color', '#334455'],
  ['Engineer profile', 'Engineer profile'], ['Engineer tab color', '#445566'],
  ['Architect profile', 'Architect profile'], ['Architect tab color', '#556677'],
] as const;

test('Stored launch profile and color defaults retain metadata and inheritance without claiming embedded terminal effects', async ({ page, request }) => {
  test.setTimeout(90_000); page.setDefaultTimeout(12_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default'); expect(runtime.terminal_backend).toBe('pty');
  const group = `Launch metadata ${Date.now()}`; const ids: string[] = []; const evidence: Row[] = [];
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: '/private/tmp', agent_provider: 'generic', agent_boot_command: '/bin/cat', terminal_boot_command: '/bin/cat', default_agent_template: '', git_worktree: false } });
  await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'settings' } });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (!/^add_(worker|engineer|architect|terminal)$/.test(String(data.cmd))) { await route.continue(); return; }
    const response = await route.fetch(); const body = await response.json() as { data: Row }; if (typeof body.data.id === 'string') ids.push(body.data.id); await route.fulfill({ response });
  });
  const openSettings = async () => { await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Settings', exact: true }).click(); };
  const reveal = async (label: string) => { await page.getByRole('searchbox', { name: 'Search settings' }).fill(label); await page.getByRole('button', { name: new RegExp(`^${label} — `) }).click(); return page.getByRole('textbox', { name: label, exact: true }); };
  const save = async () => { await page.getByRole('button', { name: 'Save changes', exact: true }).click(); await expect(page.getByText('Saved', { exact: true })).toBeVisible(); };
  const launch = async (kind: 'worker' | 'engineer' | 'architect' | 'terminal', phase: string, profile: string, color: string) => {
    await page.getByRole('button', { name: /⌁ Agents/ }).click(); await page.getByRole('button', { name: 'Create agent or terminal' }).click();
    await page.getByRole('menuitem', { name: `New ${kind[0]!.toUpperCase()}${kind.slice(1)}…` }).click();
    const dialog = page.getByRole('dialog', { name: `New ${kind}` }); await dialog.getByLabel('Name', { exact: true }).fill(`${phase} ${kind}`);
    await dialog.getByRole('button', { name: `Create ${kind}`, exact: true }).click(); await expect(dialog).toHaveCount(0);
    const id = ids.at(-1)!; expect(id).toBeTruthy(); let cell: Row = {};
    await expect.poll(async () => { cell = ((await command(request, { cmd: 'get_state' })).agents as Record<string, Row>)[id]!; return cell; }).toMatchObject({ name: `${phase} ${kind}`, profile, tab_color: color, terminal_backend: 'pty' });
    expect(cell.session_id).toBeTruthy(); evidence.push({ phase, kind, id, profile: cell.profile, tab_color: cell.tab_color, terminal_backend: cell.terminal_backend });
  };
  try {
    await page.goto('/');
    for (const [label, value] of fields) {
      const input = await reveal(label); await input.fill(value);
      await expect(input).toHaveAccessibleDescription(label === 'Default terminal backend' ? /embedded PTY backend regardless of this setting/ : label.toLowerCase().includes('profile') ? /Embedded terminals do not apply terminal-emulator profiles/ : /do not apply tab colors/);
    }
    await save(); await page.reload();
    for (const [label, value] of fields) await expect(await reveal(label)).toHaveValue(value);
    await launch('worker', 'Specific', 'Agent profile', '#223344'); await launch('engineer', 'Specific', 'Engineer profile', '#445566'); await launch('architect', 'Specific', 'Architect profile', '#556677'); await launch('terminal', 'Specific', 'Terminal profile', '#334455');
    await openSettings();
    for (const [label] of fields.slice(3)) await (await reveal(label)).fill('');
    await save(); await page.reload();
    for (const [label] of fields.slice(3)) await expect(await reveal(label)).toHaveValue('');
    for (const kind of ['worker', 'engineer', 'architect', 'terminal'] as const) await launch(kind, 'Fallback', 'Group profile', '#112233');
    await openSettings(); await (await reveal('Profile')).fill(''); await (await reveal('Tab color')).fill(''); await save(); await page.reload();
    await expect(await reveal('Profile')).toHaveValue(''); await expect(await reveal('Tab color')).toHaveValue('');
    await launch('worker', 'Cleared', 'Default', '');
    await openSettings(); await reveal('Engineer profile'); await page.screenshot({ path: test.info().outputPath('launch-metadata-guidance.png'), animations: 'disabled' });
    const output = test.info().outputPath('launch-metadata-evidence.json'); await writeFile(output, JSON.stringify(evidence, null, 2)); await test.info().attach('launch-metadata-evidence', { path: output, contentType: 'application/json' });
  } finally {
    for (const id of [...ids].reverse()) await command(request, { cmd: 'remove_agent', id });
  }
});
