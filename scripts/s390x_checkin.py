#!/usr/bin/env python3
"""
s390x/server 纯 HTTP 统一签到脚本。

无需浏览器，仅依赖 httpx + node(解 anyrouter WAF 挑战 JS)。
支持 provider:
  - agentrouter:  邮箱密码登录 ps.air-outer.com，GET /api/user/self 自动签到
  - anyrouter:    解 acw_sc__v2 WAF 挑战 + session cookie 执行 /api/user/sign_in

配置：从 .env 读取（ANYROUTER_ACCOUNTS / BARK_SERVER / BARK_KEY）
版本：0.4.4
"""

import asyncio
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import httpx

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / '.env'

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

# 已签到关键词：签到接口返回这些说明今日已签过
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


# ---------- anyrouter WAF 挑战求解 ----------
def solve_acw(body: str) -> str | None:
    """用 node 执行 WAF 混淆 JS，解出 acw_sc__v2。"""
    m = re.search(r'<script[^>]*>(.*?)</script>', body, re.S)
    if not m:
        return None
    Path('/tmp/acw.js').write_text(m.group(1), encoding='utf-8')
    # node 运行时模拟 document.cookie 环境，执行后打印 cookie
    runner = (
        'var _c="";'
        'var document={};'
        'Object.defineProperty(document,"cookie",{get:function(){return _c;},'
        'set:function(v){if(v&&v.indexOf("acw_sc__v2")!==-1)_c=v;}});'
        'require("/tmp/acw.js");'
        'console.log(_c);'
    )
    script = Path('/tmp/acw_run.js')
    script.write_text(
        runner.replace('require("/tmp/acw.js");', 'eval(require("fs").readFileSync("/tmp/acw.js","utf8"));'),
        encoding='utf-8',
    )
    try:
        p = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=40)
    except Exception as e:  # noqa: BLE001
        print(f'  [WAF-SOLVE-ERROR] node 执行失败: {type(e).__name__} {e}')
        return None
    out = (p.stdout or '') + '\n' + (p.stderr or '')
    m2 = re.search(r'acw_sc__v2=([^;]+)', out)
    if not m2:
        print(f'  [WAF-SOLVE-FAIL] node stdout={p.stdout[:200]!r} stderr={p.stderr[:200]!r}')
        return None
    return m2.group(1).strip()


# ---------- 单账号签到 ----------
async def http_login_agentrouter(client, acc: dict, cfg: dict) -> dict:
    """agentrouter 邮箱密码登录 + user/self 自动签到。"""
    domain = cfg['domain']
    ua = cfg['user_agent']
    model = {'provider': 'agentrouter', 'name': acc.get('name'), 'success': False, 'skipped': False}
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
        'User-Agent': ua,
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
    model['api_user'] = api_user
    model['quota'] = data.get('quota')
    model['used_quota'] = data.get('used_quota')
    model['success'] = bool(isinstance(dd, dict) and dd.get('success'))
    model['message'] = dd.get('message', '') if isinstance(dd, dict) else ''
    return model


async def http_checkin_anyrouter(client, acc: dict, cfg: dict) -> dict:
    """anyrouter 解 WAF + session 签到。"""
    domain = cfg['domain']
    ua = cfg['user_agent']
    model = {'provider': 'anyrouter', 'name': acc.get('name'), 'success': False, 'skipped': False}
    cookies = (acc.get('cookies') or {})
    session = acc.get('session') or cookies.get('session')
    api_user = acc.get('api_user')

    if not session:
        model['message'] = 'anyrouter 需提供 session cookie'
        return model

    api_headers = {
        'User-Agent': ua,
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Origin': domain,
        'Referer': f'{domain}/console',
        'Content-Type': 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
    }
    if api_user:
        api_headers['New-Api-User'] = api_user

    async def show_user(tag):
        try:
            rr = await client.get(f'{domain}/api/user/self', headers=api_headers, timeout=25)
            dd = rr.json()
            data = dd.get('data', {}) if isinstance(dd, dict) else {}
            return data.get('quota'), data.get('used_quota'), (dd.get('message', '') if isinstance(dd, dict) else '')
        except Exception:
            return None, None, ''

    try:
        r = await client.get(f'{domain}/login')
        acw_tc = r.cookies.get('acw_tc')
        cdn_sec_tc = r.cookies.get('cdn_sec_tc')
        acw_sc_v2 = solve_acw(r.text)
        if not (acw_tc and cdn_sec_tc and acw_sc_v2):
            model['message'] = f'WAF 解算不完整 acw_tc={bool(acw_tc)} cdn={bool(cdn_sec_tc)} v2={bool(acw_sc_v2)}'
            return model
        client.cookies.update({
            'session': session,
            'acw_tc': acw_tc,
            'cdn_sec_tc': cdn_sec_tc,
            'acw_sc__v2': acw_sc_v2,
        })
    except Exception as e:  # noqa: BLE001
        model['message'] = f'WAF 获取失败: {type(e).__name__}'
        return model

    before_q, before_u, _ = await show_user('before')
    try:
        sr = await client.post(f'{domain}/api/user/sign_in', headers=api_headers, timeout=25)
        sdd = sr.json()
    except Exception as e:  # noqa: BLE001
        model['message'] = f'签到请求失败: {type(e).__name__}'
        return model

    after_q, after_u, after_msg = await show_user('after')
    msg = sdd.get('message', '') if isinstance(sdd, dict) else ''
    success = bool(isinstance(sdd, dict) and sdd.get('success'))
    msg_low = (msg or '').lower()
    if not success and any(k in msg_low for k in ALREADY_CHECKED_KEYWORDS):
        model['skipped'] = True
        model['success'] = False
        model['message'] = msg
    elif success:
        model['success'] = True
        model['message'] = msg
        model['before_quota'] = before_q
        model['before_used'] = before_u
        model['after_quota'] = after_q
        model['after_used'] = after_u
    else:
        model['message'] = msg or (str(sdd) if isinstance(sdd, dict) else '签到失败')
    model['quota'] = after_q
    model['used_quota'] = after_u
    model['api_user'] = api_user
    return model


