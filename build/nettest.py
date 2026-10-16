"""网络可达性与速度实测。

对 GameHub 依赖的每个数据源分别测试「走系统代理」与「直连」两种模式，
统计成功率与延迟，用来决定默认走哪条路。
"""

import json
import os
import socket
import ssl
import time
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) GameHub/1.0"

TARGETS = [
    ("Steam 商店搜索",   "https://store.steampowered.com/api/storesearch/?term=Monument%20Valley&cc=us&l=en"),
    ("Steam 详情接口",   "https://store.steampowered.com/api/appdetails?appids=1927720&filters=basic"),
    ("Steam 图片 CDN",   "https://cdn.cloudflare.steamstatic.com/steam/apps/1927720/library_600x900.jpg"),
    ("Steam 备用图片",   "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/1927720/header.jpg"),
    ("Steam 全量应用表", "https://api.steampowered.com/ISteamApps/GetAppList/v2/"),
    ("Steam 社区搜索",   "https://steamcommunity.com/actions/SearchApps/Monument%20Valley"),
    ("SteamGridDB API",  "https://www.steamgriddb.com/api/v2/search/autocomplete/Monument%20Valley"),
    ("SteamGridDB 图床", "https://cdn2.steamgriddb.com/grid/1.png"),
]

ROUNDS = 3
TIMEOUT = 8


def _opener(use_proxy: bool):
    if use_proxy:
        return urllib.request.build_opener()
    # 显式绕过代理
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def probe(opener, url: str, timeout: int = TIMEOUT):
    """返回 (成功?, 状态码, 耗时, 字节数, 备注)"""
    t = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with opener.open(req, timeout=timeout) as r:
            data = r.read(4096)
        return True, r.status, time.time() - t, len(data), ""
    except urllib.error.HTTPError as e:
        # 有响应就算通（401/404 说明域名可达）
        return True, e.code, time.time() - t, 0, "有响应"
    except Exception as e:
        return False, 0, time.time() - t, 0, type(e).__name__


def show_proxy_env():
    keys = ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "no_proxy", "NO_PROXY"]
    found = {k: os.environ[k] for k in keys if os.environ.get(k)}
    print("当前代理环境变量：")
    if found:
        for k, v in found.items():
            print(f"  {k} = {v}")
    else:
        print("  （无）")
    try:
        print(f"  urllib.getproxies() = {urllib.request.getproxies()}")
    except Exception as e:
        print(f"  getproxies 失败: {e}")
    print()


def main():
    show_proxy_env()

    for mode, use_proxy in (("走系统代理", True), ("直连（绕过代理）", False)):
        opener = _opener(use_proxy)
        print(f"══ {mode} ══")
        print(f"  {'数据源':<18} {'成功':<6} {'平均':<8} {'最快':<8} {'最慢':<8} 状态")
        print("  " + "-" * 66)
        for name, url in TARGETS:
            results = [probe(opener, url) for _ in range(ROUNDS)]
            oks = [r for r in results if r[0]]
            if oks:
                times = [r[2] for r in oks]
                codes = ",".join(str(r[1]) for r in oks)
                print(f"  {name:<18} {len(oks)}/{ROUNDS:<4} "
                      f"{sum(times)/len(times):>6.2f}s  {min(times):>6.2f}s  {max(times):>6.2f}s  HTTP {codes}")
            else:
                errs = ",".join(sorted({r[4] for r in results}))
                print(f"  {name:<18} 0/{ROUNDS:<4} {'—':>7} {'—':>7} {'—':>7}  {errs}")
        print()


if __name__ == "__main__":
    main()
