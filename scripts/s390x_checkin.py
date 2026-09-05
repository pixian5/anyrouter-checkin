#!/usr/bin/env python3
"""
s390x/server 纯 HTTP 统一签到脚本。

无需浏览器，仅依赖 httpx + node(解 anyrouter WAF 挑战 JS)。
支持 provider:
  - agentrouter:  邮箱密码登录 ps.air-outer.com，GET /api/user/self 自动签到
  - anyrouter:    解 acw_sc__v2 WAF 挑战 + session cookie 执行 /api/user/sign_in

数据：每次签到把余额/累计消耗写入 SQLite 审计库(checkin_history.sqlite3)，
     并据此计算相对上次记录的余额变化。

配置：从 .env 读取（ANYROUTER_ACCOUNTS / BARK_SERVER / BARK_KEY）
版本：0.4.6
"""

import asyncio
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import httpx

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / '.env'
DB_PATH = BASE_DIR / 'checkin_history.sqlite3'

# new-api/agentrouter 余额原始单位 -> 美元 的换算系数(500000 单位 = $1)
QUOTA_TO_USD = 500000.0

# ---------- provider 配置 ----------
PROVIDERS = {
    'agentrouter': {
        'domain': 'https://ps.air-outer.com',
        'user_agent': (
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36'
        ),
    },
    'anyrouter': {
        'domain': 'https://anyrouter.top',
        'user_agent': (
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36'
        ),
    },
}

ALREADY_CHECKED_KEYWORDS = ('已经签到', '已签到', '重复签到', 'already checked', 'already signed')


# ---------- .env 读取 ----------
def load_dotenv() -> dict:
    env = {}
    if not ENV_PATH.exists():
        return env
    for raw in ENV_PATH.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


# ---------- SQLite 审计库 ----------
def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS checkin_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_key TEXT NOT NULL,
            name TEXT NOT NULL,
            provider TEXT NOT NULL,
            success INTEGER NOT NULL,
            skipped INTEGER NOT NULL,
            before_quota REAL,
            before_used REAL,
            after_quota REAL,
            after_used REAL,
            check_in_reward REAL,
            usage_increase REAL,
            balance_change REAL,
            baseline_balance_change REAL,
            checkin_time TEXT NOT NULL
        )
        """
    )
    conn.execute('CREATE INDEX IF NOT EXISTS idx_history_account ON checkin_history(account_key, id DESC)')
    conn.commit()
    return conn


def last_balance(conn: sqlite3.Connection, account_key: str) -> tuple[float, float] | None:
    """返回该账号最近一次成功记录后的 (quota, used_quota)；无则 None。"""
    row = conn.execute(
        'SELECT after_quota, after_used FROM checkin_history '
        'WHERE account_key=? AND success=1 ORDER BY id DESC LIMIT 1',
        (account_key,),
    ).fetchone()
    if not row or row[0] is None:
        return None
    return float(row[0]), float(row[1] or 0.0)


def checked_in_today(conn: sqlite3.Connection, account_key: str, today: str) -> bool:
    row = conn.execute(
        'SELECT 1 FROM checkin_history WHERE account_key=? AND success=1 AND checkin_time LIKE ? LIMIT 1',
        (account_key, f'{today}%'),
    ).fetchone()
    return row is not None


def insert_history(conn: sqlite3.Connection, record: dict) -> None:
    conn.execute(
        """
        INSERT INTO checkin_history (
            account_key, name, provider, success, skipped,
            before_quota, before_used, after_quota, after_used,
            check_in_reward, usage_increase, balance_change, baseline_balance_change,
            checkin_time
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            record['account_key'], record['name'], record['provider'],
            int(record.get('success', False)), int(record.get('skipped', False)),
            record.get('before_quota'), record.get('before_used'),
            record.get('after_quota'), record.get('after_used'),
            record.get('check_in_reward'), record.get('usage_increase'),
            record.get('balance_change'), record.get('baseline_balance_change'),
            record['checkin_time'],
        ),
    )
    conn.commit()


# ---------- 余额换算 ----------
def usd(q):
    try:
        f = float(q)
    except (TypeError, ValueError):
        return None
    return f / QUOTA_TO_USD if f else 0.0


