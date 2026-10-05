import { expect, test, type APIRequestContext, type Page, type WebSocketRoute } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}
async function settings(page: Page) {
  await page.getByRole('button', { name: /◎ Control/ }).click();
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByRole('searchbox', { name: 'Search settings' }).fill('Maximum agents');
  await page.getByRole('button', { name: /^Maximum agents — / }).click();
  return page.getByLabel('Maximum agents', { exact: true });
}
async function setLimit(page: Page, limit: number) {
  await (await settings(page)).fill(String(limit));
  await page.getByRole('button', { name: 'Save changes', exact: true }).click();
  await expect(page.getByText('Saved', { exact: true })).toBeVisible();
}

test('Saved agent capacity counts retained agents, releases deletions, preserves reconnect drafts and allows exact creation recovery', async ({ page, request }) => {
  test.setTimeout(120_000); page.setDefaultTimeout(12_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Capacity ${Date.now()}`; const other = `${group} other`; const ids: string[] = [];
  const writes: Row[] = []; let loseNext = false; let socket: WebSocketRoute | undefined; let connections = 0;
  await page.routeWebSocket(/\/ws\?/, (client) => { socket = client; connections++; client.connectToServer(); });
  for (const name of [group, other]) {
    await command(request, { cmd: 'add_group', group: name });
    await command(request, { cmd: 'update_group_settings', group: name, settings: { default_directory: '/private/tmp', default_agent_template: '', agent_provider: 'generic', agent_boot_command: '/bin/cat', git_worktree: false, notifications: false } });
  }
  const add = async (name: string, target = group, kind = 'worker') => {
    const id = String((await command(request, { cmd: `add_${kind}`, group: target, name })).id); ids.push(id); return id;
  };
  const remove = async (id: string) => { await command(request, { cmd: 'remove_agent', id }); };
  const cell = async (id: string) => ((await command(request, { cmd: 'get_state' })).agents as Record<string, Row>)[id]!;
  await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: 'settings' } });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (!['add_worker', 'add_terminal'].includes(String(data.cmd))) { await route.continue(); return; }
    writes.push(data); const response = await route.fetch(); const body = await response.json() as { ok: boolean; data: Row };
    expect(body.ok).toBe(true); if (typeof body.data.id === 'string' && !ids.includes(body.data.id)) ids.push(body.data.id);
    if (loseNext) { loseNext = false; await route.abort('failed'); } else await route.fulfill({ response });
  });
  try {
    const original = await add('Original'); await add('Foreign', other); await add('Standalone', group, 'terminal');
    await page.goto('/'); await setLimit(page, 1); await page.reload();
    await expect(await settings(page)).toHaveValue('1');
    expect((await command(request, { cmd: 'get_group_settings', group })).settings).toMatchObject({ max_agents: 1 });
    await page.getByRole('button', { name: /Agents/ }).click();
    const newMenu = page.getByRole('button', { name: 'Create agent or terminal' }); await newMenu.click();
    await expect(page.getByRole('menuitem', { name: 'Agent limit reached (1/1)' })).toBeDisabled();
    for (const kind of ['Architect', 'Engineer', 'Worker']) await expect(page.getByRole('menuitem', { name: `New ${kind}…` })).toBeDisabled();
    await page.getByRole('menuitem', { name: 'New Terminal…' }).click();
    const terminal = page.getByRole('dialog', { name: 'New terminal' }); await terminal.getByLabel('Name', { exact: true }).fill('At capacity terminal');
    await terminal.getByRole('button', { name: 'Create terminal', exact: true }).click(); await expect(terminal).toHaveCount(0);
    await remove(original); expect(Number((await cell(original)).deleted_at)).toBeGreaterThan(0);
    await newMenu.click(); await expect(page.getByRole('menuitem', { name: 'New Worker…' })).toBeEnabled();
    await page.getByRole('menuitem', { name: 'New Worker…' }).click();
    const creation = page.getByRole('dialog', { name: 'New worker' }); const name = creation.getByLabel('Name', { exact: true });
    await name.fill('Retained capacity draft'); await creation.getByLabel('Boot command', { exact: true }).fill('/bin/cat');
    await name.focus(); await name.evaluate((element: HTMLInputElement) => element.setSelectionRange(4, 9));
    const external = await add('External');
    await expect(creation.getByText(/Agent limit reached \(1\/1\)/)).toBeVisible();
    await expect(creation.getByRole('button', { name: 'Create worker', exact: true })).toBeDisabled();
    const before = connections; await socket!.close({ code: 1012, reason: 'Capacity draft reconnect' });
    await expect.poll(() => connections).toBeGreaterThan(before);
    await expect(name).toBeFocused(); await expect(name).toHaveValue('Retained capacity draft');
    expect(await name.evaluate((element: HTMLInputElement) => [element.selectionStart, element.selectionEnd])).toEqual([4, 9]);
    await expect(creation.getByLabel('Boot command', { exact: true })).toHaveValue('/bin/cat');
    await page.screenshot({ path: test.info().outputPath('capacity-draft.png'), animations: 'disabled' });
    const priorWrites = writes.length; await name.press('Enter'); expect(writes).toHaveLength(priorWrites);
    await remove(external); await expect(creation.getByRole('button', { name: 'Create worker', exact: true })).toBeEnabled();
    loseNext = true; await creation.getByRole('button', { name: 'Create worker', exact: true }).click();
    await expect(creation.getByRole('button', { name: 'Retry same creation' })).toBeVisible();
    const launched = writes.at(-1)!; const created = ids.at(-1)!; expect((await cell(created)).name).toBe('Retained capacity draft');
    // The successful but unobserved launch consumes the last seat. Its exact retry must still work.
    await expect(creation.getByText(/Agent limit reached/)).toHaveCount(0);
    await creation.getByRole('button', { name: 'Retry same creation' }).click(); await expect(creation).toHaveCount(0);
    expect(writes.at(-1)).toEqual(launched);
    const state = await command(request, { cmd: 'get_state' });
    expect(Object.values(state.agents as Record<string, Row>).filter((agent) => agent.name === 'Retained capacity draft')).toHaveLength(1);
    await newMenu.click(); await expect(page.getByRole('menuitem', { name: 'Agent limit reached (1/1)' })).toBeVisible(); await page.keyboard.press('Escape');
    await setLimit(page, 0); await page.reload(); await expect(await settings(page)).toHaveValue('0');
    const unlimited = await add('Unlimited'); expect((await cell(unlimited)).name).toBe('Unlimited');
    await page.getByRole('button', { name: /Agents/ }).click(); await newMenu.click();
    await expect(page.getByRole('menuitem', { name: 'New Worker…' })).toBeEnabled();
    const path = test.info().outputPath('capacity-evidence.json'); await writeFile(path, JSON.stringify({ group, original: await cell(original), external: await cell(external), created: await cell(created), writes }, null, 2)); await test.info().attach('capacity-evidence', { path, contentType: 'application/json' });
  } finally {
    for (const id of ids) { const current = await cell(id); if (!current) continue; if (!(Number(current.deleted_at) > 0)) await remove(id); if (await cell(id)) await command(request, { cmd: 'purge_agent_now', id }); }
  }
});
