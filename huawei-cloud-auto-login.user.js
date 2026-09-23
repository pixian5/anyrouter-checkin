// ==UserScript==
// @name         华为云自动登录（点亮灰色登录按钮并自动登录）
// @namespace    huawei-cloud-login
// @version      0.8.0
// @description  华为云新版登录页(hwid)：浏览器已自动切换到密码登录tab并自动填充账号密码，但登录按钮仍为灰色。脚本检测到账号密码已就绪后，用原生setter+input事件把按钮点亮，随后自动点击登录。不做任何切tab/补密码操作。
// @author       pixian5
// @match        https://auth.huaweicloud.com/authui/login.html*
// @match        https://auth.huaweicloud.com/authui/*
// @grant        none
// @run-at       document-idle
// @noframes
// ==/UserScript==

(function () {
  'use strict';

  // 可视账号框
  const USERNAME_SEL = 'input.userAccount, input[name="userAccount"], [ht="input_pwdlogin_account"]';
  // 可视密码框
  const PASSWORD_SEL = 'input.hwid-input-pwd, [ht="input_pwdlogin_pwd"]';
  // 登录按钮
  const SUBMIT_SEL = '[ht="click_pwdlogin_submitLogin"]';

  function isVisible(el) {
    if (!el || !el.isConnected) return false;
    const st = getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }

  const q1 = (sel) => {
    for (const el of document.querySelectorAll(sel)) if (isVisible(el)) return el;
    return null;
  };

  function setNativeValue(el, value) {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
    if (setter) setter.call(el, value);
    else el.value = value;
  }

  // 核心：把已有值强制写回并派发事件，让 Vue 把按钮点亮
  function lightUp(el) {
    if (!el) return;
    setNativeValue(el, el.value);
    ['input', 'change', 'keyup', 'keydown', 'blur'].forEach((t) => {
      try { el.dispatchEvent(new InputEvent(t, { bubbles: true, inputType: 'insertText' })); }
      catch (e) { el.dispatchEvent(new Event(t, { bubbles: true, cancelable: true })); }
    });
  }

  function isButtonDisabled(btn) {
    if (!btn) return true;
    if (btn.getAttribute && btn.getAttribute('disabled') === 'true') return true;
    let node = btn;
    while (node && node !== document.body) {
      const cls = (node.className && String(node.className)) || '';
      if (/\bdisabled\b|btn-?disabled|is-?disabled/i.test(cls)) return true;
      if (node.getAttribute && node.getAttribute('disabled') === 'true') return true;
      node = node.parentElement;
    }
    return false;
  }

  function clickLogin(btn, pwdEl) {
    // 1) 聚焦密码框按 Enter
    try { pwdEl && pwdEl.focus(); } catch (e) {}
    const keyOpts = { bubbles: true, cancelable: true, composed: true, view: window, key: 'Enter', code: 'Enter', keyCode: 13, which: 13 };
    for (const [t, C] of [['keydown', KeyboardEvent], ['keypress', KeyboardEvent], ['keyup', KeyboardEvent]]) {
      try { pwdEl && pwdEl.dispatchEvent(new C(t, keyOpts)); } catch (e) { try { pwdEl && pwdEl.dispatchEvent(new Event(t, keyOpts)); } catch (e2) {} }
    }
    // 2) 完整指针事件序列点击按钮
    const opts = { bubbles: true, cancelable: true, composed: true, view: window, pointerId: 1, isPrimary: true, button: 0, buttons: 1, clientX: 0, clientY: 0, pointerType: 'mouse' };
    for (const [t, C] of [['pointerdown', PointerEvent], ['mousedown', MouseEvent], ['pointerup', PointerEvent], ['mouseup', MouseEvent], ['click', MouseEvent]]) {
      try { btn.dispatchEvent(new C(t, opts)); } catch (e) { try { btn.dispatchEvent(new Event(t, opts)); } catch (e2) {} }
    }
    try { btn.click(); } catch (e) {}
  }

  let loginAttempted = false;

  function attempt() {
    if (!location.pathname.includes('/authui/login')) return;

    const u = q1(USERNAME_SEL);
    const p = q1(PASSWORD_SEL);
    if (!u || !p) return; // 账号密码框未出现

    // 只有两边都有值才继续（浏览器自动填充的）
    if (!u.value || !p.value) return;

    // 点亮按钮（不管当前亮没亮，反复强制写回+事件，直到点亮）
    lightUp(u);
    lightUp(p);

    const btn = q1(SUBMIT_SEL);
    if (!btn || isButtonDisabled(btn)) {
      console.info('[HWCloudAutoLogin] 有值但按钮仍未点亮，重试: ' + u.value);
      return;
    }

    if (!loginAttempted) {
      loginAttempted = true;
      console.info('[HWCloudAutoLogin] 按钮已点亮，自动点击登录');
      clickLogin(btn, p);
    }
  }

  let timer = null;
  function start() {
    if (timer) return;
    attempt();
    timer = setInterval(attempt, 600);
    window.addEventListener('beforeunload', () => clearInterval(timer));
  }

  const boot = new MutationObserver(() => {
    if (document.querySelector('input.userAccount, input.hwid-input-pwd')) {
      boot.disconnect();
      start();
      attempt();
    }
  });
  boot.observe(document.documentElement, { childList: true, subtree: true });
  setTimeout(() => { boot.disconnect(); if (!timer) start(); }, 2000);

  console.info('[HWCloudAutoLogin] 华为云自动登录脚本已加载 v0.8.0');
})();