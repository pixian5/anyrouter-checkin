const { chromium } = require('/Users/x/.nvm/versions/node/v24.14.0/lib/node_modules/playwright');
const fs = require('fs');

const EXE = process.env.HOME + '/Library/Caches/ms-playwright/chromium-1243/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing';

(async () => {
  const browser = await chromium.launch({ executablePath: EXE, headless: false });
  const page = await browser.newPage({ viewport: { width: 1728, height: 1117 } });
  const logs = [];
  page.on('console', m => { const t = m.text(); if (t.includes('HWCloudAutoLogin')) logs.push(t); });

  await page.goto('https://auth.huaweicloud.com/authui/login.html?service=https%3A%2F%2Fdeveloper.huaweicloud.com%2F&locale=zh-cn', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(5000);

  // 模拟浏览器已自动切到密码tab：点击密码tab并等待渲染
  await page.evaluate(() => {
    const t = document.querySelector('.accountLogin .switch-item-text, div.accountLogin');
    if (t) t.click();
  });
  await page.waitForTimeout(800);
  // 回读确认密码tab已出现输入框
  const hasFields = await page.evaluate(() => !!document.querySelector('input.userAccount') && !!document.querySelector('input.hwid-input-pwd'));
  console.log('PASSWORD_TAB_FIELDS_READY:', hasFields);

  // 模拟自动填充：只 setter 写值，不触发事件 -> 制造"按钮灰色"状态
  await page.evaluate(() => {
    const set = (el, v) => { if (!el) return; Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, v); };
    const u = document.querySelector('input.userAccount');
    const p = document.querySelector('input.hwid-input-pwd');
    set(u, '17386281048');
    set(p, 'qq9900--');
  });
  const filled = await page.evaluate(() => ({
    u: document.querySelector('input.userAccount').value,
    p: document.querySelector('input.hwid-input-pwd').value,
  }));
  console.log('FILLED_VALUES:', JSON.stringify(filled));

  // 注入脚本
  const code = fs.readFileSync('/Users/x/code/anyrouter-checkin/huawei-cloud-auto-login.user.js', 'utf8')
    .replace(/^\/\/ ==UserScript==[\s\S]*?\/\/ ==\/UserScript==\s*/, '');
  await page.evaluate(code);

  await page.waitForTimeout(8000);

  console.log('LOGS:'); logs.forEach(l => console.log('  ' + l));
  console.log('URL_AFTER_AUTO:', page.url());

  console.log('=== 浏览器已交给用户。如需手动补一步/看状态请自行操作，完成后关窗口或终端Ctrl+C。 ===');
  await new Promise(() => {});
})();