# ---------- anyrouter WAF 挑战求解(browser shim via node vm) ----------
# 通过 node 的 vm 模块提供完整浏览器全局环境，执行混淆挑战 JS 解出 acw_sc__v2。
NODE_SOLVER = r"""
const fs = require('fs'), vm = require('vm');
const chall = fs.readFileSync('/tmp/acw_chal.js', 'utf8');
const noop = () => {};
let cookies = {};
const locEl = { style:{}, getAttribute:()=>null, setAttribute:noop };
const loc = { href:'https://anyrouter.top/login', host:'anyrouter.top', hostname:'anyrouter.top',
  pathname:'/login', protocol:'https:', origin:'https://anyrouter.top', reload:noop, replace:noop, assign:noop };
const doc = {
  get cookie(){ return Object.keys(cookies).map(k=>k+'='+cookies[k]).join('; '); },
  set cookie(v){ const m = v.match(/^\s*([^=;]+)=([^;]*)/); if(m) cookies[m[1].trim()] = m[2].trim(); },
  location: loc, documentElement: locEl, head:{appendChild:noop}, body:{appendChild:noop},
  write:noop, writeln:noop, createElement:()=>({style:{},setAttribute:noop,appendChild:noop,removeChild:noop}),
  createTextNode:()=>({}), getElementById:()=>null, getElementsByTagName:()=>[], querySelector:()=>null,
  querySelectorAll:()=>[], addEventListener:noop, removeEventListener:noop, attachEvent:noop,
  title:'', referrer:'', domain:'anyrouter.top', readyState:'complete', hidden:false, visibilityState:'visible',
  defaultView: null,
};
const nav = { userAgent:"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36",
  platform:'Linux x86_64', language:'zh-CN', languages:['zh-CN','zh'], cookieEnabled:true, onLine:true,
  webdriver:false, hardwareConcurrency:8, deviceMemory:8, maxTouchPoints:0, vendor:'Google Inc.' };
const scr = { width:1920,height:1080,availWidth:1920,availHeight:1040,colorDepth:24,pixelDepth:24,orientation:{type:'landscape-primary'} };
const sandbox = {
  document:doc, navigator:nav, location:loc, screen:scr,
  performance:{ now:()=>Date.now(), timing:{navigationStart:Date.now()-5000} },
  history:{length:1}, frames:null, length:0,
  setTimeout, setInterval, clearTimeout, clearInterval,
  addEventListener:noop, removeEventListener:noop, attachEvent:noop,
  fetch:noop, XMLHttpRequest:function(){}, WebSocket:function(){},
  getComputedStyle:()=>({}), matchMedia:()=>({matches:false,addEventListener:noop}),
  requestAnimationFrame:noop, cancelAnimationFrame:noop,
  localStorage:{getItem:()=>null,setItem:noop,removeItem:noop},
  sessionStorage:{getItem:()=>null,setItem:noop,removeItem:noop},
  btoa:s=>Buffer.from(s,'binary').toString('base64'), atob:s=>Buffer.from(s,'base64').toString('binary'),
  prompt:()=>null, alert:noop, confirm:()=>true, print:noop,
};
sandbox.window=sandbox; sandbox.self=sandbox; sandbox.top=sandbox; sandbox.parent=sandbox;
sandbox.globalThis=sandbox; sandbox.frames=sandbox; doc.defaultView=sandbox;
const ctx = vm.createContext(sandbox);
try { vm.runInContext(chall, ctx, {filename:'chall.js'}); } catch(e) {}
console.log(JSON.stringify(cookies));
"""