# ---------- 账号显示名 ----------
def display_name(acc: dict) -> str:
    name = acc.get('name') or ''
    email = acc.get('email') or ''
    uid = acc.get('api_user') or ''
    return name or email or uid


# ---------- 单账号通知行 ----------
def format_account_line(detail: dict) -> str:
    name = detail.get('name') or detail.get('api_user') or ''
    parts = [detail.get('provider', ''), name]
    if detail.get('api_user'):
        parts.append(str(detail['api_user']))
    label = ' '.join(parts)
    quota = detail.get('after_quota', detail.get('quota'))
    if detail.get('success'):
        if quota is not None:
            return f'✅ {label} 签到成功 (余额 {_fmt_quota(quota)})'
        return f'✅ {label} 签到成功'
    if detail.get('skipped'):
        return f'ℹ️ {label} 今日已签到，跳过'
    return f'❌ {label} 签到失败: {detail.get("message", "")}'


def _fmt_quota(q) -> str:
    try:
        q = float(q)
    except (TypeError, ValueError):
        return str(q)
    return f'${q/10000.0:,.2f}'


# ---------- 状态持久化 ----------
def persist_state(details: dict, total: int, success: int) -> None:
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    state = {
        'date': now[:10],
        'run_time': now,
        'accounts_checked': {k: v.get('success', False) for k, v in details.items()},
        'providers_checked': {},
        'details': details,
    }
    for k, v in details.items():
        state['providers_checked'].setdefault(v.get('provider', 'anyrouter'), True)
    try:
        (BASE_DIR / 'daily_checkin_state.json').write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8'
        )
        print(f'[STATE] saved {len(details)} account(s) -> daily_checkin_state.json')
    except Exception as e:  # noqa: BLE001
        print(f'[STATE] 保存失败: {type(e).__name__} {e}')


# ---------- Bark 通知 ----------
def send_bark(env: dict, title: str, content: str) -> bool:
    key = env.get('BARK_KEY', '')
    if not key:
        print('[NOTIFY] BARK_KEY 未配置，跳过 Bark 通知')
        return False
    server = env.get('BARK_SERVER', 'https://api.day.app').rstrip('/')
    url = f'{server}/push'
    data = {
        'device_key': key,
        'title': title,
        'body': content,
        'icon': 'https://anyrouter.top/favicon.ico',
        'group': '签到通知',
    }
    try:
        with httpx.Client(timeout=30.0) as c:
            resp = c.post(url, json=data)
        print(f'[NOTIFY] Bark 推送 status={resp.status_code} title={title}')
        return resp.status_code < 400
    except Exception as e:  # noqa: BLE001
        print(f'[NOTIFY] Bark 推送失败: {type(e).__name__} {e}')
        return False


# ---------- 主流程 ----------
def run_all(accounts: list[dict]) -> tuple[dict, int]:
    details = {}
    success_count = 0

    for acc in accounts:
        provider = (acc.get('provider') or 'anyrouter').strip().lower()
        cfg = PROVIDERS.get(provider)
        if not cfg:
            model = {'provider': provider, 'name': display_name(acc), 'success': False,
                     'skipped': False, 'message': f'不支持的 provider: {provider}'}
            details[f'{provider}:{display_name(acc)}'] = model
            print(f'  [FAIL] {display_name(acc)} 不支持的 provider')
            continue

        async def worker():
            async with httpx.AsyncClient(http2=True, timeout=25.0, follow_redirects=True,
                                         headers={'User-Agent': cfg['user_agent']}) as c:
                if provider == 'agentrouter':
                    return await http_login_agentrouter(c, acc, cfg)
                return await http_checkin_anyrouter(c, acc, cfg)

        model = asyncio.run(worker())
        key = f'{provider}:{acc.get("email") or acc.get("name") or acc.get("api_user") or display_name(acc)}'
        details[key] = model
        status = 'OK' if model['success'] else ('SKIP' if model['skipped'] else 'FAIL')
        line = format_account_line(model)
        print(f'  [{status}] {line}')

    success_count = sum(1 for d in details.values() if d.get('success', False))
    return details, success_count


def build_notification(details: dict, total: int, success: int) -> tuple[str, str] | None:
    if not details:
        return None
    # 按 provider 分组
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
        items = [format_account_line(d) for d in provider_details]
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
    details, success = run_all(accounts)
    total = len(accounts)
    persist_state(details, total, success)

    res = build_notification(details, total, success)
    if res:
        title, content = res
        print('\n' + title)
        print('')
        print(content)
        send_bark(env, title, content)
    else:
        print('[INFO] 无结果，跳过通知')

    all_handled = success == total and not any(d.get('skipped', False) and not d.get('success', False) for d in details.values())
    return 0 if all_handled else 1


if __name__ == '__main__':
    sys.exit(main())