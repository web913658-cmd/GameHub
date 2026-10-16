"""GameHub 本地服务端。

基于 Python 标准库 http.server，零第三方依赖。
对外提供静态页面 + JSON API，供前端调用。
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from core import artwork, autostart, launcher, steam_applist
from core.detectors import scan_all
from core.models import PLATFORM_COLORS, PLATFORM_LABELS
from core.store import Library


def resource_root() -> str:
    """静态资源根目录（兼容 PyInstaller 打包）。"""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def data_root() -> str:
    if getattr(sys, "frozen", False):
        base = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "GameHub")
    else:
        base = os.path.join(resource_root(), "data")
    os.makedirs(base, exist_ok=True)
    return base


# 常见图片格式的文件头魔数 -> Content-Type
_IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"RIFF", "image/webp"),          # RIFF....WEBP，需再校验偏移 8
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
)


def _sniff_image(body: bytes) -> str:
    """按文件头判定图片类型；不是已知图片格式则返回空串。"""
    if not body:
        return ""
    for magic, ctype in _IMAGE_MAGIC:
        if body.startswith(magic):
            if magic == b"RIFF" and body[8:12] != b"WEBP":
                continue
            return ctype
    return ""


# --------------------------------------------------------------------------
# 应用状态
# --------------------------------------------------------------------------

class AppState:
    def __init__(self):
        self.data_dir = data_root()
        self.web_dir = os.path.join(resource_root(), "web")
        self.icon_dir = os.path.join(self.data_dir, "icons")
        self.img_cache = os.path.join(self.data_dir, "imgcache")
        os.makedirs(self.icon_dir, exist_ok=True)
        os.makedirs(self.img_cache, exist_ok=True)

        self.library = Library(self.data_dir)
        self.art = artwork.ArtCache(os.path.join(self.data_dir, "artcache.json"))

        # 离线 appid 全量表
        self.applist = steam_applist.get(self.data_dir)
        self.applist_busy = False
        self.applist_msg = ""

        self.scan_lock = threading.Lock()
        self.scanning = False
        self.scan_progress = {"phase": "idle", "current": "", "index": 0, "total": 0,
                              "found": 0, "message": "就绪"}

    # -- 离线全量表 ---------------------------------------------------------
    def _applist_worker(self):
        try:
            ok, msg = self.applist.download(progress=lambda m: setattr(self, "applist_msg", m))
            self.applist_msg = msg
            print(f"[applist] {msg}")
        except Exception as exc:
            self.applist_msg = f"下载失败：{exc}"
        finally:
            self.applist_busy = False

    # -- 扫描 --------------------------------------------------------------
    def start_scan(self, full: bool = True) -> bool:
        with self.scan_lock:
            if self.scanning:
                return False
            self.scanning = True
        t = threading.Thread(target=self._scan_worker, args=(full,), daemon=True)
        t.start()
        return True

    def _set_progress(self, **kw):
        self.scan_progress.update(kw)

    def _scan_worker(self, full: bool):
        try:
            self._set_progress(phase="detect", message="正在识别已安装游戏…",
                               index=0, total=1, current="")

            cfg = self.library.config

            def det_progress(label, i, n):
                self._set_progress(current=label, index=i, total=n,
                                   message=f"检测 {label} …")

            games = scan_all(
                progress=det_progress,
                include_standalone=cfg.get("include_standalone", True) is not False,
                include_local=cfg.get("include_local", True) is not False,
                local_roots=cfg.get("local_roots") or [],
            )
            self._set_progress(phase="art", found=len(games),
                               message=f"识别到 {len(games)} 个游戏，正在获取宣传图…",
                               index=0, total=max(len(games), 1), current="")

            key = self.library.config.get("steamgriddb_key", "")

            def art_progress(name, i, n):
                self._set_progress(current=name, index=i, total=n,
                                   message=f"获取封面 {i+1}/{n}：{name}")

            artwork.resolve(games, self.art, self.icon_dir, sgdb_key=key,
                            progress=art_progress)

            self.library.merge_scan(games)
            self._set_progress(phase="done", message=f"完成，共 {len(games)} 个游戏",
                               current="", index=1, total=1)
        except Exception as exc:
            self._set_progress(phase="error", message=f"扫描出错：{exc}")
        finally:
            self.scanning = False

    # -- 输出 --------------------------------------------------------------
    def games_payload(self):
        out = []
        for g in self.library.games:
            d = g.to_dict()
            d["platform_label"] = PLATFORM_LABELS.get(g.platform, g.platform)
            d["platform_color"] = PLATFORM_COLORS.get(g.platform, "#8e8e93")
            out.append(d)
        return out


STATE: Optional[AppState] = None

# 由 GameHub.py 注册的「完全退出」回调。
# 前端点「完全退出」按钮时调用，让主进程收尾（停服务、删托盘、退出进程）。
_exit_hook = None


def register_exit_hook(fn) -> None:
    """注册完全退出回调。调用后本进程会在回调里结束。"""
    global _exit_hook
    _exit_hook = fn


# --------------------------------------------------------------------------
# HTTP 处理
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "GameHub/1.0"

    def log_message(self, fmt, *args):  # 静音默认日志
        pass

    # -- 工具 --------------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str = "application/json",
              extra: Optional[dict] = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _read_body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0:
                return {}
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def _serve_file(self, path: str):
        if not os.path.isfile(path):
            self._json({"error": "not found"}, 404)
            return
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if path.lower().endswith(".ico"):
            ctype = "image/x-icon"
        elif path.endswith((".html", ".css", ".js", ".svg", ".json")):
            ctype += "; charset=utf-8"
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            self._json({"error": "read error"}, 500)
            return
        # 图片缓存文件名由 URL 截断而来，常常没有扩展名，mimetypes 只能猜出
        # octet-stream。此时按文件头魔数判定真实类型，避免浏览器拿不到
        # 正确的 Content-Type。
        if ctype == "application/octet-stream":
            sniffed = _sniff_image(body)
            if sniffed:
                ctype = sniffed
        self._send(200, body, ctype)

    # -- 路由 --------------------------------------------------------------
    def do_GET(self):
        st = STATE
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        qs = urllib.parse.parse_qs(parsed.query)

        if path == "/api/games":
            self._json({"games": st.games_payload(),
                        "scanning": st.scanning,
                        "last_scan": st.library.last_scan})
            return

        if path == "/api/scan/status":
            self._json({**st.scan_progress, "scanning": st.scanning,
                        "count": len(st.library.games)})
            return

        if path == "/api/config":
            self._json(st.library.config)
            return

        if path == "/api/applist/status":
            self._json({**st.applist.status(),
                        "busy": st.applist_busy,
                        "message": st.applist_msg})
            return

        if path == "/api/platforms":
            self._json({"labels": PLATFORM_LABELS, "colors": PLATFORM_COLORS})
            return

        # 开机自启动状态
        if path == "/api/autostart":
            self._json({"enabled": autostart.is_enabled(),
                        "command": autostart.current_command()})
            return

        # 图标（png 或 ico，取决于 exe 内嵌图标的存储格式）
        m = re.match(r"^/api/icon/([A-Za-z0-9]+)\.(png|ico)$", path)
        if m:
            self._serve_file(os.path.join(st.icon_dir, f"{m.group(1)}.{m.group(2)}"))
            return

        # 图片代理（带磁盘缓存，规避跨域/防盗链，支持离线复用）
        if path == "/api/img":
            u = (qs.get("u") or [""])[0]
            self._proxy_image(u)
            return

        # 静态资源
        if path == "/" or path == "":
            self._serve_file(os.path.join(st.web_dir, "index.html"))
            return
        safe = path.lstrip("/").replace("..", "")
        self._serve_file(os.path.join(st.web_dir, safe))

    def do_POST(self):
        st = STATE
        path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)

        if path == "/api/scan":
            body = self._read_body()
            started = st.start_scan(full=body.get("full", True))
            self._json({"started": started, "scanning": st.scanning})
            return

        m = re.match(r"^/api/launch/([A-Za-z0-9]+)$", path)
        if m:
            g = st.library.get(m.group(1))
            if not g:
                self._json({"ok": False, "message": "游戏不存在"}, 404)
                return
            ok, msg = launcher.launch(g)
            if ok:
                launcher.mark_played(g)
                st.library.save()
            self._json({"ok": ok, "message": msg})
            return

        m = re.match(r"^/api/favorite/([A-Za-z0-9]+)$", path)
        if m:
            g = st.library.get(m.group(1))
            if not g:
                self._json({"ok": False}, 404)
                return
            g.favorite = not g.favorite
            st.library.save()
            self._json({"ok": True, "favorite": g.favorite})
            return

        m = re.match(r"^/api/open-folder/([A-Za-z0-9]+)$", path)
        if m:
            g = st.library.get(m.group(1))
            ok, msg = launcher.open_folder(g.install_dir if g else "")
            self._json({"ok": ok, "message": msg})
            return

        if path == "/api/add":
            body = self._read_body()
            name = (body.get("name") or "").strip()
            target = (body.get("target") or "").strip()
            if not name or not target:
                self._json({"ok": False, "message": "名称与路径必填"}, 400)
                return
            g = st.library.add_manual(name, target)
            self._json({"ok": True, "game": g.to_dict()})
            return

        if path == "/api/config":
            body = self._read_body()
            for k in ("steamgriddb_key", "include_standalone", "include_local",
                      "local_roots", "sort"):
                if k in body:
                    st.library.config[k] = body[k]
            st.library.save_config()
            self._json({"ok": True, "config": st.library.config})
            return

        # 测试 SteamGridDB API Key 是否可用
        if path == "/api/test-sgdb":
            body = self._read_body()
            key = (body.get("key") or st.library.config.get("steamgriddb_key") or "").strip()
            ok, msg = artwork.steamgriddb_test(key)
            self._json({"ok": ok, "message": msg})
            return

        # 下载 / 更新本地 appid 全量表（离线名称匹配用）
        if path == "/api/applist/download":
            if st.applist_busy:
                self._json({"ok": False, "message": "正在下载中，请稍候"})
                return
            st.applist_busy = True
            threading.Thread(target=self._applist_worker, daemon=True).start()
            self._json({"ok": True, "message": "已开始下载"})
            return

        # 开机自启动开关
        if path == "/api/autostart":
            body = self._read_body()
            on = bool(body.get("enabled"))
            ok, msg = autostart.set_enabled(on)
            self._json({"ok": ok, "message": msg,
                        "enabled": autostart.is_enabled()})
            return

        # 完全退出。注意：关闭窗口只是缩到托盘，程序仍在后台跑；
        # 这里才是真正结束进程（停服务、删托盘图标）。
        if path == "/api/exit":
            hook = _exit_hook
            self._json({"ok": True, "message": "正在退出…"})
            if hook is not None:
                # 延迟发出，确保上面的响应已经写完
                threading.Timer(0.3, hook).start()
            return

        self._json({"error": "not found"}, 404)

    def do_DELETE(self):
        st = STATE
        path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        m = re.match(r"^/api/game/([A-Za-z0-9]+)$", path)
        if m:
            ok = st.library.remove(m.group(1))
            self._json({"ok": ok})
            return
        self._json({"error": "not found"}, 404)

    # -- 图片代理 ----------------------------------------------------------
    def _proxy_image(self, url: str):
        st = STATE
        if not url.startswith(("http://", "https://")):
            self._json({"error": "bad url"}, 400)
            return
        key = re.sub(r"[^A-Za-z0-9]", "_", url)[-120:]
        cache_path = os.path.join(st.img_cache, key)
        if os.path.isfile(cache_path) and os.path.getsize(cache_path) > 0:
            self._serve_file(cache_path)
            return
        try:
            data, ctype = artwork.fetch_bytes(url, timeout=15, attempts=2)
            # 上游给的 Content-Type 未必可靠（Epic 的 PNG 有时被标成
            # octet-stream），统一用魔数校正一次。
            if data:
                sniffed = _sniff_image(data)
                if sniffed:
                    ctype = sniffed
            if data:
                try:
                    with open(cache_path, "wb") as f:
                        f.write(data)
                except OSError:
                    pass
            self._send(200, data, ctype)
        except Exception:
            self._send(302, b"", "image/jpeg",
                       {"Location": url})  # 代理失败则让浏览器直连


# --------------------------------------------------------------------------
# 启动
# --------------------------------------------------------------------------

def find_free_port(preferred: int = 8721) -> int:
    import socket
    for port in [preferred] + list(range(preferred + 1, preferred + 30)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def create_server(port: int) -> ThreadingHTTPServer:
    global STATE
    if STATE is None:
        STATE = AppState()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    return httpd