def solve_acw(body: str) -> str | None:
    m = re.search(r'<script[^>]*>(.*?)</script>', body, re.S)
    if not m:
        return None
    Path('/tmp/acw_chal.js').write_text(m.group(1), encoding='utf-8')
    Path('/tmp/acw_solver.js').write_text(NODE_SOLVER, encoding='utf-8')
    try:
        p = subprocess.run(['node', '/tmp/acw_solver.js'], capture_output=True, text=True, timeout=40)
    except Exception as e:  # noqa: BLE001
        print(f'  [WAF-SOLVE-ERROR] node 执行失败: {type(e).__name__} {e}')
        return None
    try:
        data = json.loads(p.stdout.strip().splitlines()[-1])
        val = data.get('acw_sc__v2')
        return val if val else None
    except Exception as e:  # noqa: BLE001
        print(f'  [WAF-SOLVE-FAIL] 无法解析 node 输出: {type(e).__name__} '
              f'stdout={p.stdout[:120]!r} stderr={p.stderr[:120]!r}')
        return None


# ---------- 单账号签到 ----------
async def http_login_agentrouter(client, acc: dict, cfg: dict, baseline) -> dict:
    """agentrouter 邮箱密码登录 + user/self 自动签到，返回详情。"""
    domain = cfg['domain']
    model = {
        'provider': 'agentrouter', 'name': acc.get('name'), 'success': False, 'skipped': False,
        'usage_increase': 0.0,
    }
    try:
        await client.get(f'{domain}/login')
    except Exception as e:  # noqa: BLE001
        model['message'] = f'登录页获取失败: {type(e).__name__}'
        return model

    payload = {'username': acc['email'], 'password': acc['password']}
    try:
        r = await client.post(
            f'{domain}/api/user/login',
            json=payload,
            headers={'Content-Type': 'application/json', 'Origin': domain, 'Referer': f'{domain}/login'},
        )
    except Exception as e:  # noqa: BLE001
        model['message'] = f'登录请求失败: {type(e).__name__}'
        return model

    d = r.json()
    if r.status_code >= 400 or not d.get('success'):
        model['message'] = f'登录失败 code={r.status_code} msg={d.get("message", "")}'
        return model
    uid = (d.get('data') or {}).get('id')
    api_user = acc.get('api_user') or (str(uid) if uid is not None else None)

    api_headers = {
        'User-Agent': cfg['user_agent'],
        'Accept': 'application/json, text/plain, */*',
        'Origin': domain,
        'Referer': f'{domain}/console',
    }
    if api_user:
        api_headers['New-Api-User'] = api_user

    try:
        rr = await client.get(f'{domain}/api/user/self', headers=api_headers)
        dd = rr.json()
    except Exception as e:  # noqa: BLE001
        model['message'] = f'user/self 请求失败: {type(e).__name__}'
        return model

    data = dd.get('data', {}) if isinstance(dd, dict) else {}
    success = bool(isinstance(dd, dict) and dd.get('success'))
    after_q = data.get('quota')
    after_u = data.get('used_quota', 0)
    model['api_user'] = api_user
    model['after_quota'] = after_q
    model['after_used'] = after_u
    model['success'] = success
    model['message'] = (dd or {}).get('message', '') if isinstance(dd, dict) else ''

    # 相对上次记录的余额结算
    if success and after_q is not None:
        if baseline is not None:
            model['before_quota'] = baseline[0]
            model['before_used'] = baseline[1]
            delta = float(after_q) - baseline[0]
            model['balance_change'] = delta
            model['check_in_reward'] = delta if delta > 0 else 0.0
            model['baseline_balance_change'] = delta
        else:
            model['before_quota'] = None
    return model


