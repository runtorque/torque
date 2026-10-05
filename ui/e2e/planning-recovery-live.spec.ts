import { expect, test, type APIRequestContext, type WebSocketRoute } from '@playwright/test';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
type Row = Record<string, unknown>;
async function command(request: APIRequestContext, data: Row) {
  const result = await (await request.post('/api/cmd', { data })).json() as { ok: boolean; error?: string; data: Row };
  expect(result.ok, result.error).toBe(true); expect(result.data.type).not.toBe('error'); return result.data;
}
async function prepare(request: APIRequestContext, label: string) {
  const runtime = (await (await request.get('/api/runtime')).json() as { data: { runtime: Row } }).data.runtime;
  expect(runtime.port).not.toBe(18932); expect(runtime.profile).not.toBe('default');
  const group = `Planning recovery ${label} ${Date.now()}`;
  await command(request, { cmd: 'add_group', group }); await command(request, { cmd: 'ui_select_group', group });
  await command(request, { cmd: 'ui_set_react_workspace_state', state: { version: 1, activePanel: 'planning', controlTab: 'mission' } });
  return group;
}
const cases = [
  { kind: 'note', prefix: 'scratchpad_note', tab: 'Thinking', dialog: 'Scratchpad note', key: 'note', fields: { body: 'Saved body' } },
  { kind: 'brief', prefix: 'idea_brief', tab: 'Thinking', dialog: 'Idea Brief', key: 'idea_brief', fields: { problem_opportunity: 'Saved problem' } },
  { kind: 'initiative', prefix: 'initiative', tab: 'Initiatives', dialog: 'Initiative', key: 'initiative', fields: { summary: 'Saved summary' } },
  { kind: 'area', prefix: 'area', tab: 'Areas', dialog: 'Area', key: 'area', fields: { summary: 'Saved summary' } },
  { kind: 'decision', prefix: 'architect_decision', tab: 'Decisions', dialog: 'Architect decision', key: '', fields: { rationale: 'Saved rationale' } },
];
for (const entry of cases) test(`Planning ${entry.kind} detail recovers from a real deadline with draft, focus and late-response ownership intact`, async ({ page, request }) => {
  test.setTimeout(60_000);
  const group = await prepare(request, entry.kind); const directory = await mkdtemp(join(tmpdir(), 'torque-planning-recovery-'));
  let architect = ''; let release = () => {}; let held = false; let hold = false;
  try {
    if (entry.kind === 'decision') architect = String((await command(request, { cmd: 'add_architect', group, name: 'Recovery Architect', provider: 'generic', command: '/bin/cat', directory })).id);
    const result = await command(request, { cmd: `${entry.prefix}_create`, group, title: 'Deadline record', ...entry.fields, ...(architect ? { architect_id: architect } : {}) });
    const record = entry.key ? result[entry.key] as Row : result;
    await page.route('**/api/cmd', async (route) => {
      const data = route.request().postDataJSON() as Row;
      if (hold && data.cmd === (entry.kind === 'decision' ? 'decisions_snapshot' : `${entry.prefix}_show`)) {
        hold = false; const response = await route.fetch(); held = true;
        await new Promise<void>((resolve) => { release = resolve; }); await route.fulfill({ response });
      } else await route.continue();
    });
    await page.goto('/'); await page.getByRole('button', { name: entry.tab, exact: true }).click();
    await expect(page.getByRole('button', { name: /Deadline record/ })).toBeVisible();
    hold = true; await page.getByRole('button', { name: /Deadline record/ }).click(); await expect.poll(() => held).toBe(true);
    const dialog = page.getByRole('dialog', { name: entry.dialog, exact: true }); const title = dialog.getByLabel('Title', { exact: true });
    await title.fill('Unfinished operator draft'); await title.focus(); await title.evaluate((node: HTMLInputElement) => { node.dataset.detailOwner = 'same'; node.setSelectionRange(2, 9); });
    await expect(dialog.getByRole('alert')).toContainText('timed out', { timeout: 20_000 });
    await expect(title).toHaveValue('Unfinished operator draft'); await expect(title).toBeFocused(); await expect(title).toHaveAttribute('data-detail-owner', 'same');
    expect(await title.evaluate((node: HTMLInputElement) => [node.selectionStart, node.selectionEnd])).toEqual([2, 9]);
    await page.screenshot({ path: test.info().outputPath(`${entry.kind}-detail-timeout.png`) });
    await dialog.getByRole('button', { name: entry.kind === 'area' ? 'Retry Area details' : 'Retry details', exact: true }).click();
    await expect(dialog.getByRole('alert')).toHaveCount(0); await expect(dialog.getByRole('button', { name: 'Save', exact: true })).toBeEnabled();
    release(); await expect(title).toHaveValue('Unfinished operator draft'); await expect(title).toHaveAttribute('data-detail-owner', 'same');
    await dialog.getByRole('button', { name: 'Save', exact: true }).click(); await expect(dialog).toHaveCount(0);
    const saved = entry.kind === 'decision' ? Object.values((await command(request, { cmd: 'decisions_snapshot' })).decisions as Record<string, Row>).find((row) => row.id === record.id) : await command(request, { cmd: `${entry.prefix}_show`, id: record.id });
    expect(saved?.title).toBe('Unfinished operator draft');
  } finally { release(); if (architect) await command(request, { cmd: 'remove_agent', id: architect }); await rm(directory, { recursive: true, force: true }); }
});

