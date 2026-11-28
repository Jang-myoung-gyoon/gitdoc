/* End-to-end scroll ownership checks using actual GitDoc PDF and XLSX routes. */
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {mkdtempSync, rmSync} = require('node:fs');
const {tmpdir} = require('node:os');
const {join} = require('node:path');
const {createInterface} = require('node:readline');
const {chromium} = require('playwright');

const fixtures = mkdtempSync(join(tmpdir(), 'gitdoc-browser-scroll-'));
const server = spawn(process.env.PYTHON || 'python3', ['tests/browser_scroll_fixtures.py', fixtures], {
  stdio: ['ignore', 'pipe', 'inherit'],
  env: {...process.env, PYTHONPATH: process.cwd(), DOCVERS_DB: join(fixtures, 'documents.sqlite3')},
});
const ready = new Promise((resolve, reject) => {
  createInterface({input: server.stdout}).once('line', line => resolve(JSON.parse(line)));
  server.once('error', reject);
  server.once('exit', code => reject(Error('Fixture server exited: ' + code)));
});
const sizes = [[1440, 900], [1280, 720], [1024, 600], [390, 844], [320, 568], [640, 420]];
const scope = '[data-paper-scroll], [data-sheet-scroll]';

async function readPosition(page, comparison) {
  return page.evaluate(({scope, comparison}) => {
    const owner = document.querySelector(scope);
    const outer = document.scrollingElement;
    const toolbar = document.querySelector('.reader-controls');
    const rect = toolbar.getBoundingClientRect().toJSON();
    const viewport = visualViewport;
    const visibleWidth = Math.max(0, Math.min(owner.getBoundingClientRect().left + owner.clientWidth,
      viewport.offsetLeft + viewport.width) - Math.max(owner.getBoundingClientRect().left, viewport.offsetLeft));
    const visibleHeight = Math.max(0, Math.min(owner.getBoundingClientRect().top + owner.clientHeight,
      viewport.offsetTop + viewport.height) - Math.max(owner.getBoundingClientRect().top, viewport.offsetTop));
    const scrollers = Array.from(document.querySelectorAll('body *')).filter(node => {
      if (!node.getClientRects().length) return false;
      const css = getComputedStyle(node);
      return (/auto|scroll/.test(css.overflowY) && node.scrollHeight > node.clientHeight + 1) ||
        (/auto|scroll/.test(css.overflowX) && node.scrollWidth > node.clientWidth + 1);
    }).map(node => node.className);
    const cells = comparison && owner.matches('[data-sheet-scroll]') ?
      Array.from(owner.querySelectorAll('[data-workbook]'), panel => {
        const first = panel.querySelector('[data-address="D6"]').getBoundingClientRect();
        const bounds = panel.getBoundingClientRect();
        return [Math.round(first.x - bounds.x), Math.round(first.y), Math.round(bounds.x)];
      }) : null;
    const identity = Array.from(toolbar.querySelectorAll('.reader-identity > *'), node => {
      const bounds = node.getBoundingClientRect();
      return [bounds.width, bounds.top, bounds.bottom, node.scrollWidth, node.textContent.trim()];
    });
    return {owner: [owner.scrollLeft, owner.scrollTop], window: [scrollX, scrollY],
      body: [document.body.scrollLeft, document.body.scrollTop],
      outer: [outer.scrollWidth - outer.clientWidth, outer.scrollHeight - outer.clientHeight],
      toolbar: [rect.x, rect.y, rect.width, rect.height], scrollers, cells, identity,
      ratio: visibleWidth * visibleHeight / (viewport.width * viewport.height)};
  }, {scope, comparison});
}

function stable(before, after) {
  assert.deepEqual(after.window, [0, 0]);
  assert.deepEqual(after.body, [0, 0]);
  assert.deepEqual(after.outer, [0, 0]);
  assert.deepEqual(after.toolbar, before.toolbar);
  assert.deepEqual(after.scrollers, before.scrollers);
}

async function readyPage(page) {
  await page.locator('.paper-row img, .sheet-table').first().waitFor();
}

async function checkWheel(page, url, compare, width, height) {
  await page.setViewportSize({width, height});
  await page.goto(url);
  await readyPage(page);
  const before = await readPosition(page, compare);
  assert.ok(before.ratio >= .9, width + 'x' + height + ' coverage ' + before.ratio);
  assert.equal(before.scrollers.length, 1, JSON.stringify(before));
  assert.deepEqual(before.outer, [0, 0]);
  assert.ok(before.toolbar[3] <= 36);
  assert.equal(before.identity.length, 4);
  assert.ok(before.identity.every(part => part[0] > 0 && part[1] >= 0 && part[2] <= before.toolbar[3]),
    width + 'x' + height + ' toolbar geometry: ' + JSON.stringify(before.identity));
  assert.match(before.identity[1][4], compare ? /v1\.0\.0.*→/ : /확정본\s+v1\.0\.0/);
  assert.equal(before.identity[2][4], 'main');
  if (width <= 390) {
    assert.ok(before.identity[0][3] > before.identity[0][0]);
    assert.ok(before.identity[3][3] > before.identity[3][0]);
  }
  assert.ok(await page.locator('.reader-more summary').isVisible());
  assert.ok(await page.locator('.reader-more summary').evaluate(button =>
    button.getBoundingClientRect().right <= visualViewport.width));
  await page.mouse.move(width * (compare ? .75 : .5), height * .55);
  await page.mouse.wheel(0, 420);
  await page.waitForFunction(selector => document.querySelector(selector).scrollTop > 0, scope);
  const middle = await readPosition(page, compare);
  stable(before, middle);
  assert.ok(middle.owner[1] > before.owner[1]);
  // Even at the end of the document, the next wheel gesture cannot move the app shell.
  await page.mouse.wheel(0, 30000);
  await page.waitForFunction(selector => {
    const node = document.querySelector(selector);
    return node.scrollTop + node.clientHeight >= node.scrollHeight - 2;
  }, scope);
  await page.mouse.wheel(0, 500);
  stable(before, await readPosition(page, compare));

  const owner = page.locator(scope);
  await owner.focus();
  await page.keyboard.press('Home');
  await page.waitForFunction(selector => document.querySelector(selector).scrollTop === 0, scope);
  await page.keyboard.press('PageDown');
  await page.waitForFunction(selector => document.querySelector(selector).scrollTop > 0, scope);
  stable(before, await readPosition(page, compare));
  await page.locator('.reader-more summary').click();
  stable(before, await readPosition(page, compare));
  assert.ok((await page.locator('.reader-details').textContent()).includes(before.identity[3][4]));
  return before.ratio;
}

