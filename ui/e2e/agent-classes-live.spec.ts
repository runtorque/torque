import { mkdtempSync, mkdirSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}
test('Agent Classes retain deny rules and drafts, validate authority, acknowledge saves and stage duplicate/archive/delete lifecycle', async ({ page, request }) => {
  test.setTimeout(90_000);
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: { port: number; profile: string } } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const project = mkdtempSync(join(tmpdir(), 'torque-class-parity-')); mkdirSync(join(project, '.torque', 'agent_classes'), { recursive: true });
  const group = `Agent Classes ${Date.now()}`; const id = 'parity-engineer';
  try {
    await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'update_group_settings', group, settings: { default_directory: project, git_worktree: false } }); await command(request, { cmd: 'ui_select_group', group });
    const definition: Row = { agent_class_schema_version: 5, id, version: '1', base_kind: 'engineer', display_name: 'Parity Engineer', description: 'Initial purpose', lifecycle: 'stable', acl: { mode: 'deny', rules: [{ capability: 'task.read' }] }, prompt: { identity: 'Keep specialist identity', job: 'Original job', boot_checklist: ['Keep the boot checklist'], operating_guidelines: ['Preserve project context'] }, metadata: { marker: 'keep metadata', ui: { badge: 'kept' } }, warnings: ['An authored class warning'] };
    const created = await command(request, { cmd: 'agent_class_create', base_dir: project, agent_class: definition }); expect(created.ok).toBe(true);
    const original = created.agent_class as Row; const originalAuthority = original.effective_authority;
    let socket: WebSocketRoute | undefined; let connections = 0; let refusal = ''; let holdSave = false; let releaseSave: (() => void) | undefined; const calls: Row[] = [];
    await page.routeWebSocket(/\/ws\?/, (connection) => { connection.connectToServer(); socket = connection; connections += 1; });
    const reconnect = async () => { const before = connections; await socket!.close({ code: 1012, reason: 'Class draft reconnect' }); await expect.poll(() => connections).toBeGreaterThan(before); };
    await page.route('**/api/cmd', async (route) => {
      const data = route.request().postDataJSON() as Row; if (String(data.cmd).startsWith('agent_class_')) calls.push(data);
      if (data.cmd === refusal) { await route.fulfill({ json: { ok: true, data: { type: 'error', message: 'Injected class refusal' } } }); return; }
      if (holdSave && data.cmd === 'agent_class_create') { const response = await route.fetch(); await new Promise<void>((resolve) => { releaseSave = resolve; }); await route.fulfill({ response }); return; }
      await route.continue();
    });
    await page.goto('/'); await page.getByRole('button', { name: /◎ Control/ }).click(); await page.getByRole('button', { name: 'Catalog', exact: true }).click();
    const library = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Agent Classes', exact: true }) }).last();
    await library.getByRole('button', { name: /^Parity Engineer / }).click();
    await expect(library.getByRole('combobox', { name: 'Lifecycle', exact: true }).locator('option')).toHaveText(['Stable', 'Draft']);
    await expect(library.getByRole('checkbox', { name: /Read tasks/ })).toBeChecked();
    const authority = page.getByRole('region', { name: 'Agent Class authority preview', exact: true }); await expect(authority.getByText('An authored class warning', { exact: true })).toBeVisible(); await expect(authority.getByText(/Saves and assignments do not change running sessions/)).toBeVisible();
    const job = library.getByRole('textbox', { name: 'Class job prompt', exact: true }); await job.fill('Local class job draft'); await job.evaluate((node: HTMLTextAreaElement) => { node.setSelectionRange(2, 8); node.dataset.classAnchor = 'original'; });
    await command(request, { cmd: 'agent_class_update', base_dir: project, agent_class: { ...definition, display_name: 'Remote Parity Engineer', prompt: { ...(definition.prompt as Row), job: 'Remote base job' } } });
    await reconnect(); await expect(library.getByRole('textbox', { name: 'Display name', exact: true })).toHaveValue('Remote Parity Engineer'); await expect(job).toHaveValue('Local class job draft'); await expect(job).toBeFocused(); await expect(job).toHaveAttribute('data-class-anchor', 'original'); expect(await job.evaluate((node: HTMLTextAreaElement) => [node.selectionStart, node.selectionEnd])).toEqual([2, 8]);
    refusal = 'agent_class_list'; await reconnect(); await expect(library.getByRole('alert')).toContainText('Class refresh failed'); await expect(job).toHaveValue('Local class job draft'); refusal = ''; await library.getByRole('button', { name: 'Retry classes', exact: true }).click(); await expect(library.getByRole('alert')).toHaveCount(0);
    await library.getByRole('button', { name: 'Validate', exact: true }).click(); await expect(library.getByText('Validation passed', { exact: true })).toBeVisible(); await expect(authority.getByRole('heading', { name: 'Validated draft authority', exact: true })).toBeVisible();
    await library.getByRole('textbox', { name: 'Description', exact: true }).fill('Updated purpose'); await expect(library.getByText('Validation passed', { exact: true })).toHaveCount(0);
    refusal = 'agent_class_update'; await library.getByRole('button', { name: 'Save', exact: true }).click(); await expect(library.getByRole('alert')).toContainText('Injected class refusal'); await expect(job).toHaveValue('Local class job draft'); refusal = ''; await library.getByRole('button', { name: 'Save', exact: true }).click(); await expect(library.getByRole('status')).toContainText(id);
    const saved = (await command(request, { cmd: 'agent_class_preview', base_dir: project, class_id: id })).agent_class as Row;
    expect(saved.effective_authority).toEqual(originalAuthority); expect(saved.authoring_definition).toMatchObject({ acl: definition.acl, prompt: { ...(definition.prompt as Row), job: 'Local class job draft' }, metadata: definition.metadata, warnings: definition.warnings });
    const yaml = readFileSync(join(project, '.torque', 'agent_classes', `${id}.yaml`), 'utf8'); expect(yaml).toContain('Keep specialist identity'); expect(yaml).toContain('Keep the boot checklist'); expect(yaml).toContain('keep metadata');
    const lifecycle = library.getByRole('combobox', { name: 'Lifecycle', exact: true });
    for (const value of ['draft', 'stable']) {
      await lifecycle.selectOption(value); await expect(library.getByRole('checkbox', { name: 'Scratch-only draft class', exact: true })).toBeChecked({ checked: value === 'draft' });
      await Promise.all([page.waitForResponse((response) => response.url().endsWith('/api/cmd') && (response.request().postDataJSON() as Row)?.cmd === 'agent_class_update'), library.getByRole('button', { name: 'Save', exact: true }).click()]);
      await expect(library.getByRole('button', { name: 'Save', exact: true })).toBeEnabled();
      const persisted = (await command(request, { cmd: 'agent_class_preview', base_dir: project, class_id: id })).agent_class as Row; expect(persisted.lifecycle).toBe(value); expect((persisted.authoring_definition as Row).draft).toEqual(value === 'draft' ? { scratch_only: true } : undefined);
    }
    const before = calls.filter((call) => call.cmd === 'agent_class_create').length; await library.getByRole('button', { name: 'Duplicate', exact: true }).click(); await expect(library.getByRole('textbox', { name: 'ID', exact: true })).toHaveValue(`${id}-copy`); expect(calls.filter((call) => call.cmd === 'agent_class_create')).toHaveLength(before);
    await library.getByRole('textbox', { name: 'ID', exact: true }).fill('reviewed-copy'); holdSave = true; await library.getByRole('button', { name: 'Save', exact: true }).click(); await expect.poll(() => Boolean(releaseSave)).toBe(true); await expect(library.getByRole('heading', { name: 'Create project Agent Class', exact: true })).toBeVisible(); await expect(library.getByRole('button', { name: '＋ New', exact: true })).toBeDisabled();
    refusal = 'agent_class_list'; holdSave = false; releaseSave!(); await expect(library.getByRole('heading', { name: 'Edit project Agent Class', exact: true })).toBeVisible(); await expect(library.getByRole('alert')).toContainText('Class refresh failed'); const creations = calls.filter((call) => call.cmd === 'agent_class_create').length;
    refusal = ''; await library.getByRole('button', { name: 'Retry classes', exact: true }).click(); await expect(library.getByRole('alert')).toHaveCount(0); expect(calls.filter((call) => call.cmd === 'agent_class_create')).toHaveLength(creations);
    await library.getByRole('button', { name: 'Archive', exact: true }).click(); refusal = 'agent_class_archive'; await page.getByRole('dialog').getByRole('button', { name: 'Archive', exact: true }).click(); await expect(page.getByRole('dialog').getByRole('alert')).toContainText('Injected class refusal'); refusal = ''; await page.getByRole('dialog').getByRole('button', { name: 'Archive', exact: true }).click(); await expect(library.getByRole('heading', { name: 'Archived Agent Class', exact: true })).toBeVisible(); await expect(authority.getByText('Unavailable', { exact: true })).toBeVisible();
    await library.getByRole('button', { name: 'Delete', exact: true }).click(); refusal = 'agent_class_delete'; await page.getByRole('dialog').getByRole('button', { name: 'Delete', exact: true }).click(); await expect(page.getByRole('dialog').getByRole('alert')).toContainText('Injected class refusal'); refusal = ''; await page.getByRole('dialog').getByRole('button', { name: 'Delete', exact: true }).click(); await expect(page.getByRole('dialog')).toHaveCount(0); await expect(library.getByRole('button', { name: /reviewed-copy/ })).toHaveCount(0);
    await library.getByRole('button', { name: /^Remote Parity Engineer / }).click(); await authority.scrollIntoViewIfNeeded(); await page.screenshot({ path: test.info().outputPath('agent-class-authority.png') });
    await page.getByRole('button', { name: 'Mission Control', exact: true }).click(); const hidden = calls.length; await reconnect(); expect(calls).toHaveLength(hidden);
  } finally { rmSync(project, { recursive: true, force: true }); }
});