async def http_checkin_anyrouter(client, acc: dict, cfg: dict, baseline, skip_signin: bool) -> dict:
    """anyrouter 解 WAF + session 签到。"""
    domain = cfg['domain']
    api_user = acc.get('api_user')
    session = acc.get('session') or (acc.get('cookies') or {}).get('session')
    model = {
        'provider': 'anyrouter', 'name': acc.get('name'), 'success': False, 'skipped': False,
        'usage_increase': 0.0,
    }
    if not session:
        model['message'] = 'anyrouter 需提供 session cookie'
        return model

    api_headers = {
        'User-Agent': cfg['user_agent'],
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Origin': domain,
        'Referer': f'{domain}/console',
        'Content-Type': 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
    }
    if api_user:
        api_headers['New-Api-User'] = api_user

    async def show_user():
        try:
            rr = await client.get(f'{domain}/api/user/self', headers=api_headers, timeout=25)
            dd = rr.json()
            data = dd.get('data', {}) if isinstance(dd, dict) else {}
            return data.get('quota'), data.get('used_quota', 0)
        except Exception:
            return None, None

    try:
        r = await client.get(f'{domain}/login')
        acw_tc = r.cookies.get('acw_tc')
        cdn_sec_tc = r.cookies.get('cdn_sec_tc')
        acw_sc_v2 = solve_acw(r.text)
        if not (acw_tc and cdn_sec_tc and acw_sc_v2):
            model['message'] = f'WAF 解算不完整 acw={bool(acw_tc)} cdn={bool(cdn_sec_tc)} v2={bool(acw_sc_v2)}'
            return model
        client.cookies.update({
            'session': session, 'acw_tc': acw_tc, 'cdn_sec_tc': cdn_sec_tc, 'acw_sc__v2': acw_sc_v2,
        })
    except Exception as e:  # noqa: BLE001
        model['message'] = f'WAF 获取失败: {type(e).__name__}'
        return model

    before_q, before_u = await show_user()
    if not skip_signin:
        try:
            sr = await client.post(f'{domain}/api/user/sign_in', headers=api_headers, timeout=25)
            sdd = sr.json()
        except Exception as e:  # noqa: BLE001
            model['message'] = f'签到请求失败: {type(e).__name__}'
            return model
        msg = sdd.get('message', '') if isinstance(sdd, dict) else ''
        success = bool(isinstance(sdd, dict) and sdd.get('success'))
        if not success and any(k in (msg or '').lower() for k in ALREADY_CHECKED_KEYWORDS):
            model['skipped'] = True
            success = False
        elif success:
            model['success'] = True
        else:
            model['message'] = msg or '签到失败'
    else:
        model['skipped'] = True

    after_q, after_u = await show_user()
    model['before_quota'] = before_q
    model['before_used'] = before_u
    model['after_quota'] = after_q
    model['after_used'] = after_u
    model['api_user'] = api_user

    if model['success'] and after_q is not None and before_q is not None:
        delta_run = float(after_q) - float(before_q)
        model['balance_change'] = delta_run
        model['check_in_reward'] = delta_run if delta_run > 0 else 0.0
    if after_q is not None and baseline is not None:
        model['baseline_balance_change'] = float(after_q) - baseline[0]
    if model['skipped']:
        model['success'] = False  # 跳过不算成功(用于统计成功数)
        if after_q is not None and baseline is not None:
            if model['balance_change'] == 0:
                model['baseline_balance_change'] = float(after_q) - baseline[0]
    return model


# ---------- 账号 key / 显示名 ----------
def account_key(acc: dict) -> str:
    val = acc.get('email') or acc.get('name') or acc.get('api_user') or ''
    p = (acc.get('provider') or 'anyrouter').strip().lower()
    return f'{p}:{val}'


def display_name(acc: dict) -> str:
    return acc.get('name') or acc.get('email') or acc.get('api_user') or ''


