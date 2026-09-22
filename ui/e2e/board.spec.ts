import { expect, test, type Page } from '@playwright/test';

async function sendSetupCommand(page: Page, command: Record<string, unknown>) {
  await page.evaluate(async (payload) => {
    await new Promise<void>((resolve, reject) => {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const socket = new WebSocket(`${protocol}//${window.location.host}/ws?compact=1&client_id=e2e-setup`);
      socket.addEventListener('open', () => socket.send(JSON.stringify(payload)));
      socket.addEventListener('message', (event) => {
        const frame = JSON.parse(String(event.data)) as { type?: string };
        if (frame.type === 'state') return;
        socket.close();
        resolve();
      });
      socket.addEventListener('error', () => reject(new Error('setup WebSocket failed')));
      window.setTimeout(() => { socket.close(); resolve(); }, 1_500);
    });
  }, command);
}

async function ensureGroup(page: Page) {
  if (await page.getByText('Choose a group').isVisible()) {
    await sendSetupCommand(page, { cmd: 'add_group', group: 'Phase 2 E2E' });
  }
  await expect(page.getByRole('heading', { name: 'Board' })).toBeVisible();
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