test('Planning validates create/note acknowledgements and retries an archive deadline without replaying its acknowledged edit', async ({ page, request }) => {
  test.setTimeout(90_000); const group = await prepare(request, 'writes');
  const initiative = (await command(request, { cmd: 'initiative_create', group, title: 'Recover archive' })).initiative as Row;
  await command(request, { cmd: 'area_create', group, title: 'Recovery Area' });
  let malformedCreate = true; let malformedNote = true; let holdArchive = true; let held = false; let release = () => {}; let socket: WebSocketRoute | undefined; let connections = 0; const writes: Row[] = [];
  await page.routeWebSocket(/\/ws\?/, (route) => { route.connectToServer(); socket = route; connections++; });
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd === 'idea_brief_create' && malformedCreate) { malformedCreate = false; await route.fulfill({ json: { ok: true, data: { type: 'ok' } } }); return; }
    if (data.cmd === 'area_note_create' && malformedNote) { malformedNote = false; await route.fulfill({ json: { ok: true, data: { type: 'area_note_created', note: { id: 999, area_id: 'wrong-area' } } } }); return; }
    if (data.cmd === 'initiative_update' || data.cmd === 'initiative_archive') writes.push(data);
    if (data.cmd === 'initiative_archive' && holdArchive) { holdArchive = false; held = true; await new Promise<void>((resolve) => { release = resolve; }); await route.fulfill({ json: { ok: false, error: 'Obsolete archive reply' } }); return; }
    await route.continue();
  });
  try {
    await page.goto('/'); await page.getByRole('button', { name: 'Thinking', exact: true }).click(); await page.getByRole('button', { name: '＋ Brief', exact: true }).click();
    let dialog = page.getByRole('dialog'); await dialog.getByLabel('Title', { exact: true }).fill('Acknowledged brief'); await dialog.getByLabel('Problem or opportunity').fill('Retain creation draft'); await dialog.getByRole('button', { name: 'Create', exact: true }).click();
    await expect(dialog.getByRole('alert')).toContainText('invalid acknowledgement'); await expect(dialog.getByLabel('Title', { exact: true })).toHaveValue('Acknowledged brief');
    await dialog.getByRole('button', { name: 'Create', exact: true }).click(); await expect(dialog).toHaveCount(0); expect((await command(request, { cmd: 'idea_brief_list', group })).idea_briefs).toHaveLength(1);
    await page.getByRole('button', { name: 'Initiatives', exact: true }).click(); await page.getByRole('button', { name: /Recover archive/ }).click(); dialog = page.getByRole('dialog', { name: 'Initiative', exact: true });
    await dialog.getByLabel('Why this matters').fill('Acknowledged scope'); await dialog.getByRole('button', { name: 'Archive', exact: true }).click(); await expect.poll(() => held).toBe(true);
    await expect(dialog.getByRole('alert')).toContainText('outcome is unknown', { timeout: 35_000 }); await expect(dialog.getByRole('button', { name: 'Archive', exact: true })).toBeEnabled();
    await command(request, { cmd: 'initiative_update', id: initiative.id, why: 'External change after the acknowledged save' });
    await expect(dialog.getByLabel('Why this matters')).toHaveValue('External change after the acknowledged save');
    const before = connections; await socket!.close({ code: 1012, reason: 'Unknown Planning archive outcome' }); await expect.poll(() => connections).toBeGreaterThan(before); expect(writes).toHaveLength(2);
    release(); await expect(dialog.getByRole('alert')).toContainText('outcome is unknown'); await page.screenshot({ path: test.info().outputPath('planning-archive-recovery.png') });
    await dialog.getByRole('button', { name: 'Archive', exact: true }).click(); await expect(dialog).toHaveCount(0);
    expect(writes.map((row) => row.cmd)).toEqual(['initiative_update', 'initiative_archive', 'initiative_archive']); expect(await command(request, { cmd: 'initiative_show', id: initiative.id })).toMatchObject({ archived: true, why: 'External change after the acknowledged save' });
    await page.getByRole('button', { name: 'Areas', exact: true }).click(); await page.getByRole('button', { name: /Recovery Area/ }).click(); dialog = page.getByRole('dialog', { name: 'Area', exact: true });
    await dialog.getByLabel('Note title').fill('Retained note'); await dialog.getByLabel('Note body').fill('Area-owned evidence'); await dialog.getByRole('button', { name: 'Add note', exact: true }).click();
    await expect(dialog.getByRole('alert')).toContainText('invalid acknowledgement'); await expect(dialog.getByLabel('Note body')).toHaveValue('Area-owned evidence');
    await dialog.getByRole('button', { name: 'Add note', exact: true }).click(); await expect(dialog.getByRole('button', { name: 'Edit note Retained note', exact: true })).toBeVisible();
    await expect(dialog.getByLabel('Note title')).toHaveValue(''); await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
  } finally { release(); }
});

