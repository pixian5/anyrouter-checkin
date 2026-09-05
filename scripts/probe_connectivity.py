"""连通性探测：纯 httpx 访问 ps.air-outer.com 与 anyrouter.top。"""
import asyncio
import httpx

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


async def probe(url: str) -> None:
    print(f"\n========== {url} ==========")
    async with httpx.AsyncClient(http2=True, timeout=25.0, follow_redirects=True, headers=DEFAULT_HEADERS) as c:
        try:
            r = await c.get(url)
        except Exception as e:
            print(f"  [ERROR] {type(e).__name__}: {e}")
            return
        print(f"  status={r.status_code} http={r.http_version} final_url={r.url}")
        print(f"  headers.keys={list(r.headers.keys())[:30]}")
        set_cookie = r.headers.get("set-cookie", "")
        print(f"  set-cookie (first 300): {set_cookie[:300]}")
        ct = r.headers.get("content-type", "")
        body = r.text
        print(f"  content-type={ct}  body_len={len(body)}")
        low = body[:2000]
        snippet = " ".join(low.split())
        print(f"  body_head: {snippet[:600]}")
        # 探测 WAF 特征
        for marker in ("acw_sc__v2", "acw_tc", "滑块", "verify", "captcha", "jsjiami"):
            if marker.lower() in body.lower() or marker in set_cookie.lower():
                print(f"  [WAF-SIGNAL] contains marker: {marker}")


async def main() -> None:
    await probe("https://ps.air-outer.com/login")
    await probe("https://anyrouter.top/login")


if __name__ == "__main__":
    asyncio.run(main())