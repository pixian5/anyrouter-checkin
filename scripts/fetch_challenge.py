"""抓取 anyrouter ACW WAF 挑战页，保存原始 body 供分析/解算。"""
import asyncio
import httpx

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


async def main():
    async with httpx.AsyncClient(http2=True, timeout=25.0, follow_redirects=True, headers=HEADERS) as c:
        r = await c.get("https://anyrouter.top/login")
        with open("/tmp/any_challenge.html", "w", encoding="utf-8") as f:
            f.write(r.text)
        cookies = "; ".join(f"{k}={v}" for k, v in r.cookies.items())
        print(f"status={r.status_code} http={r.http_version}")
        print(f"cookies: {cookies}")
        print(f"saved body_len={len(r.text)} to /tmp/any_challenge.html")


if __name__ == "__main__":
    asyncio.run(main())