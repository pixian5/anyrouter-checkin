// ==UserScript==
// @name         华为云自动登录（修复自动填充后按钮灰色）
// @namespace    huawei-cloud-login
// @version      0.3.0
// @description  华为云新版 hwid Vue3 登录页：浏览器自动填充被 autocomplete=off + 隐藏假表单(hwid-hidden-*) 干扰，且仅派发 input/change 无法点亮按钮。本脚本从隐藏假表单找回填充值，用原生 value setter 逐字符模拟输入点亮按钮，并以合成事件点击登录。
// @author       pixian5
// @match        https://auth.huaweicloud.com/authui/login.html*
// @match        https://auth.huaweicloud.com/authui/*
// @grant        none
// @run-at       document-idle
// @noframes
// ==/UserScript==

(function () {
  'use strict';

  const SEL_USER = ['input.userAccount', 'input[name="userAccount"]', 'input[ht="input_pwdlogin_account"]'];
  const SEL_PWD = ['input.hwid-input-pwd', 'input[ht="input_pwdlogin_pwd"]'];
  const SEL_SUBMIT = ['[ht="click_pwdlogin_submitLogin"]'];

  function isVisible(el) {
    if (!el || !el.isConnected) return false;
    const st = getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }
  function qsFirstVisible(sels) {
    for (const s of sels) for (const el of document.querySelectorAll(s)) if (isVisible(el)) return el;
    return null;
  }

  // 收集所有输入值：可视框优先，隐藏假表单(hwid-hidden-*)为自动填充来源
  function collect() {
    const r = { visU: null, visP: null, uVal: '', pVal: '' };
    // 隐藏假表单（浏览器自动填充经常落到这里）
    const hu = document.querySelector('.hwid-hidden-useraccount, input.hwid-hidden-useraccount');
    const hp = document.querySelector('.hwid-hidden-password, input.hwid-hidden-password');
    if (hu && hu.value) r.uVal = hu.value;
    if (hp && hp.value) r.pVal = hp.value;

    r.visU = qsFirstVisible(SEL_USER);
    r.visP = qsFirstVisible(SEL_PWD);
    const vU = r.visU && r.visU.value;
    const vP = r.visP && r.visP.value;
    if (vU) r.uVal = vU;
    if (vP) r.pVal = vP;
    return r;
  }

  // 原生 value setter 写入（Vue 双向绑定可通过关键事件感知）
  const nativeSet = (el, v) => {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const s = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
    if (s) s.call(el, v); else el.value = v;
  };
  // 逐字符模拟输入 + 完整事件序列（实测此法能点亮按钮）
  function typeIn(el, value) {
    if (!el) return;
    try { el.focus(); } catch (e) {}
    el.value = '';
    nativeSet(el, '');
    for (const ch of value) {
      nativeSet(el, el.value + ch);
      ['keydown', 'keypress', 'input', 'keyup'].forEach((t) =>
        el.dispatchEvent(new InputEvent(t, { bubbles: true, cancelable: true, inputType: 'insertText', data: ch })));
    }
    el.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
    el.dispatchEvent(new Event('blur', { bubbles: true }));
  }

  // 合成鼠标事件点击（mousedown->mouseup->click，比 .click() 更贴近真实）
  function fireClick(el) {
    if (!el) return false;
    const opts = { bubbles: true, cancelable: true, view: window };
    try { el.dispatchEvent(new MouseEvent('mousedown', opts)); } catch (e) {}
    try { el.dispatchEvent(new MouseEvent('mouseup', opts)); } catch (e) {}
    try { el.dispatchEvent(new MouseEvent('click', opts)); } catch (e) { el.click(); }
    return true;
  }

  const submitDisabled = (b) =>
    !b || b.getAttribute('disabled') === 'true' ||
    ((b.className || '') + (b.parentElement ? b.parentElement.className : '') + (b.parentElement && b.parentElement.parentElement ? b.parentElement.parentElement.className : ''))
      .toLowerCase().includes('disabled');

  let clicked = false;

  function attempt() {
    if (!location.pathname.includes('/authui/login')) return;
    const c = collect();
    if (!c.visU || !c.visP) return;

    // 只在有值且可视框为空/不一致时写入
    const needU = c.uVal && !c.visU.value;
    const needP = c.pVal && !c.visP.value;
    if (needU) typeIn(c.visU, c.uVal);
    if (needP) typeIn(c.visP, c.pVal);

    const btn = qsFirstVisible(SEL_SUBMIT) || document.querySelector(SEL_SUBMIT[0]);
    if (!btn) return;

    if (submitDisabled(btn)) {
      // 若刚写入，下一轮再点；若一直被禁用但值已齐，再补一轮输入事件
      if ((c.visU.value && c.visP.value) || needU || needP) {
        typeIn(c.visU, c.visU.value);
        typeIn(c.visP, c.visP.value);
      }
      return;
    }
    if (!clicked && c.visU.value && c.visP.value) {
      clicked = true;
      console.info('[HWCloudAutoLogin] 按钮已点亮，自动登录');
      fireClick(btn);
    }
  }

  let timer = null;
  function start() {
    if (timer) return;
    attempt();
    timer = setInterval(attempt, 700);
    window.addEventListener('beforeunload', () => clearInterval(timer));
  }

  const boot = new MutationObserver(() => {
    if (document.querySelector(SEL_USER[0]) && document.querySelector(SEL_PWD[0])) {
      boot.disconnect();
      start();
      attempt();
    }
  });
  boot.observe(document.documentElement, { childList: true, subtree: true });
  setTimeout(() => { boot.disconnect(); if (!timer) start(); }, 2000);

  console.info('[HWCloudAutoLogin] 华为云自动登录脚本已加载 v0.3.0');
})();