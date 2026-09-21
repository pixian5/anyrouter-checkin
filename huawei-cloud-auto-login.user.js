// ==UserScript==
// @name         华为云自动登录（修复自动填充后按钮灰色）
// @namespace    huawei-cloud-login
// @version      0.7.1
// @description  华为云新版登录页(hwid Vue3 SDK)：浏览器自动填充后登录按钮灰色。核心思路：无论自动填充落在可视框还是隐藏假表单，都强制用 value setter 写回 + 派发完整 input 事件序列，持续直到按钮点亮再用合成事件点击登录。
// @author       pixian5
// @match        https://auth.huaweicloud.com/authui/login.html*
// @match        https://auth.huaweicloud.com/authui/*
// @grant        none
// @run-at       document-idle
// @noframes
// ==/UserScript==

(function () {
  'use strict';

  // ---------- 新版 hwid Vue3 SDK 选择器 ----------
  // 登录方式 tab：页面默认在"手机号登录"，必须先切到"密码登录"才出现账号密码框
  // 密码登录 tab 父元素 .accountLogin（文本元素 .switch-item-text）
  const PWD_TAB_SEL = ['.accountLogin .switch-item-text', 'div.accountLogin', '.switch-item-text']; 
  // 可视账号框
  const USERNAME_SEL = ['input.userAccount', 'input[name="userAccount"]', 'input[ht="input_pwdlogin_account"]', '.accountLogin input[type="text"]'];
  // 可视密码框
  const PASSWORD_SEL = ['input.hwid-input-pwd', 'input[ht="input_pwdlogin_pwd"]', '.hwid-pwdlogin-root input[type="password"]', '.accountLogin input[type="password"]'];
  // 触发登录的可点击元素
  const SUBMIT_SEL = ['[ht="click_pwdlogin_submitLogin"]', '.normalBtn[ht="click_pwdlogin_submitLogin"]'];

  function isVisible(el) {
    if (!el || !el.isConnected) return false;
    const st = el.ownerDocument ? getComputedStyle(el) : null;
    if (st && (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity) === 0)) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }

  function qsFirstVisible(selectors) {
    for (const sel of selectors) {
      for (const el of document.querySelectorAll(sel)) {
        if (isVisible(el)) return el;
      }
    }
    return null;
  }

  let pwdTabHandled = false; // 记录密码tab是否已处理过（只抢切一次，避免和用户来回抢）

  // 切换到"密码登录"tab（页面默认在手机号/短信登录tab，账号密码框此时不存在）
  // 只允许主动抢切一次：处理完密码登录后即让位，用户切回手机号就保持手机号，不再强制切回
  function switchToPwdTab() {
    if (pwdTabHandled) return false;
    if (qsFirstVisible(USERNAME_SEL) && qsFirstVisible(PASSWORD_SEL)) return true;
    const tab = qsFirstVisible(PWD_TAB_SEL);
    if (tab) {
      try { tab.click(); } catch (e) {
        try {
          tab.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
          tab.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
          tab.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
        } catch (e2) {}
      }
      console.info('[HWCloudAutoLogin] 已点击切换到密码登录 tab');
      return true;
    }
    return false;
  }

  // 收集所有输入框（含隐藏假表单里的），用于找回自动填充的值
  function collectInputs() {
    const out = { visibleU: null, visibleP: null, uValues: new Set(), pValues: new Set() };
    for (const el of document.querySelectorAll('input')) {
      const type = (el.type || '').toLowerCase();
      const vis = isVisible(el);
      const cls = el.className;
      const isAcc = type === 'text' && (/useraccount|userAccount|username/i.test(cls) || el.name === 'userAccount' || el.getAttribute('ht') === 'input_pwdlogin_account');
      const isPwd = type === 'password';

      if (isPwd) {
        if (el.value) out.pValues.add(el.value);
        if (vis && cls.includes('hwid-input-pwd')) out.visibleP = el;
        else if (vis && !out.visibleP && cls.includes('hwid-input')) out.visibleP = el;
      } else if (isAcc) {
        if (el.value) out.uValues.add(el.value);
        if (vis && cls.includes('userAccount')) out.visibleU = el;
      } else if (vis && type === 'text' && !out.visibleU && cls.includes('hwid-input')) {
        // 兜底：可见的 hwid 文本框
        if (el.value) out.uValues.add(el.value);
        out.visibleU = el;
      }
    }
    return out;
  }

  // 用原生 setter 强制写回值（Vue v-model 关键：必须 setter 而非直接 el.value）
  function setNativeValue(el, value) {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
    if (setter) setter.call(el, value);
    else el.value = value;
  }

  // 强制写回 + 派发完整事件序列（实测：此路径才能点亮按钮）
  const forceWrite = (el) => {
    if (!el) return;
    setNativeValue(el, el.value || '');
    ['input', 'change', 'keyup', 'keydown', 'blur'].forEach((t) => {
      try { el.dispatchEvent(new InputEvent(t, { bubbles: true, inputType: 'insertText' })); }
      catch (e) { el.dispatchEvent(new Event(t, { bubbles: true, cancelable: true })); }
    });
  };

  function getSubmit() {
    return qsFirstVisible(SUBMIT_SEL) || document.querySelector('[ht="click_pwdlogin_submitLogin"]');
  }
  function isButtonDisabled(btn) {
    if (!btn) return true;
    if (btn.getAttribute('disabled') === 'true') return true;
    let node = btn;
    while (node && node !== document.body) {
      const cls = (node.className && String(node.className)) || '';
      if (/\bdisabled\b|btn-?disabled|is-?disabled/i.test(cls)) return true;
      if (node.getAttribute('disabled') === 'true') return true;
      node = node.parentElement;
    }
    return false;
  }

  let loginAttempted = false;

  // 真实聚焦密码框（Enter 提交依赖焦点）
  function focusPwd(el) {
    try { el.focus({ preventScroll: true }); } catch (e) { try { el.focus(); } catch (e2) {} }
  }

  // 完整指针事件序列（比纯 mouse 更贴近真实点击）
  function firePointer(el) {
    const opts = { bubbles: true, cancelable: true, composed: true, view: window, pointerId: 1, isPrimary: true, button: 0, buttons: 1, clientX: 0, clientY: 0, pointerType: 'mouse' };
    const seq = [
      ['pointerdown', PointerEvent],
      ['mousedown', MouseEvent],
      ['pointerup', PointerEvent],
      ['mouseup', MouseEvent],
      ['click', MouseEvent],
    ];
    for (const [type, Ctor] of seq) {
      try { el.dispatchEvent(new Ctor(type, opts)); } catch (e) {
        try { el.dispatchEvent(new Event(type, opts)); } catch (e2) {}
      }
    }
    try { el.click(); } catch (e) {}
  }

  // 密码框 Enter 提交
  function fireEnter(el) {
    focusPwd(el);
    const opts = { bubbles: true, cancelable: true, composed: true, view: window, key: 'Enter', code: 'Enter', keyCode: 13, which: 13 };
    for (const type of [
      ['keydown', KeyboardEvent],
      ['keypress', KeyboardEvent],
      ['keyup', KeyboardEvent],
    ]) {
      const [t, Ctor] = type;
      try { el.dispatchEvent(new Ctor(t, opts)); } catch (e) {
        try { el.dispatchEvent(new Event(t, opts)); } catch (e2) {}
      }
    }
  }

  // 从 localStorage 读可选账号/密码（放在本机，不写公共仓库）
  function savedCreds() {
    try {
      const u = (localStorage.getItem('HUAWEI_AUTO_USER') || '').trim();
      const p = localStorage.getItem('HUAWEI_AUTO_PASS') || '';
      return { u, p };
    } catch (e) { return { u: '', p: '' }; }
  }

  function attemptLogin() {
    if (!location.pathname.includes('/authui/login')) return;

    // 先确保切到"密码登录"tab（页面默认在手机号tab，账号密码框此时不存在）
    switchToPwdTab();

    const c = collectInputs();
    if (!c.visibleU || !c.visibleP) return; // SDK 未就绪

    const cred = savedCreds();

    // 数据来源优先级：可视框已有值 > 隐藏假表单值 > localStorage 配置
    const uVal = c.visibleU.value || ([...c.uValues][0] || '') || cred.u;
    const pVal = c.visibleP.value || ([...c.pValues][0] || '') || cred.p;

    // 有账号但密码为空（Chrome 因 autocomplete=off 不填充密码框）-> 用配置补，否则永远点不亮
    if (uVal && !c.visibleP.value && pVal) {
      console.info('[HWCloudAutoLogin] 检测到账号已填充但密码为空，用本机配置补填密码');
      setNativeValue(c.visibleP, pVal);
    }
    if (c.visibleU.value !== uVal && uVal) setNativeValue(c.visibleU, uVal);
    if (c.visibleP.value !== pVal && pVal) setNativeValue(c.visibleP, pVal);

    // 强制写回 + 派发（核心：无论是否已填，每次都用 setter+事件），持续直到点亮
    if (c.visibleU.value || c.visibleP.value) {
      forceWrite(c.visibleU);
      forceWrite(c.visibleP);
    }

    // 账号和密码都齐了才继续
    if (!c.visibleU.value || !c.visibleP.value) return;

    const btn = getSubmit();
    if (!btn || isButtonDisabled(btn)) {
      console.info('[HWCloudAutoLogin] 有值但按钮仍未点亮，继续重试: u=' + c.visibleU.value + ' p=' + (c.visibleP.value ? '***' : ''));
      return;
    }

    if (!loginAttempted) {
      loginAttempted = true;
      pwdTabHandled = true; // 密码tab已处理，之后不再主动抢切（让位给用户，切回手机号就保持）
      console.info('[HWCloudAutoLogin] 检测到账号密码已就绪且按钮点亮，尝试自动登录');

      // 策略1：在密码框真实聚焦后按 Enter（依赖表单/框架提交逻辑，不依赖 isTrusted）
      // 策略2：对登录按钮派发完整指针事件序列 + click
      // 两者先后都触发，避免单一路径被风控忽略
      fireEnter(c.visibleP);
      window.setTimeout(() => { try { fireEnter(c.visibleP); } catch (e) {} }, 120);
      window.setTimeout(() => { try { firePointer(btn); } catch (e) {} }, 250);
      window.setTimeout(() => { try { fireEnter(c.visibleP); } catch (e) {} }, 400);
    }
  }

  let timer = null;
  function startWatch() {
    if (timer) return;
    attemptLogin();
    timer = setInterval(attemptLogin, 600);
    window.addEventListener('beforeunload', () => clearInterval(timer));
  }

  const boot = new MutationObserver(() => {
    const ready = document.querySelector('input.userAccount, input.hwid-input-pwd, [ht="click_pwdlogin_submitLogin"], .accountLogin, .switch-item-text');
    if (ready) {
      boot.disconnect();
      startWatch();
      attemptLogin();
    }
  });
  boot.observe(document.documentElement, { childList: true, subtree: true });
  setTimeout(() => {
    boot.disconnect();
    if (!timer) startWatch();
  }, 2000);

  console.info('[HWCloudAutoLogin] 华为云自动登录脚本已加载 v0.7.0');
})();