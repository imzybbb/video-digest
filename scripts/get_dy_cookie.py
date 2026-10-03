# -*- coding: utf-8 -*-
"""get_dy_cookie.py — 从本地调试端口导出抖音登录 cookie（供 vd.py 抖音下载用）

用法:
  1) 启动一个带调试端口的浏览器（以 Edge 为例），扫码登录抖音:
     msedge --remote-debugging-port=9222 --user-data-dir=<任意新目录> https://www.douyin.com/
     （Chrome 同理: chrome --remote-debugging-port=9222 --user-data-dir=<目录> https://www.douyin.com/）
  2) 运行: python scripts/get_dy_cookie.py

输出: 仓库根目录 dy_cookie.txt（vd.py 自动读取；也可用环境变量 VD_DY_COOKIE 指定）
端口可用环境变量 VD_DY_PORT 修改（默认 9222）。cookie 过期后重跑本脚本即可。
"""
import asyncio, json, os
import requests
import websockets

PORT = int(os.environ.get("VD_DY_PORT", "9222"))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dy_cookie.txt")


def browser_ws():
    v = requests.get(f"http://127.0.0.1:{PORT}/json/version", timeout=10).json()
    return v.get("webSocketDebuggerUrl")


def page_ws():
    ts = requests.get(f"http://127.0.0.1:{PORT}/json", timeout=10).json()
    pages = [t for t in ts if t.get("type") == "page"]
    for t in pages:
        if "douyin" in (t.get("url") or ""):
            return t["webSocketDebuggerUrl"]
    return pages[0]["webSocketDebuggerUrl"] if pages else None


async def ask(ws_url, method):
    async with websockets.connect(ws_url, max_size=50 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"id": 1, "method": method}))
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("id") == 1:
                return (msg.get("result") or {}).get("cookies") or []


async def main():
    cookies = []
    try:
        cookies = await ask(browser_ws(), "Storage.getCookies")
    except Exception as e:
        print("browser ws 失败:", repr(e)[:100], "→ 尝试页面会话")
    if not cookies and page_ws():
        cookies = await ask(page_ws(), "Network.getAllCookies")
    keep = [c for c in cookies if "douyin" in (c.get("domain") or "")]
    line = "; ".join(f"{c['name']}={c['value']}" for c in keep)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(line)
    names = sorted({c["name"] for c in keep})
    key = [n for n in ["sessionid", "sessionid_ss", "ttwid", "odin_tt", "sid_guard"] if n in names]
    print(f"cookie 条数: {len(keep)} | 总长: {len(line)}")
    print("关键项:", key)
    print("已写入:", os.path.abspath(OUT))


asyncio.run(main())
