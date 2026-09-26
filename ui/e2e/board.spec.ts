import { expect, test, type Page } from '@playwright/test';

async function ensureGroup(page: Page) {
  const runtime = await (await page.request.get('/api/runtime')).json() as { data: { runtime: { port: number; profile: string } } };
  expect(runtime.data.runtime.port).not.toBe(18932);
  expect(runtime.data.runtime.profile).not.toBe('default');
  const group = `Board smoke ${Date.now()}`;
  for (const cmd of ['add_group', 'ui_select_group']) {
    const response = await page.request.post('/api/cmd', { data: { cmd, group } });
    expect(response.ok()).toBe(true);
    const result = await response.json() as { ok: boolean; error?: string; data: { type?: string } };
    expect(result.ok, result.error).toBe(true);
    expect(result.data.type).not.toBe('error');
  }
  await page.getByRole('button', { name: /▦ Board/ }).click();
  await page.getByRole('button', { name: group, exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Board', exact: true, level: 1 })).toBeVisible();
}

test('Board supports a create, edit, and completion workflow against a live daemon', async ({ page }) => {
  const pageErrors: string[] = [];
  const sentFrames: string[] = [];
  const receivedFrames: string[] = [];
  page.on('websocket', (socket) => {
    socket.on('framesent', (event) => sentFrames.push(String(event.payload)));
    socket.on('framereceived', (event) => receivedFrames.push(String(event.payload)));
  });
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') pageErrors.push(message.text());
  });

  await page.goto('./');
  await expect(page.getByText('connected')).toBeVisible();
  await ensureGroup(page);
  await expect(page.getByRole('heading', { name: 'Groups' })).toBeVisible();

  const title = `Phase 2 workflow ${Date.now()}`;
  await page.getByRole('button', { name: '＋ New task' }).click();
  await expect(page.getByRole('dialog', { name: 'Create task' })).toBeVisible();
  await page.getByRole('textbox', { name: 'Title' }).fill(title);
  await page.getByRole('button', { name: 'Create task' }).click();
  const card = page.getByText(title, { exact: true });
  await expect(card).toBeVisible();
  const addedFrame = { task_id: await card.locator('xpath=ancestor::article').getAttribute('data-task-id') };
  expect(addedFrame?.task_id).toBeTruthy();

  await card.dblclick();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByLabel('Description').fill('Created and edited through the React Board.');
  await page.getByRole('button', { name: 'Save task' }).click();

  await page.getByRole('button', { name: `Actions for ${title}` }).click();
  await page.getByRole('menuitem', { name: 'Move to Done' }).click();
  await expect.poll(() => sentFrames.some((frame) => frame.includes('board_move_task'))).toBe(true);
  expect(sentFrames.find((frame) => frame.includes('board_move_task'))).toBe(JSON.stringify({ cmd: 'board_move_task', id: addedFrame?.task_id, lane: 'Done' }));
  await expect(page.getByRole('dialog', { name: 'Acknowledge unfinished merge' })).toBeVisible();
  await page.getByRole('button', { name: 'Move anyway' }).click();
  await expect.poll(() => receivedFrames.some((frame) => frame.includes('task_moved'))).toBe(true);
  await expect(page.locator('#lane-Done').getByText(title, { exact: true })).toBeVisible();
  expect(pageErrors).toEqual([]);
});

test('workspace keyboard entry points remain available', async ({ page }) => {
  await page.goto('./');
  await expect(page.getByText('connected')).toBeVisible();
  await ensureGroup(page);
  await page.keyboard.press('ControlOrMeta+K');
  await expect(page.getByRole('dialog', { name: 'Command palette' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: 'Command palette' })).toBeHidden();
  await page.keyboard.press('/');
  await expect(page.getByRole('searchbox', { name: 'Search board' })).toBeFocused();
});
