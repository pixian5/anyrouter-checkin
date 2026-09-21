// ==UserScript==
// @name         华为云自动登录（修复自动填充后按钮灰色）
// @namespace    huawei-cloud-login
// @version      0.1.0
// @description  华为云登录页：浏览器自动填充账号密码后，登录按钮没从灰色变彩色无法点击。本脚本在检测到输入框已有值时强制派发 input/change 事件并启用登录按钮，随后自动点击登录。
// @author       pixian5
// @match        https://auth.huaweicloud.com/authui/login.html*
// @match        https://auth.huaweicloud.com/authui/*
// @grant        none
// @run-at       document-idle
// @noframes
// ==/UserScript==

(function () {
  'use strict';

  // ---------- 元素定位 ----------
  const USERNAME_SELECTORS = ['#username', 'input[id*="user"]', 'input[name*="user"]', 'input[type="text"]'];
  const PASSWORD_SELECTORS = ['#password', 'input[type="password"]'];
  const SUBMIT_SELECTORS = ['button[type="submit"]', '#submitBtn', 'button[login]'];

  function qsOne(selectors) {
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el && el.offsetParent !== null) return el; // 可见
    }
    return null;
  }

  function isDisabled(btn) {
    return !btn || btn.disabled || btn.getAttribute('aria-disabled') === 'true';
  }

  function textMatch(btn, re) {
    return !!btn && re.test((btn.innerText || '').trim());
  }

  function getPasswordInput() {
    return qsOne(PASSWORD_SELECTORS);
  }
  function getUsernameInput() {
    return qsOne(USERNAME_SELECTORS);
  }
  function getSubmitButton() {
    let btn = qsOne(SUBMIT_SELECTORS);
    if (!btn) {
      // 按可见按钮文本兜底
      const allowedMinSize = 24;
      for (const b of document.querySelectorAll('button')) {
        if (b.offsetParent === null) continue;
        const r = b.getBoundingClientRect();
        if (r.width < allowedMinSize || r.height < allowedMinSize) continue;
        if (textMatch(b, /登\s*录|sign\s*in|log\s*in/i)) { btn = b; break; }
      }
    }
    return btn;
  }

  // ---------- 派发事件，让框架感知输入 ----------
  function fireInputEvents(el) {
    if (!el) return;
    ['input', 'change', 'keyup', 'keydown', 'propertychange'].forEach((type) => {
      el.dispatchEvent(new Event(type, { bubbles: true, cancelable: true }));
    });
  }

  // 用原生 setter 写值并派发事件（React/Vue 受控组件最可靠）
  function setNativeValue(el, value) {
    const proto = el instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
    if (setter) setter.call(el, value);
    else el.value = value;
  }

  // ---------- 强制启用登录按钮 ----------
  function enableAndLogin() {
    const submit = getSubmitButton();
    if (!submit) return false;
    if (isDisabled(submit)) {
      submit.disabled = false;
      submit.removeAttribute('disabled');
      submit.removeAttribute('aria-disabled');
    }
    // 触发框架重算 disabled 的兜底输入事件
    const u = getUsernameInput();
    const p = getPasswordInput();
    if (u && u.value) setNativeValue(u, u.value);
    if (p && p.value) setNativeValue(p, p.value);
    fireInputEvents(u);
    fireInputEvents(p);

    // 大部分 SPA 其实只需要一次 input 事件即可点亮按钮
    // 若仍未点亮则直接强制点击
    if (isDisabled(submit)) submit.click();
    return true;
  }

  // ---------- 主流程 ----------
  let clickedOnce = false;
  let lastFailCount = 0;

  function attemptLogin() {
    if (window.location.pathname.split('?')[0] !== '/authui/login.html') return;
    const p = getPasswordInput();
    const u = getUsernameInput();
    if (!p) return; // 登录页未就绪

    const hasValue = !!(u && u.value) || !!p.value;
    if (!hasValue) return; // 等浏览器自动填充

    const submit = getSubmitButton();
    if (!submit) return;

    if (isDisabled(submit)) {
      if (enableAndLogin()) return; // 已派发事件，这块会点亮按钮
    }

    // 按钮已点亮：自动登录（只点一次）
    if (!clickedOnce) {
      clickedOnce = true;
      console.info('[HWCloudAutoLogin] 检测到已填充凭证，自动点击登录');
      submit.click();
    }
  }

  // 轮询检测自动填充（自动填充不触发普通事件，只能轮询/MutationObserver）
  let timer = null;
  function startWatch() {
    if (timer) return;
    attemptLogin();
    timer = setInterval(attemptLogin, 500);
    // 页面卸载清理
    window.addEventListener('beforeunload', () => clearInterval(timer));
  }

  // 表单出现后开始监视
  const bootObserver = new MutationObserver(() => {
    if (getPasswordInput() || getUsernameInput()) {
      bootObserver.disconnect();
      startWatch();
      attemptLogin();
    }
  });
  bootObserver.observe(document.documentElement, { childList: true, subtree: true });

  // 兜底：无论表单是否就绪都尽早启动轮询
  setTimeout(() => {
    bootObserver.disconnect();
    if (!timer) startWatch();
  }, 1500);

  console.info('[HWCloudAutoLogin] 华为云自动登录脚本已加载 v0.1.0');
})();