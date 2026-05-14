const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  await page.goto('http://127.0.0.1:18934/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1500);
  const info = await page.evaluate(() => {
    const rect = (sel) => {
      const el = document.querySelector(sel);
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { top: r.top, bottom: r.bottom, left: r.left, right: r.right, width: r.width, height: r.height, display: getComputedStyle(el).display, visibility: getComputedStyle(el).visibility };
    };
    return {
      bodyClass: document.body.className,
      mode: document.body.dataset.torqueMode,
      runtime: window.state && state.runtime,
      groups: window.state && Object.keys(state.groups || {}),
      appTabs: rect('#app-group-tabs-host'),
      tabsInner: rect('.agent-group-tabs'),
      workspace: rect('#workspace-shell'),
      sidebar: rect('#standalone-sidebar-shell'),
      mainStack: rect('#standalone-main-stack'),
      header: rect('header'),
      main: rect('#main'),
      rail: rect('#standalone-right-rail'),
      railHeader: rect('#standalone-right-rail .standalone-panel-zone-header'),
      terminal: rect('#terminal-workspace'),
      htmlSnippet: document.querySelector('#workspace-shell')?.innerHTML.slice(0,500),
    };
  });
  console.log(JSON.stringify(info, null, 2));
  await page.screenshot({ path: 'output/playwright/current-layout.png', fullPage: false });
  await browser.close();
})();
