import { mkdtempSync, mkdirSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const response = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(response.ok, response.error).toBe(true); expect(response.data.type).not.toBe('error'); return response.data;
}
for (const kind of ['role', 'template', 'specialization']) test(`${kind} catalog loads full definitions, retains reconnect drafts and acknowledges duplicate/save/delete`, async ({ page, request }) => {
  test.setTimeout(90_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: { port: number; profile: string } } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const project = mkdtempSync(join(tmpdir(), 'torque-catalog-parity-')); const folder = kind === 'specialization' ? 'specializations' : 'roles'; mkdirSync(join(project, '.torque', folder), { recursive: true });
  const title = kind === 'specialization' ? 'Specializations' : kind === 'template' ? 'Templates' : 'Roles'; const listKey = title.toLowerCase(); const detailKind = kind === 'specialization' ? 'specialization' : 'template';
  const name = `parity-${kind}`; const group = `Catalog ${kind} ${Date.now()}`;
  const definition: Row = { name, description: 'Original description', preamble: 'Original preamble', priorities: ['Keep first', 'Keep second'], ...(kind === 'specialization' ? {} : { provider: 'generic', model: 'persisted-model', system_prompt: 'Preserved system prompt', initial_prompt: 'Preserved initial prompt', session_resume: false, max_turns: 0, idle_timeout: 3, worktree: false, worktree_merge_squash: false, reasoning_effort: 'high', env_vars: { KEEP: 'a=b' }, terminals: [{ name: 'watch', command: 'make watch' }] }) };
  try {
    await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: project, git_worktree: false } }); await command(request, { cmd: 'ui_select_group', group });
    await command(request, { cmd: `save_${kind}`, name, scope: 'project', group, data: definition });
    let socket: WebSocketRoute | undefined; let connections = 0; let refusal = ''; let hold = false; let release: (() => void) | undefined; const calls: Row[] = [];
    await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections += 1; });
    const reconnect = async () => { const before = connections; await socket!.close({ code: 1012, reason: 'Catalog reconnect' }); await expect.poll(() => connections).toBeGreaterThan(before); };
    await page.route('**/api/cmd', async (route) => {
      const data = route.request().postDataJSON() as Row; calls.push(data);
      if (data.cmd === refusal) { await route.fulfill({ json: { ok: true, data: { type: 'error', message: 'Injected catalog refusal' } } }); return; }
      if (hold && data.cmd === `save_${kind}`) { const response = await route.fetch(); await new Promise<void>((resolve) => { release = resolve; }); await route.fulfill({ response }); return; }
      await route.continue();
    });
    await page.goto('/'); await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Catalog', exact: true }).click(); const library = page.getByRole('region', { name: `${title} library`, exact: true });
    await library.getByRole('button', { name: new RegExp(name) }).click(); const preamble = library.getByRole('textbox', { name: 'Preamble', exact: true }); await expect(preamble).toHaveValue('Original preamble');
    await expect(library.getByText(`Source: ${join(project, '.torque', folder, `${name}.yaml`)}`, { exact: true })).toBeVisible();
    if (kind !== 'specialization') await library.getByText('Appearance', { exact: true }).click();
    await preamble.fill('Local catalog draft'); await preamble.evaluate((node: HTMLTextAreaElement) => { node.setSelectionRange(2, 7); node.dataset.catalogAnchor = 'original'; });
    const scrollBefore = await preamble.evaluate((node) => node.closest('form')!.parentElement!.scrollTop);
    await command(request, { cmd: `save_${kind}`, name, scope: 'project', group, data: { ...definition, description: 'Remote description', preamble: 'Remote preamble' } });
    await reconnect(); await expect(library.getByRole('textbox', { name: 'Description', exact: true })).toHaveValue('Remote description'); await expect(preamble).toHaveValue('Local catalog draft'); await expect(preamble).toBeFocused(); await expect(preamble).toHaveAttribute('data-catalog-anchor', 'original'); expect(await preamble.evaluate((node: HTMLTextAreaElement) => [node.selectionStart, node.selectionEnd])).toEqual([2, 7]);
    expect(await preamble.evaluate((node) => node.closest('form')!.parentElement!.scrollTop)).toBeCloseTo(scrollBefore, 0);
    if (kind !== 'specialization') await expect(library.locator('details').filter({ has: page.getByText('Appearance', { exact: true }) })).toHaveAttribute('open', '');
    refusal = `get_${detailKind}`; await reconnect(); await expect(library.getByRole('alert')).toContainText('Definition refresh failed'); await expect(preamble).toHaveValue('Local catalog draft'); refusal = ''; await library.getByRole('button', { name: 'Retry definition', exact: true }).click(); await expect(library.getByRole('alert')).toHaveCount(0);
    refusal = `list_${listKey}`; await reconnect(); await expect(library.getByRole('alert')).toContainText('Catalog refresh failed'); refusal = ''; await library.getByRole('button', { name: 'Retry catalog', exact: true }).click(); await expect(library.getByRole('alert')).toHaveCount(0);
    await library.getByRole('textbox', { name: 'Priorities (one per line)', exact: true }).fill('New first\nKeep second');
    if (kind !== 'specialization') {
      await expect(library.getByRole('textbox', { name: 'Model', exact: true })).toHaveValue('persisted-model'); await expect(library.getByRole('spinbutton', { name: 'Max turns', exact: true })).toHaveValue('0');
      await library.getByText('Environment and terminals', { exact: true }).click(); await library.getByRole('button', { name: 'Add terminal', exact: true }).click(); await library.getByRole('textbox', { name: 'Terminal 2 name', exact: true }).fill('extra'); await library.getByRole('textbox', { name: 'Terminal 2 command', exact: true }).fill('echo ready');
    }
    refusal = `save_${kind}`; await library.getByRole('button', { name: 'Save', exact: true }).click(); await expect(library.getByRole('alert')).toContainText('Injected catalog refusal'); await expect(preamble).toHaveValue('Local catalog draft'); refusal = ''; await library.getByRole('button', { name: 'Save', exact: true }).click(); await expect(library.getByRole('status')).toContainText(`Saved project ${name}`);
    const saved = (await command(request, { cmd: `get_${detailKind}`, name, scope: 'project', group }))[detailKind] as Row; expect(saved).toMatchObject({ ...definition, preamble: 'Local catalog draft', description: 'Remote description', priorities: ['New first', 'Keep second'], ...(kind === 'specialization' ? {} : { terminals: [...definition.terminals as Row[], { name: 'extra', command: 'echo ready' }] }) });
    const yaml = readFileSync(join(project, '.torque', folder, `${name}.yaml`), 'utf8'); expect(yaml).toContain('Local catalog draft'); expect(yaml).not.toContain('shadowed:'); expect(yaml).not.toContain('global:');
    const writes = calls.filter((call) => call.cmd === `save_${kind}`).length; await library.getByRole('button', { name: 'Duplicate', exact: true }).click(); await expect(library.getByRole('textbox', { name: 'Name', exact: true })).toHaveValue(`${name}-copy`); expect(calls.filter((call) => call.cmd === `save_${kind}`)).toHaveLength(writes);
    hold = true; await library.getByRole('button', { name: 'Save', exact: true }).click(); await expect.poll(() => Boolean(release)).toBe(true); await expect(library.getByRole('heading', { name: `New ${kind}`, exact: true })).toBeVisible(); await expect(library.getByRole('button', { name: `New ${kind}`, exact: true })).toBeDisabled();
    refusal = `list_${listKey}`; hold = false; release!(); await expect(library.getByRole('heading', { name: `Edit ${name}-copy`, exact: true })).toBeVisible(); await expect(library.getByRole('alert')).toContainText('Catalog refresh failed'); const completedWrites = calls.filter((call) => call.cmd === `save_${kind}`).length; refusal = ''; await library.getByRole('button', { name: 'Retry catalog', exact: true }).click(); await expect(library.getByRole('alert')).toHaveCount(0); expect(calls.filter((call) => call.cmd === `save_${kind}`)).toHaveLength(completedWrites);
    await library.getByRole('textbox', { name: 'Name', exact: true }).fill(`${name}-renamed`); await library.getByRole('button', { name: 'Save', exact: true }).click(); await expect(library.getByRole('heading', { name: `Edit ${name}-renamed`, exact: true })).toBeVisible();
    expect((await command(request, { cmd: `list_${listKey}`, group }))[listKey]).toEqual(expect.arrayContaining([expect.objectContaining({ name }), expect.objectContaining({ name: `${name}-renamed` })]));
    await library.getByRole('button', { name: 'Delete', exact: true }).click(); refusal = `delete_${kind}`; await page.getByRole('dialog').getByRole('button', { name: 'Delete', exact: true }).click(); await expect(page.getByRole('dialog').getByRole('alert')).toContainText('Injected catalog refusal'); refusal = ''; await page.getByRole('dialog').getByRole('button', { name: 'Delete', exact: true }).click(); await expect(page.getByRole('dialog')).toHaveCount(0); await expect(library.getByRole('button', { name: new RegExp(`${name}-renamed`) })).toHaveCount(0);
    await library.getByRole('button', { name: new RegExp(name) }).click(); await expect(preamble).toHaveValue('Local catalog draft'); await preamble.scrollIntoViewIfNeeded(); await page.screenshot({ path: test.info().outputPath(`${kind}-catalog.png`) });
    await page.getByRole('button', { name: 'Mission Control', exact: true }).click(); const hidden = calls.filter((call) => [`list_${listKey}`, `get_${detailKind}`].includes(String(call.cmd))).length; await reconnect(); expect(calls.filter((call) => [`list_${listKey}`, `get_${detailKind}`].includes(String(call.cmd)))).toHaveLength(hidden);
  } finally { rmSync(project, { recursive: true, force: true }); }
});