test('Initiative task options and linking recover from real deadlines without duplicate task creation', async ({ page, request }) => {
  test.setTimeout(90_000); const group = await prepare(request, 'task linking');
  const initiative = (await command(request, { cmd: 'initiative_create', group, title: 'Task source' })).initiative as Row;
  let holdRoles = false; let holdLink = true; let releaseRoles = () => {}; let releaseLink = () => {}; let rolesHeld = false; let linkHeld = false; const creates: Row[] = []; const links: Row[] = [];
  await page.route('**/api/cmd', async (route) => {
    const data = route.request().postDataJSON() as Row;
    if (data.cmd === 'list_roles' && holdRoles) { holdRoles = false; const response = await route.fetch(); rolesHeld = true; await new Promise<void>((resolve) => { releaseRoles = resolve; }); await route.fulfill({ response }); return; }
    if (data.cmd === 'board_add_task') creates.push(data);
    if (data.cmd === 'initiative_link_task') { links.push(data); if (holdLink) { holdLink = false; const response = await route.fetch(); linkHeld = true; await new Promise<void>((resolve) => { releaseLink = resolve; }); await route.fulfill({ response }); return; } }
    await route.continue();
  });
  try {
    await page.goto('/'); await page.getByRole('button', { name: /Task source/ }).click(); await expect(page.getByRole('button', { name: 'Create Board task', exact: true })).toBeEnabled(); holdRoles = true;
    await page.getByRole('button', { name: 'Create Board task', exact: true }).click(); const dialog = page.getByRole('dialog', { name: 'Create Board task', exact: true });
    await expect.poll(() => rolesHeld).toBe(true); await dialog.getByLabel('Title', { exact: true }).fill('Exactly one reviewed task');
    await expect(dialog.getByRole('alert')).toContainText('timed out', { timeout: 20_000 }); await expect(dialog.getByLabel('Title', { exact: true })).toHaveValue('Exactly one reviewed task');
    await dialog.getByRole('button', { name: 'Retry task options', exact: true }).click(); await expect(dialog.getByRole('alert')).toHaveCount(0); releaseRoles();
    await dialog.getByRole('button', { name: 'Create task', exact: true }).click(); await expect.poll(() => linkHeld).toBe(true);
    await expect(dialog.getByRole('alert')).toContainText('outcome is unknown', { timeout: 35_000 }); await expect(dialog.getByRole('button', { name: 'Retry link', exact: true })).toBeEnabled(); releaseLink();
    await expect(dialog).toBeVisible(); await page.screenshot({ path: test.info().outputPath('planning-known-task-link-recovery.png') });
    await dialog.getByRole('button', { name: 'Close', exact: true }).click(); await page.getByRole('button', { name: 'Resume task link', exact: true }).click(); await dialog.getByRole('button', { name: 'Retry link', exact: true }).click(); await expect(dialog).toHaveCount(0);
    expect(creates).toHaveLength(1); expect(links).toHaveLength(2); expect(links[1]).toEqual(links[0]);
    const saved = await command(request, { cmd: 'initiative_show', id: initiative.id }); expect((saved.links as Row).tasks).toEqual([links[0]?.task_id]);
    const state = await command(request, { cmd: 'get_state' }); expect(Object.values(state.board_tasks as Record<string, Row>).filter((row) => row.group === group)).toHaveLength(1);
  } finally { releaseRoles(); releaseLink(); }
});
