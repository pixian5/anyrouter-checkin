// ==UserScript==
// @name         华为云自动登录（修复自动填充后按钮灰色）
// @namespace    huawei-cloud-login
// @version      0.2.0
// @description  华为云新版登录页(hwid Vue3 SDK)：浏览器自动填充只填隐藏假表单，可视输入框拿不到值，且登录按钮(.normalBtn)非<button>。本脚本把隐藏值复制到可视框、派发input/change事件点亮按钮，然后自动点击登录。
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

  // 用原生 setter 写值并派发事件（Vue v-model 更新）
  function setNativeValue(el, value) {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
    if (setter) setter.call(el, value);
    else el.value = value;
  }
  function fire(el) {
    if (!el) return;
    ['input', 'change', 'keyup', 'blur'].forEach((t) => el.dispatchEvent(new Event(t, { bubbles: true, cancelable: true })));
  }

  function getSubmit() {
    return qsFirstVisible(SUBMIT_SEL) || document.querySelector('[ht="click_pwdlogin_submitLogin"]');
  }

  // 勾勒是否“灰”按钮（新版用 hwid-btn 且可能带 disabled）
  function isButtonDisabled(btn) {
    if (!btn) return true;
    if (btn.getAttribute('disabled') === 'true') return true;
    // 灰样式类兜底
    const cls = (btn.className || '');
    if (/\bdisabled\b|btn-?disabled|hwid-btn-not-?available/i.test(cls)) return true;
    return false;
  }

  let clickedOnce = false;

  function attemptLogin() {
    if (!location.pathname.includes('/authui/login')) return;
    const c = collectInputs();
    if (!c.visibleU || !c.visibleP) return; // SDK 未就绪

    // 若某可视框没值，但从别处(含隐藏假表单)拿到了值 -> 补上
    let changed = false;
    if (!c.visibleU.value && c.uValues.size) {
      setNativeValue(c.visibleU, [...c.uValues][0]);
      changed = true;
    }
    if (!c.visibleP.value && c.pValues.size) {
      setNativeValue(c.visibleP, [...c.pValues][0]);
      changed = true;
    }
    if (changed || c.visibleU.value || c.visibleP.value) {
      fire(c.visibleU);
      fire(c.visibleP);
      // 若密码框还是空的（自动填充只填了账号或只在隐藏框），仍需继续等
      if (!c.visibleU.value || !c.visibleP.value) return;
    }

    const btn = getSubmit();
    if (!btn || isButtonDisabled(btn)) {
      // 派发事件让框架点亮；若仍未点亮再补点一次事件重试
      fire(c.visibleU);
      fire(c.visibleP);
      return;
    }

    if (!clickedOnce) {
      clickedOnce = true;
      console.info('[HWCloudAutoLogin] 检测到账号密码已就绪，自动点击登录');
      btn.click();
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

  console.info('[HWCloudAutoLogin] 华为云自动登录脚本已加载 v0.2.0');
})();