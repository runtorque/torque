import { mkdtempSync, mkdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) { const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row }; expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data; }
for (const kind of ['role', 'template', 'specialization', 'action', 'class']) test(`${kind} authoring recovers from real listing and save deadlines without replay`, async ({ page, request }) => {
  test.setTimeout(90_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime; expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const project = mkdtempSync(join(tmpdir(), 'torque-authoring-deadline-')); const group = `Authoring ${kind} ${Date.now()}`; const name = `deadline-${kind}`;
  const key = kind === 'class' ? 'agent_classes' : kind === 'specialization' ? 'specializations' : `${kind}s`; const listCommand = kind === 'class' ? 'agent_class_list' : `list_${key}`; const saveCommand = kind === 'class' ? 'agent_class_update' : `save_${kind}`;
  const detailKind = kind === 'specialization' ? 'specialization' : kind === 'action' ? 'action' : 'template'; const titles: Record<string, string> = { role: 'Roles', template: 'Templates', specialization: 'Specializations', action: 'Actions' };
  let holdList = true; let holdWrite = true; let writeHeld = false; const releaseList: (() => void)[] = []; let releaseWrite = () => {}; const writes: Row[] = [];
  try {
    mkdirSync(join(project, '.torque', kind === 'class' ? 'agent_classes' : kind === 'action' ? 'actions' : kind === 'specialization' ? 'specializations' : 'roles'), { recursive: true });
    await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: project, git_worktree: false } }); await command(request, { cmd: 'ui_select_group', group }); await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'control', controlTab: kind === 'action' ? 'actions' : 'catalog' } });
    if (kind === 'class') await command(request, { cmd: 'agent_class_create', base_dir: project, agent_class: { agent_class_schema_version: 5, id: name, display_name: `Deadline ${kind}`, version: '1', base_kind: 'engineer', lifecycle: 'stable', description: 'Original purpose', prompt: { job: 'Keep scoped verification' }, acl: { mode: 'allow', rules: [] } } });
    else await command(request, { cmd: saveCommand, group, name, scope: 'project', [kind === 'action' ? 'action' : 'data']: { name, description: 'Original purpose', ...(kind === 'action' ? { prompt: '{{ TASK }}' } : { preamble: 'Keep scoped verification', priorities: ['Evidence'] }) } });
    let socket: WebSocketRoute | undefined; let connections = 0; await page.routeWebSocket(/\/ws\?/, (route) => { route.connectToServer(); socket = route; connections++; });
    const reconnect = async () => { const previous = connections; await socket!.close({ code: 1012, reason: 'Authoring deadline recovery' }); await expect.poll(() => connections).toBeGreaterThan(previous); };
    await page.route('**/api/cmd', async (route) => { const data = route.request().postDataJSON() as Row;
      if (data.cmd === listCommand && holdList) await new Promise<void>((resolve) => { releaseList.push(resolve); });
      if (data.cmd === saveCommand) { writes.push(data); if (holdWrite) { const response = await route.fetch(); writeHeld = true; await new Promise<void>((resolve) => { releaseWrite = resolve; }); await route.fulfill({ response }).catch(() => {}); return; } }
      await route.continue().catch(() => {});
    });
    await page.goto('/'); const library = kind === 'class' ? page.getByRole('heading', { name: 'Agent Classes', exact: true }).locator('xpath=ancestor::section[1]') : page.getByRole('region', { name: `${titles[kind]} library`, exact: true });
    await expect(library.getByRole('alert')).toContainText('refresh timed out', { timeout: 20_000 }); holdList = false; await library.getByRole('button', { name: kind === 'class' ? 'Retry classes' : 'Retry catalog', exact: true }).click(); await library.getByRole('button', { name: new RegExp(kind === 'class' ? '^Deadline class ' : `${name}\\s*project`) }).click(); releaseList.forEach((release) => release());
    const description = library.getByRole('textbox', { name: 'Description', exact: true }); await expect(description).toHaveValue('Original purpose'); await description.fill('Persist this retained draft'); await description.evaluate((node: HTMLInputElement) => { node.dataset.authoringAnchor = 'same'; node.setSelectionRange(2, 8); }); const save = () => library.getByRole('button', { name: kind === 'action' ? 'Save action' : 'Save', exact: true });
    await save().click(); await expect.poll(() => writeHeld).toBe(true); await expect(description).toBeDisabled(); await reconnect(); expect(writes).toHaveLength(1); await expect(library.getByRole('alert')).toContainText('outcome is unknown', { timeout: 35_000 }); await expect(description).toBeEnabled(); await expect(description).toHaveValue('Persist this retained draft'); await expect(description).toHaveAttribute('data-authoring-anchor', 'same'); releaseWrite(); await expect(library.getByRole('status')).toHaveCount(0); await expect(save()).toBeEnabled();
    await description.focus(); await description.evaluate((node: HTMLInputElement) => node.setSelectionRange(2, 8)); await reconnect(); await expect(description).toBeFocused(); expect(await description.evaluate((node: HTMLInputElement) => [node.selectionStart, node.selectionEnd])).toEqual([2, 8]); expect(writes).toHaveLength(1);
    await description.scrollIntoViewIfNeeded(); await page.screenshot({ path: test.info().outputPath(`${kind}-unknown-outcome.png`) });
    holdWrite = false; await description.fill('Explicitly confirmed draft'); await save().click(); await expect(library.getByRole('status')).toContainText(name); expect(writes).toHaveLength(2);
    const saved = kind === 'class' ? ((await command(request, { cmd: 'agent_class_list', base_dir: project })).classes as Row[]).find((row) => row.id === name)! : (await command(request, { cmd: `get_${detailKind}`, group, name, scope: 'project' }))[detailKind] as Row; expect(saved.description).toBe('Explicitly confirmed draft');
    await page.reload(); await library.getByRole('button', { name: new RegExp(kind === 'class' ? '^Deadline class ' : `${name}\\s*project`) }).click(); await expect(description).toHaveValue('Explicitly confirmed draft');
  } finally { releaseList.forEach((release) => release()); releaseWrite(); rmSync(project, { recursive: true, force: true }); }
});
