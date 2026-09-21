// ==UserScript==
// @name         华为云自动登录（修复自动填充后按钮灰色）
// @namespace    huawei-cloud-login
// @version      0.4.0
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
  // 可视账号框
  const USERNAME_SEL = ['input.userAccount', 'input[name="userAccount"]', 'input[ht="input_pwdlogin_account"]'];
  // 可视密码框
  const PASSWORD_SEL = ['input.hwid-input-pwd', 'input[ht="input_pwdlogin_pwd"]', '.hwid-pwdlogin-root input[type="password"]'];
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

  // 是否“灰”按钮：查找按钮及其祖先里是否带 disabled 标志
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

  let clickedOnce = false;

  function attemptLogin() {
    if (!location.pathname.includes('/authui/login')) return;
    const c = collectInputs();
    if (!c.visibleU || !c.visibleP) return; // SDK 未就绪

    // 从隐藏假表单/任何输入框找回值，补进可视框
    if (!c.visibleU.value && c.uValues.size) setNativeValue(c.visibleU, [...c.uValues][0]);
    if (!c.visibleP.value && c.pValues.size) setNativeValue(c.visibleP, [...c.pValues][0]);

    // 强制写回 + 派发（无论值原本是否已在可视框，每次都用 setter+事件），持续直到点亮
    if (c.visibleU.value || c.visibleP.value) {
      forceWrite(c.visibleU);
      forceWrite(c.visibleP);
    }

    // 账号和密码都齐了才继续
    if (!c.visibleU.value || !c.visibleP.value) return;

    const btn = getSubmit();
    if (!btn || isButtonDisabled(btn)) return; // 还没点亮，下轮再来

    if (!clickedOnce) {
      clickedOnce = true;
      console.info('[HWCloudAutoLogin] 检测到账号密码已就绪且按钮点亮，自动点击登录');
      const opts = { bubbles: true, cancelable: true, view: window };
      try { btn.dispatchEvent(new MouseEvent('mousedown', opts)); } catch (e) {}
      try { btn.dispatchEvent(new MouseEvent('mouseup', opts)); } catch (e) {}
      try { btn.dispatchEvent(new MouseEvent('click', opts)); } catch (e) { btn.click(); }
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
    const ready = document.querySelector('input.userAccount, input.hwid-input-pwd, [ht="click_pwdlogin_submitLogin"]');
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

  console.info('[HWCloudAutoLogin] 华为云自动登录脚本已加载 v0.4.0');
})();