# ---------- 单账号通知块(用户指定格式) ----------
def format_account_block(detail: dict, check_in_time: str) -> str:
    name = detail.get('name') or detail.get('api_user') or ''
    t = f' @ {check_in_time}' if check_in_time else ''
    sep = '  ━━━━━━━━━━━━━━━━━━━━'
    if not detail.get('success'):
        error = detail.get('message') or '未知错误'
        return f'{name}\n[FAIL]{t}\n{sep}\n  ❌ 签到失败\n  📝 错误: {error}\n{sep}'

    tag = '[SKIP]' if detail.get('skipped') else '[CHECK-IN]'
    bq = detail.get('before_quota')
    bu = detail.get('before_used')
    aq = detail.get('after_quota')
    au = detail.get('after_used')

    usd_bq, usd_aq = usd(bq), usd(aq)
    if usd_aq is None:
        return f'{name}\n{tag}{t}\n{sep}\n  📍 当前 💵 余额: 未知\n{sep}'

    bu = 0.0 if bu is None else float(bu)
    au = 0.0 if au is None else float(au)
    lines = [
        f'{name}',
        f'{tag}{t}',
        sep,
        f'  📍 签到前 💵 余额: ${usd_bq:.2f}  |  📊 累计消耗: ${usd(bu):.2f}' if usd_bq is not None
        else f'  📍 当前 💵 余额: ${usd_aq:.2f}  |  📊 累计消耗: ${usd(au):.2f}',
    ]
    if usd_bq is not None:
        lines.append(f'  📍 签到后 💵 余额: ${usd_aq:.2f}  |  📊 累计消耗: ${usd(au):.2f}')
    lines.append(sep)

    reward = detail.get('check_in_reward') or 0
    balance_change = detail.get('balance_change') or 0
    baseline_change = detail.get('baseline_balance_change') or 0

    if not detail.get('skipped') and reward > 0:
        lines.append(f'  🎁 签到获得: +${usd(reward):.2f}')
    if not detail.get('skipped') and balance_change != 0:
        sym = '+' if balance_change > 0 else ''
        lines.append(f'  💹 余额变化: {sym}${usd(abs(balance_change)):.2f}')
    elif baseline_change != 0:
        sym = '+' if baseline_change > 0 else ''
        lines.append(f'  📈 相比上次记录余额变化: {sym}${usd(abs(baseline_change)):.2f}')
    else:
        lines.append('  ℹ️ 今日已签到，无变化')
    return '\n'.join(lines)


# ---------- 状态/历史持久化 ----------
def persist_state(details: dict) -> None:
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    state = {
        'date': now[:10],
        'run_time': now,
        'accounts_checked': {k: bool(v.get('success')) for k, v in details.items()},
        'details': details,
    }
    try:
        (BASE_DIR / 'daily_checkin_state.json').write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8'
        )
    except Exception as e:  # noqa: BLE001
        print(f'[STATE] 保存失败: {type(e).__name__} {e}')


# ---------- Bark 通知 ----------
def send_bark(env: dict, title: str, content: str) -> bool:
    key = env.get('BARK_KEY', '')
    if not key:
        print('[NOTIFY] BARK_KEY 未配置，跳过 Bark 通知')
        return False
    server = env.get('BARK_SERVER', 'https://api.day.app').rstrip('/')
    data = {
        'device_key': key,
        'title': title,
        'body': content,
        'icon': 'https://anyrouter.top/favicon.ico',
        'group': '签到通知',
    }
    try:
        with httpx.Client(timeout=30.0) as c:
            resp = c.post(f'{server}/push', json=data)
        print(f'[NOTIFY] Bark 推送 status={resp.status_code} title={title}')
        return resp.status_code < 400
    except Exception as e:  # noqa: BLE001
        print(f'[NOTIFY] Bark 推送失败: {type(e).__name__} {e}')
        return False


# ---------- 主流程 ----------
def run_all(conn, accounts, env, now_str, today) -> tuple[dict, int]:
    details = {}
    for acc in accounts:
        provider = (acc.get('provider') or 'anyrouter').strip().lower()
        key = account_key(acc)
        name = display_name(acc)
        cfg = PROVIDERS.get(provider)
        if not cfg:
            model = {'provider': provider, 'name': name, 'success': False, 'skipped': False,
                     'message': f'不支持的 provider: {provider}', 'usage_increase': 0.0}
            details[key] = model
            print(f'  [FAIL] {name} 不支持的 provider')
            continue

        baseline = last_balance(conn, key)
        already = checked_in_today(conn, key, today)

        async def worker():
            async with httpx.AsyncClient(http2=True, timeout=25.0, follow_redirects=True,
                                         headers={'User-Agent': cfg['user_agent']}) as c:
                if provider == 'agentrouter':
                    return await http_login_agentrouter(c, acc, cfg, baseline)
                return await http_checkin_anyrouter(c, acc, cfg, baseline, skip_signin=already)

        model = asyncio.run(worker())
        model['account_key'] = key
        model.setdefault('usage_increase', 0.0)
        model.setdefault('check_in_reward', 0.0)
        model.setdefault('balance_change', 0.0)
        model.setdefault('baseline_balance_change', 0.0)
        model['checkin_time'] = now_str

        # 埋入审计库
        record = {
            'account_key': key, 'name': name, 'provider': provider,
            'success': model.get('success', False), 'skipped': model.get('skipped', False),
            'before_quota': model.get('before_quota'), 'before_used': model.get('before_used'),
            'after_quota': model.get('after_quota'), 'after_used': model.get('after_used'),
            'check_in_reward': model.get('check_in_reward'), 'usage_increase': model.get('usage_increase'),
            'balance_change': model.get('balance_change'),
            'baseline_balance_change': model.get('baseline_balance_change'),
            'checkin_time': now_str,
        }
        insert_history(conn, record)
        details[key] = model
        status = 'OK' if model['success'] else ('SKIP' if model['skipped'] else 'FAIL')
        print(f'  [{status}]')
        print(format_account_block(model, now_str))

    success_count = sum(1 for d in details.values() if d.get('success', False))
    return details, success_count


