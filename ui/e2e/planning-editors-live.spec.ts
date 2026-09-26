import { expect, test, type APIRequestContext } from '@playwright/test';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); return result.data;
}
test('Planning persists every initiative/decision status, scoped links, archive and restore', async ({ page, request }) => {
  test.setTimeout(60_000);
  const runtime = await (await request.get('/api/runtime')).json() as { data: { runtime: { port: number; profile: string } } };
  expect(runtime.data.runtime.port).not.toBe(18932); expect(runtime.data.runtime.profile).not.toBe('default');
  const directory = await mkdtemp(join(tmpdir(), 'torque-planning-e2e-'));
  const group = `Planning editors ${Date.now()}`; const other = `${group} other`;
  const agents: string[] = [];
  try {
    for (const name of [group, other]) {
      await command(request, { cmd: 'add_group', group: name });
      await command(request, { cmd: 'update_group_settings', group: name, settings: { default_directory: directory, git_worktree: false, agent_provider: 'generic' } });
    }
    const add = async (kind: string, name: string, targetGroup = group) => {
      const state = await command(request, { cmd: `add_${kind}`, group: targetGroup, name: `${name} ${Date.now()}`, provider: 'generic', command: '/bin/cat', directory });
      const id = state.id as string; expect(id).toBeTruthy();
      agents.push(id); return id;
    };
    const architect = await add('architect', 'Planning Architect');
    const engineer = await add('engineer', 'Planning Engineer');
    const otherArchitect = await add('architect', 'Other Architect', other);
    await command(request, { cmd: 'architect_decision_create', architect_id: otherArchitect, title: 'Outside decision', rationale: 'Another group' });
    const prior = await command(request, { cmd: 'architect_decision_create', architect_id: architect, title: 'Prior decision', rationale: 'Prior direction' });
    await command(request, { cmd: 'board_add_task', group, task: 'Evidence task', lane: 'Backlog' });
    await page.goto('/'); await page.getByRole('button', { name: group, exact: true }).click(); await page.getByRole('button', { name: /◇ Planning/ }).click();
    await page.getByRole('button', { name: 'Decisions', exact: true }).click();
    await expect(page.getByRole('button', { name: /Outside decision/ })).toHaveCount(0);
    await page.getByRole('button', { name: '＋ New', exact: true }).click();
    await page.getByLabel('Title', { exact: true }).fill('Reviewed decision');
    await page.getByRole('combobox', { name: 'Architect', exact: true }).selectOption(architect);
    await expect(page.getByRole('button', { name: 'Create', exact: true })).toBeDisabled();
    await page.getByLabel('Rationale', { exact: true }).fill('Explain the choice'); await page.getByRole('button', { name: 'Create', exact: true }).click();
    await expect(page.getByRole('dialog')).toHaveCount(0); await page.getByRole('button', { name: /Reviewed decision/ }).click();
    await expect(page.getByLabel('Rationale', { exact: true })).toHaveValue('Explain the choice');
    await page.getByLabel('Rationale', { exact: true }).fill('Preserved while linking');
    await page.getByLabel('Supersedes', { exact: true }).selectOption(prior.id as string);
    await page.getByLabel('Task to link').selectOption({ label: 'Evidence task' }); await page.getByRole('button', { name: 'Link task', exact: true }).click();
    await expect(page.getByRole('button', { name: /^Unlink task/ })).toBeVisible();
    await page.getByLabel('Engineer to link').selectOption(engineer); await page.getByRole('button', { name: 'Link engineer', exact: true }).click();
    await expect(page.getByRole('button', { name: /^Unlink engineer/ })).toBeVisible();
    await expect(page.getByLabel('Rationale', { exact: true })).toHaveValue('Preserved while linking');
    let rejectSave = true;
    await page.route('**/api/cmd', async (route) => {
      const data = route.request().postDataJSON() as Row;
      if (rejectSave && data.cmd === 'architect_decision_update' && data.rationale) { rejectSave = false; await route.fulfill({ json: { ok: false, error: 'Injected decision failure' } }); }
      else await route.continue();
    });
    await page.getByRole('button', { name: 'Save', exact: true }).click(); await expect(page.getByRole('alert')).toContainText('Injected decision failure');
    await expect(page.getByLabel('Rationale', { exact: true })).toHaveValue('Preserved while linking');
    await page.getByRole('button', { name: 'Save', exact: true }).click(); await expect(page.getByRole('dialog')).toHaveCount(0);
    for (const status of ['accepted', 'revised', 'rejected', 'proposed']) {
      await page.getByRole('button', { name: /Reviewed decision/ }).click(); await page.getByRole('combobox', { name: 'Status', exact: true }).selectOption(status);
      await page.getByRole('button', { name: 'Save', exact: true }).click(); await expect(page.getByRole('dialog')).toHaveCount(0);
      const snapshot = await command(request, { cmd: 'decisions_snapshot' });
      const saved = Object.values(snapshot.decisions as Record<string, Row>).find((entry) => entry.title === 'Reviewed decision')!;
      expect(saved).toMatchObject({ status, supersedes: prior.id, rationale: 'Preserved while linking', linked_engineer_ids: [engineer] });
    }
    await page.getByRole('button', { name: /Reviewed decision/ }).click();
    await page.getByRole('button', { name: /^Unlink task/ }).click(); await expect(page.getByRole('button', { name: /^Unlink task/ })).toHaveCount(0);
    await page.getByRole('button', { name: /^Unlink engineer/ }).click(); await expect(page.getByRole('button', { name: /^Unlink engineer/ })).toHaveCount(0);
    await page.getByRole('button', { name: 'Archive', exact: true }).click(); await expect(page.getByRole('dialog')).toHaveCount(0); await expect(page.getByRole('button', { name: /Reviewed decision/ })).toHaveCount(0);
    await page.getByLabel('Show archived decisions').check(); await page.getByRole('button', { name: /Reviewed decision/ }).click();
    await expect(page.getByLabel('Rationale', { exact: true })).toBeDisabled(); await page.getByRole('button', { name: 'Restore', exact: true }).click(); await expect(page.getByRole('dialog')).toHaveCount(0);
    await page.getByLabel('Show archived decisions').uncheck(); await expect(page.getByRole('button', { name: /Reviewed decision/ })).toBeVisible();
    await page.getByRole('button', { name: 'Initiatives', exact: true }).click(); await page.getByRole('button', { name: '＋ New', exact: true }).click();
    await page.getByLabel('Title', { exact: true }).fill('Persisted initiative'); await page.getByLabel('Summary', { exact: true }).fill('Initial scope'); await page.getByRole('button', { name: 'Create', exact: true }).click(); await expect(page.getByRole('dialog')).toHaveCount(0);
    const list = await command(request, { cmd: 'initiative_list', group }); const initiative = (list.initiatives as Row[])[0]!.id;
    for (const status of ['now', 'next', 'later', 'parked', 'shipped', 'triage']) {
      await page.getByRole('button', { name: /Persisted initiative/ }).click();
      await page.getByRole('combobox', { name: 'Status', exact: true }).selectOption(status); await page.getByRole('button', { name: 'Save', exact: true }).click(); await expect(page.getByRole('dialog')).toHaveCount(0);
      expect(await command(request, { cmd: 'initiative_show', id: initiative })).toMatchObject({ planning_status: status });
    }
    await page.getByRole('button', { name: /Persisted initiative/ }).click();
    await page.getByRole('combobox', { name: 'Owner kind' }).selectOption('architect');
    for (const [label, value] of Object.entries({ Summary: 'Detailed scope', 'Why this matters': 'Operator value', 'In scope': 'Required work', 'Out of scope': 'Explicit boundary', 'Definition of done': 'Evidence for every gate', Priority: 'P1', 'Owner ID': architect })) await page.getByLabel(label, { exact: true }).fill(value);
    await page.getByLabel('Link type', { exact: true }).selectOption('decision'); await page.getByLabel('Linked record', { exact: true }).selectOption({ label: 'Reviewed decision' }); await page.getByRole('button', { name: 'Link', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Unlink', exact: true })).toBeVisible(); await expect(page.getByLabel('Why this matters')).toHaveValue('Operator value');
    await page.getByRole('button', { name: 'Save', exact: true }).click(); await expect(page.getByRole('dialog')).toHaveCount(0);
    expect(await command(request, { cmd: 'initiative_show', id: initiative })).toMatchObject({ summary: 'Detailed scope', why: 'Operator value', in_scope: 'Required work', out_of_scope: 'Explicit boundary', done_definition: 'Evidence for every gate', priority: 'P1', owner_kind: 'architect', owner_id: architect });
    await page.getByRole('button', { name: /Persisted initiative/ }).click(); await page.getByRole('button', { name: 'Unlink', exact: true }).click(); await expect(page.getByRole('button', { name: 'Unlink', exact: true })).toHaveCount(0);
    await page.screenshot({ path: test.info().outputPath('initiative-editor.png'), animations: 'disabled', fullPage: true });
    await page.getByRole('button', { name: 'Archive', exact: true }).click(); await expect(page.getByRole('dialog')).toHaveCount(0); await expect(page.getByRole('button', { name: /Persisted initiative/ })).toHaveCount(0);
    expect(await command(request, { cmd: 'initiative_show', id: initiative })).toMatchObject({ archived: true, links: { decisions: [] } });
  } finally {
    for (const id of agents.reverse()) await command(request, { cmd: 'remove_agent', id });
    await rm(directory, { recursive: true, force: true });
  }
});

test('Initiative task creation reviews unsaved scope and recovers a failed link without duplication', async ({ page, request }) => {
  const runtime = await (await request.get('/api/runtime')).json() as { data: { runtime: { port: number; profile: string } } };
  expect(runtime.data.runtime.port).not.toBe(18932); expect(runtime.data.runtime.profile).not.toBe('default');
  const group = `Initiative task ${Date.now()}`;
  await command(request, { cmd: 'add_group', group });
  const result = await command(request, { cmd: 'initiative_create', group, title: 'Scope to review', summary: 'Stored summary' });
  const initiativeId = String((result.initiative as Row).id);
  await page.goto('/'); await page.getByRole('button', { name: group, exact: true }).click();
  await page.getByRole('button', { name: /◇ Planning/ }).click();
  await page.getByRole('button', { name: /Scope to review/ }).click();
  await page.getByLabel('Why this matters').fill('Unsaved rationale');
  await page.getByLabel('In scope').fill('Reviewed scope');
  await page.getByLabel('Definition of done').fill('Verify persistence');
  await page.getByRole('button', { name: 'Create Board task', exact: true }).click();
  let dialog = page.getByRole('dialog', { name: 'Create Board task', exact: true });
  await expect(dialog.getByLabel('Title', { exact: true })).toHaveValue('Scope to review');
  await expect(dialog.getByLabel('Description')).toHaveValue(`Source initiative: ${initiativeId} — Scope to review\n\nSummary\nStored summary\n\nWhy\nUnsaved rationale\n\nIn scope\nReviewed scope\n\nDone definition\nVerify persistence`);
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(page.getByLabel('Why this matters')).toHaveValue('Unsaved rationale');
  await page.getByRole('button', { name: 'Create Board task', exact: true }).click();
  dialog = page.getByRole('dialog', { name: 'Create Board task', exact: true });
  await dialog.getByLabel('Title', { exact: true }).fill('Reviewed child task');
  await dialog.getByRole('combobox', { name: 'Lane', exact: true }).selectOption('Backlog');
  await dialog.getByLabel('Labels', { exact: true }).fill('reviewed, scope');
  await dialog.getByText('Advanced variables', { exact: true }).click();
  await dialog.getByLabel('Action variables (JSON)').fill('{"reviewed":true}');
  await page.screenshot({ path: test.info().outputPath('initiative-task-review.png'), animations: 'disabled', fullPage: true });
  let createAttempts = 0; let linkAttempts = 0;
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd === 'board_add_task' && ++createAttempts === 1) await route.fulfill({ json: { ok: false, creation_refused: true, error: 'Injected task failure' } });
    else if (data.cmd === 'initiative_link_task' && ++linkAttempts === 1) await route.fulfill({ json: { ok: false, error: 'Injected link failure' } });
    else await route.continue();
  });
  await dialog.getByRole('button', { name: 'Create task', exact: true }).click();
  await expect(dialog.getByRole('alert')).toContainText('Injected task failure');
  await expect(dialog.getByLabel('Title', { exact: true })).toHaveValue('Reviewed child task');
  await dialog.getByRole('button', { name: 'Create task', exact: true }).click();
  await expect(dialog.getByRole('alert')).toContainText('Injected link failure');
  await expect(dialog.getByLabel('Title', { exact: true })).toBeDisabled();
  await dialog.getByRole('button', { name: 'Close', exact: true }).click();
  await expect(page.getByLabel('Why this matters')).toHaveValue('Unsaved rationale');
  await page.getByRole('button', { name: 'Resume task link', exact: true }).click();
  await dialog.getByRole('button', { name: 'Retry link', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Unlink', exact: true })).toBeVisible();
  expect(createAttempts).toBe(2); expect(linkAttempts).toBe(2);
  const saved = await command(request, { cmd: 'initiative_show', id: initiativeId });
  expect(saved.why).toBe('');
  const tasks = (saved.links as { tasks: string[] }).tasks;
  expect(tasks).toHaveLength(1);
  const task = (await command(request, { cmd: 'task_detail', id: tasks[0] })).task as Row;
  expect(task).toMatchObject({ task: 'Reviewed child task', group, lane: 'Backlog', labels: ['reviewed', 'scope'], action_vars: { reviewed: true } });
  expect(task.description).toContain('Unsaved rationale');
  await expect(page.getByLabel('Why this matters')).toHaveValue('Unsaved rationale');
  await page.screenshot({ path: test.info().outputPath('initiative-created-task.png'), animations: 'disabled', fullPage: true });
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect((await command(request, { cmd: 'initiative_show', id: initiativeId })).why).toBe('Unsaved rationale');
});
