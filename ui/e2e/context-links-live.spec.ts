import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}
test('Context publishes optional task, pipeline and agent links, retains failed drafts and navigates saved targets', async ({ page, request }) => {
  test.setTimeout(60_000);
  const runtime = await (await request.get('/api/runtime')).json() as { data: { runtime: { port: number; profile: string } } };
  expect(runtime.data.runtime.port).not.toBe(18932); expect(runtime.data.runtime.profile).not.toBe('default');
  const group = `Context links ${Date.now()}`; const rootGroup = `${group} roots`;
  await command(request, { cmd: 'add_group', group: rootGroup });
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: '/private/tmp', git_worktree: false } });
  const root = String((await command(request, { cmd: 'board_add_task', group: rootGroup, task: 'Linked pipeline root' })).task_id);
  const task = String((await command(request, { cmd: 'board_add_task', group, task: 'Linked child task', parent_task_id: root, pipeline_root_id: root, pipeline_depth: 1 })).task_id);
  const obsolete = String((await command(request, { cmd: 'board_add_task', group, task: 'Disappearing link target' })).task_id);
  const agent = String((await command(request, { cmd: 'add_engineer', group, name: 'Context linked Engineer', provider: 'generic', command: '/bin/cat', directory: '/private/tmp' })).id);
  const otherAgent = String((await command(request, { cmd: 'add_engineer', group, name: 'Other Context Engineer', provider: 'generic', command: '/bin/cat', directory: '/private/tmp' })).id);
  try {
    let socket: WebSocketRoute | undefined; let connections = 0; let detailFrames = 0; let refuse = false; const publications: Row[] = [];
    await page.routeWebSocket(/\/ws\?/, (connection) => { const server = connection.connectToServer(); server.onMessage((message) => { if (typeof message === 'string' && (JSON.parse(message) as Row).type === 'task_detail') detailFrames += 1; connection.send(message); }); socket = connection; connections += 1; });
    await page.route('**/api/cmd', async (route) => {
      const data = route.request().postDataJSON() as Row;
      if (data.cmd === 'memory_publish') publications.push(data);
      if (refuse && data.cmd === 'memory_publish') await route.fulfill({ json: { ok: false, error: 'Injected linked publication refusal' } }); else await route.continue();
    });
    await page.goto('/'); await page.getByRole('button', { name: /⌁ Agents/ }).click(); await page.getByRole('treeitem', { name: /Other Context Engineer, engineer/ }).click(); await expect(page.getByRole('treeitem', { name: /Other Context Engineer, engineer/ })).toHaveAttribute('aria-selected', 'true'); await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Context', exact: true }).click();
    await page.getByRole('button', { name: '＋ Add context', exact: true }).click();
    const title = page.getByRole('textbox', { name: 'Title', exact: true }); const content = page.getByRole('textbox', { name: 'Content', exact: true });
    await title.fill('Linked shared context'); await content.fill('Draft and links survive reconnect and refusal.');
    const addLink = async (kind: string, id: string) => {
      await page.getByRole('combobox', { name: 'Link kind', exact: true }).selectOption(kind);
      await page.getByRole('textbox', { name: 'Search link targets', exact: true }).fill(group);
      if (kind === 'pipeline') await expect(page.getByRole('combobox', { name: 'Link target', exact: true }).locator('option')).toHaveText(['Choose a pipeline…', `Linked pipeline root · ${rootGroup} · ${root}`]);
      await page.getByRole('textbox', { name: 'Search link targets', exact: true }).fill(id);
      await page.getByRole('combobox', { name: 'Link target', exact: true }).selectOption(id);
      await page.getByRole('button', { name: 'Add link', exact: true }).click(); await expect(page.getByRole('button', { name: 'Add link', exact: true })).toBeDisabled();
    };
    await addLink('task', obsolete); await command(request, { cmd: 'board_remove_task', id: obsolete });
    await expect(page.getByText(/Unavailable — remove this link/)).toBeVisible(); await expect(page.getByRole('button', { name: 'Publish context', exact: true })).toBeDisabled(); expect(publications).toHaveLength(0);
    await page.getByRole('button', { name: `Remove link task: ${obsolete}`, exact: true }).click();
    await addLink('task', task); await addLink('pipeline', root); await addLink('agent', agent);
    await expect(page.getByRole('button', { name: /Remove link/ })).toHaveCount(3);
    await content.focus(); await content.evaluate((node: HTMLTextAreaElement) => { node.setSelectionRange(2, 8); node.dataset.linkDraftAnchor = 'original'; });
    const before = connections; await socket!.close({ code: 1012, reason: 'Context link draft reconnect' }); await expect.poll(() => connections).toBeGreaterThan(before);
    await expect(content).toBeFocused(); await expect(content).toHaveAttribute('data-link-draft-anchor', 'original'); expect(await content.evaluate((node: HTMLTextAreaElement) => [node.selectionStart, node.selectionEnd])).toEqual([2, 8]);
    await expect(page.getByRole('button', { name: /Remove link/ })).toHaveCount(3);
    refuse = true; await page.getByRole('button', { name: 'Publish context', exact: true }).click(); await expect(page.getByRole('alert')).toContainText('Injected linked publication refusal'); await expect(content).toHaveValue('Draft and links survive reconnect and refusal.');
    refuse = false; await page.getByRole('button', { name: 'Publish context', exact: true }).click(); await expect(page.getByRole('heading', { name: 'Linked shared context' })).toBeVisible();
    const links = [{ target_kind: 'task', target_ref: task }, { target_kind: 'pipeline', target_ref: root }, { target_kind: 'agent', target_ref: agent }];
    expect(publications).toHaveLength(2); for (const sent of publications) expect(sent).toMatchObject({ scope_kind: 'group', scope_ref: group, link_targets: links });
    const entries = (await command(request, { cmd: 'memory_list', group_name: group })).entries as Row[]; expect(entries).toHaveLength(1); const entry = entries[0]!;
    expect(entry.links).toEqual(expect.arrayContaining(links.map((link) => expect.objectContaining(link)))); expect(entry.links).toHaveLength(3);
    await expect(page.getByRole('button', { name: /Open linked/ })).toHaveCount(3);
    await page.getByRole('button', { name: 'Edit', exact: true }).click(); await content.fill('Edited without changing links'); await page.getByRole('button', { name: 'Save context', exact: true }).click(); await expect(content).toHaveCount(0);
    expect(publications.at(-1)).toEqual({ cmd: 'memory_publish', entry_id: entry.id, content: 'Edited without changing links' });
    expect((await command(request, { cmd: 'memory_read', entry_id: entry.id })).entry).toMatchObject({ links: entry.links });
    for (const [kind, label] of [['task', 'Linked child task'], ['pipeline', 'Linked pipeline root']]) {
      await page.getByRole('button', { name: `Open linked ${kind} ${label}`, exact: true }).click();
      const dialog = page.getByRole('dialog'); const taskTitle = dialog.getByRole('textbox', { name: 'Title', exact: true }); await expect(taskTitle).toHaveValue(label!);
      if (kind === 'task') {
        await taskTitle.fill('Unsaved linked task title'); await taskTitle.focus(); await taskTitle.evaluate((node: HTMLInputElement) => { node.setSelectionRange(2, 7); node.dataset.taskLinkAnchor = 'retained'; });
        const beforeDetail = detailFrames; await socket!.close({ code: 1012, reason: 'Open linked task editor reconnect' }); await expect.poll(() => detailFrames).toBeGreaterThan(beforeDetail);
        await expect(taskTitle).toHaveValue('Unsaved linked task title'); await expect(taskTitle).toHaveAttribute('data-task-link-anchor', 'retained'); await expect(taskTitle).toBeFocused();
        expect(await taskTitle.evaluate((node: HTMLInputElement) => [node.selectionStart, node.selectionEnd])).toEqual([2, 7]);
      }
      await dialog.getByRole('button', { name: 'Cancel', exact: true }).click(); await page.getByRole('button', { name: group, exact: true }).click(); await page.getByRole('button', { name: /◎ Control/ }).click();
      await expect(page.getByRole('heading', { name: 'Linked shared context' })).toBeVisible();
    }
    await page.getByRole('button', { name: 'Open linked agent Context linked Engineer', exact: true }).click();
    await expect(page.getByRole('treeitem', { name: /Context linked Engineer, engineer/ })).toHaveAttribute('aria-selected', 'true');
    await page.getByRole('button', { name: /◎ Control/ }).click(); await expect(page.getByRole('heading', { name: 'Linked shared context' })).toBeVisible();
    await page.screenshot({ path: test.info().outputPath('context-links.png'), fullPage: true });
    await page.getByRole('button', { name: '＋ Add context', exact: true }).click(); await content.fill('Cancelled linked draft'); await addLink('task', task); await page.getByRole('button', { name: 'Cancel', exact: true }).click();
    expect((await command(request, { cmd: 'memory_list', group_name: group })).entries).toHaveLength(1);
  } finally { for (const id of [agent, otherAgent]) await command(request, { cmd: 'remove_agent', id }); }
});