def build_notification(details: dict, total: int, success: int, now_str) -> tuple[str, str] | None:
    if not details:
        return None
    provider_groups: dict[str, list[dict]] = {}
    for key, detail in details.items():
        pname = detail.get('provider') or 'anyrouter'
        provider_groups.setdefault(pname, []).append(detail)

    all_sections = []
    for provider_name, provider_details in provider_groups.items():
        provider_total = len(provider_details)
        provider_success = sum(1 for d in provider_details if d.get('success', False) and not d.get('skipped', False))
        provider_skipped = sum(1 for d in provider_details if d.get('skipped', False))
        provider_handled = provider_success + provider_skipped
        if provider_success == provider_total:
            ptitle = f'✅ {provider_name}签到全部成功 ({provider_success}/{provider_total})'
        elif provider_skipped == provider_total:
            ptitle = f'ℹ️ {provider_name}今日已签到，跳过 ({provider_total}/{provider_total})'
        elif provider_handled == provider_total:
            ptitle = f'⚠️ {provider_name}签到完成（部分跳过）({provider_success}+{provider_skipped}/{provider_total})'
        elif provider_success > 0:
            ptitle = f'⚠️ {provider_name}签到部分成功 ({provider_success}/{provider_total})'
        else:
            ptitle = f'❌ {provider_name}签到失败 ({provider_success}/{provider_total})'
        items = [format_account_block(d, now_str) for d in provider_details]
        all_sections.append(f'{ptitle}\n\n' + '\n\n'.join(items))

    if success == total:
        title = f'✅ 签到全部成功 ({success}/{total})'
    elif success > 0:
        title = f'⚠️ 签到部分成功 ({success}/{total})'
    else:
        title = f'❌ 签到失败 ({success}/{total})'
    return title, '\n\n'.join(all_sections)


def main() -> int:
    env = load_dotenv()
    accounts_str = env.get('ANYROUTER_ACCOUNTS', '')
    if not accounts_str:
        print('[FAILED] ANYROUTER_ACCOUNTS 环境变量未配置')
        return 1
    try:
        accounts = json.loads(accounts_str)
    except json.JSONDecodeError as e:
        print(f'[FAILED] ANYROUTER_ACCOUNTS JSON 解析失败: {e}')
        return 1
    if not isinstance(accounts, list) or not accounts:
        print('[FAILED] ANYROUTER_ACCOUNTS 必须是非空数组')
        return 1

    print(f'[CONFIG] 共 {len(accounts)} 个账号')
    now = datetime.now()
    now_str = now.strftime('%Y-%m-%d %H:%M:%S')
    today = now.strftime('%Y-%m-%d')

    conn = init_db()
    try:
        details, success = run_all(conn, accounts, env, now_str, today)
    finally:
        conn.close()
    total = len(accounts)
    persist_state(details)

    res = build_notification(details, total, success, now_str)
    if res:
        title, content = res
        print('\n' + title)
        print('')
        print(content)
        send_bark(env, title, content)
    else:
        print('[INFO] 无结果，跳过通知')

    all_handled = success == total and not any(
        d.get('skipped', False) and not d.get('success', False) for d in details.values()
    )
    return 0 if all_handled else 1


if __name__ == '__main__':
    sys.exit(main())