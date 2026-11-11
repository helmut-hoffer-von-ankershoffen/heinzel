'use strict';
const { createRequire } = require('node:module');
const requireWeb = createRequire(`${process.cwd()}/package.json`);
const { chromium, webkit } = requireWeb('playwright-core');
const version = requireWeb('playwright-core/package.json').version;
if (version !== '1.64.1' || process.version !== 'v24.19.0') throw new Error('locked runtime differs');
const name = process.argv[2];
if (!['chromium', 'webkit'].includes(name)) throw new Error('unexpected browser');
(async () => {
  const browser = await ({ chromium, webkit })[name].launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.setContent('<title>h17g node smoke</title><p id="result">ready</p>');
    if (await page.locator('#result').textContent() !== 'ready') throw new Error('page interaction failed');
    console.log(JSON.stringify({ browser: name, version: browser.version(), node: process.version,
      playwright: version, page: 'ready' }));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