async function run(config) {
  const browser = await chromium.launch();
  try {
    const ctx = await browser.newContext({permissions: ['clipboard-read', 'clipboard-write']});
    const page = await ctx.newPage();
    page.setDefaultTimeout(6500);
    for (const [width, height] of sizes) {
      const ratios = [];
      for (const type of ['pdf', 'xlsx']) {
        for (const [index, route] of config.routes[type].entries()) {
          const result = await checkWheel(page, config.origin + route, index === 1, width, height);
          ratios.push((result * 100).toFixed(2) + '%');
        }
      }
      console.log(width + 'x' + height + ': PDF, PDF compare, XLSX, XLSX compare ' + ratios.join(', '));
    }

    // Both compared sheets use the same vertical and horizontal position.
    await page.setViewportSize({width: 390, height: 844});
    await page.goto(config.origin + config.routes.xlsx[1]);
    await readyPage(page);
    const atStart = await readPosition(page, true);
    await page.mouse.move(285, 300);
    await page.mouse.wheel(220, 190);
    await page.waitForFunction(selector => document.querySelector(selector).scrollLeft > 100, scope);
    await page.waitForFunction(() => parseFloat(document.querySelector('.sheet-panes').style.getPropertyValue('--sheet-offset')) > 100);
    const shifted = await readPosition(page, true);
    assert.equal(shifted.cells[0][0], shifted.cells[1][0]);
    assert.equal(shifted.cells[0][1], shifted.cells[1][1]);
    assert.ok(shifted.cells[0][0] < atStart.cells[0][0]);
    assert.deepEqual(shifted.cells.map(row => row[2]), atStart.cells.map(row => row[2]));
    await page.locator('[data-sheet-tabs]').selectOption('Summary');
    await page.locator('[data-cell-form] input').fill('D26');
    await page.locator('[data-cell-form] button').click();
    await page.locator('.selected-cell[data-address="D26"]').first().waitFor();
    await page.locator('.reader-more summary').click();
    await page.locator('[data-edit-location]').click();
    const clipboard = await page.evaluate(() => navigator.clipboard.readText());
    assert.equal(JSON.parse(clipboard).cell, 'D26');
    await page.keyboard.press('Escape');
    await page.locator('[aria-controls="history-dialog"]').click();
    await page.locator('#history-dialog[open]').waitFor();
    await page.keyboard.press('Escape');

    await page.goto(config.origin + config.routes.pdf[1]);
    await readyPage(page);
    await page.locator('[data-page]').fill('5');
    await page.locator('[data-page]').press('Enter');
    await page.waitForFunction(() => document.querySelector('[data-paper-scroll]').scrollTop > 0);
    await page.locator('[data-zoom]').selectOption('125');
    await page.locator('[data-next-change]').click();
    await page.locator('.reader-more summary').click();
    await page.locator('[aria-controls="changes-dialog"]').click();
    await page.locator('#changes-dialog[open]').waitFor();
    await page.keyboard.press('Escape');

    const phone = await browser.newContext({viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true});
    const mobile = await phone.newPage();
    const mobileReply = await mobile.goto(config.origin + config.routes.pdf[0]);
    assert.match(mobileReply.headers()['content-security-policy'], /frame-ancestors 'none'/);
    assert.equal(await mobile.locator('iframe').count(), 0);
    await readyPage(mobile);
    const beforeTouch = await readPosition(mobile, false);
    const touch = await phone.newCDPSession(mobile);
    await touch.send('Input.dispatchTouchEvent', {type: 'touchStart', touchPoints: [{x: 191, y: 642}]});
    for (const y of [605, 545, 488, 402, 290]) {
      await touch.send('Input.dispatchTouchEvent', {type: 'touchMove', touchPoints: [{x: 191, y}]});
    }
    await touch.send('Input.dispatchTouchEvent', {type: 'touchEnd', touchPoints: []});
    await mobile.waitForFunction(selector => document.querySelector(selector).scrollTop > 0, scope);
    stable(beforeTouch, await readPosition(mobile, false));
    console.log('Shared sheet positioning, toolbar actions, and mobile touch scrolling passed.');
  } finally {
    await browser.close();
  }
}

ready.then(run).catch(error => { console.error(error); process.exitCode = 1; }).finally(() => {
  server.once('exit', () => rmSync(fixtures, {recursive:true, force:true}));
  server.kill('SIGTERM');
});
