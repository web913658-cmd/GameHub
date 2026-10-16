"""GameHub —— 一站式 Windows 游戏库启动器。

启动流程：
  1. 在本地回环地址启动 HTTP 服务
  2. 注册系统托盘图标（左键点击 = 打开 / 完全退出）
  3. 打开独立窗口承载界面（除非带 --minimized）
  4. 首次运行自动扫描已安装游戏
  5. 程序常驻，直到从托盘菜单或界面点击「完全退出」

窗口语义（重要）：
  · 关闭窗口 = 缩到系统托盘，**程序不退出**
  · 托盘左键 = 弹出菜单（打开 GameHub / 完全退出）
  · --minimized = 开机自启动用，只驻托盘不弹窗口

直接运行：  python GameHub.py
打包运行：  GameHub.exe
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import server  # noqa: E402

APP_TITLE = "GameHub"
WINDOW_SIZE = (1320, 880)

# Edge/Chrome 应用模式窗口启动宽限期（秒）。
# 应用模式窗口若在启动后极短时间内就退出，说明是"打开失败"
# （例如被既有实例接手、参数不被接受），而不是用户关闭了窗口。
# 此时必须让服务继续跑，否则页面会立刻变成白页。
WINDOW_GRACE = 4.0

EDGE_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""),
                 r"Google\Chrome\Application\chrome.exe"),
]


def _install_log() -> str:
    """把控制台输出同时写进日志文件，返回日志路径。

    PyInstaller 单文件模式下，bootloader 的父子进程结构会让外层
    重定向（> file）丢失子进程的输出，控制台看起来一片空白，
    排错时无从下手。这里由程序自己落一份日志，不依赖外层重定向。
    """
    try:
        log_dir = server.data_root()
        path = os.path.join(log_dir, "gamehub.log")
        fh = open(path, "a", encoding="utf-8", buffering=1)

        class _Tee:
            def __init__(self, console, fh):
                self.console, self.fh = console, fh

            def write(self, s):
                try:
                    self.console.write(s)
                except Exception:
                    pass
                try:
                    self.fh.write(s)
                except Exception:
                    pass

            def flush(self):
                for x in (self.console, self.fh):
                    try:
                        x.flush()
                    except Exception:
                        pass

        sys.stdout = _Tee(sys.stdout, fh)
        sys.stderr = _Tee(sys.stderr, fh)
        return path
    except Exception:
        return ""


def _find_browser() -> str:
    for p in EDGE_PATHS + CHROME_PATHS:
        if p and os.path.isfile(p):
            return p
    return ""


def open_window(url: str):
    """打开承载窗口。

    返回 (方式, 进程句柄)。pywebview 会阻塞直到窗口关闭，句柄为 None；
    其它方式返回承载进程，调用方据此判断窗口何时被关闭。
    """
    # 1) pywebview：真正的原生窗口，最接近"应用"
    try:
        import webview  # type: ignore
        webview.create_window(APP_TITLE, url,
                              width=WINDOW_SIZE[0], height=WINDOW_SIZE[1],
                              min_size=(960, 640), background_color="#f2f2f7")
        webview.start()
        return "pywebview", None
    except ImportError:
        pass
    except Exception as exc:
        print(f"[window] pywebview 启动失败，降级：{exc}")

    # 2) Chromium 应用模式：无地址栏，看起来就是一个独立应用
    browser = _find_browser()
    if browser:
        try:
            # 必须给一个独立的 user-data-dir。
            # 否则当 Edge/Chrome 已经在运行时，新进程会把手递交给既有实例后
            # 立刻退出，我们既无法判断窗口何时关闭，也没法把它当成独立应用。
            profile = os.path.join(server.data_root(), "window-profile")
            os.makedirs(profile, exist_ok=True)
            proc = subprocess.Popen([
                browser,
                f"--app={url}",
                f"--user-data-dir={profile}",
                f"--window-size={WINDOW_SIZE[0]},{WINDOW_SIZE[1]}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-features=Translate,MediaRouter",
                "--disable-background-mode",
                "--no-service-autorun",
                f"--app-name={APP_TITLE}",
            ], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return "browser-app", proc
        except Exception as exc:
            print(f"[window] 应用模式失败，降级：{exc}")

    # 3) 默认浏览器
    webbrowser.open(url)
    return "browser-tab", None


def main() -> int:
    log_path = _install_log()
    minimized = "--minimized" in sys.argv
    port = server.find_free_port(8721)
    httpd = server.create_server(port)
    url = f"http://127.0.0.1:{port}/"

    print(f"""
  ┌─────────────────────────────────────────────┐
  │   GameHub  ·  一站式游戏库启动器            │
  ├─────────────────────────────────────────────┤
  │   地址: {url:<35}│
  │   关闭窗口 = 缩到系统托盘                    │
  │   托盘左键 = 打开 / 完全退出                 │
  └─────────────────────────────────────────────┘
""")
    if log_path:
        print(f"  日志：{log_path}")
    if minimized:
        print("[run] 以 --minimized 启动（开机自启动场景），只驻托盘不弹窗口")

    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()

    # 首次运行（库为空）自动触发扫描
    st = server.STATE
    if st and not st.library.games:
        print("[scan] 首次运行，开始自动扫描已安装游戏…")
        st.start_scan()
    else:
        print(f"[scan] 已加载 {len(st.library.games)} 个游戏，如需刷新请在界面点击「重新扫描」")

    # ---- 窗口管理：可被反复打开 / 关闭 ----
    # 关闭窗口不再退出程序，只是把窗口收起来；托盘菜单可重新打开。
    window_state = {"proc": None, "mode": ""}
    window_lock = threading.Lock()
    # 退出状态。request_exit 可能被并发调用（双击托盘菜单、前端按钮与
    # 托盘菜单同时触发），用它保证「收尾只跑一次」。
    exit_state = {"exiting": False}

    def open_window_now() -> bool:
        """打开（或复用）应用窗口。返回是否成功发起。"""
        with window_lock:
            proc = window_state["proc"]
            if proc is not None and proc.poll() is None:
                print("[window] 窗口已打开，跳过重复打开")
                return True
            mode, proc = open_window(url)
            window_state.update(proc=proc, mode=mode)
            print(f"[window] 已使用 {mode} 打开界面")
            if mode == "browser-app" and proc is not None:
                # 宽限期内退出 = 打开失败，不能当成窗口被关闭
                deadline = time.time() + WINDOW_GRACE
                while time.time() < deadline:
                    if proc.poll() is not None:
                        break
                    time.sleep(0.2)
                if proc.poll() is not None:
                    print("[window] 应用窗口启动失败，服务继续运行")
                    return False
            return True

    def on_window_closed() -> None:
        """窗口进程结束时的回调：只记录，不退出。"""
        with window_lock:
            window_state["proc"] = None
        print("[window] 窗口已关闭，程序继续驻留托盘")

    def request_exit() -> None:
        """完全退出：从托盘菜单或界面调用。

        必须幂等。双击托盘菜单项会产生两次 WM_LBUTTONUP，
        前端「退出程序」按钮与托盘菜单也可能同时触发，
        所以用 exiting 标记保证收尾只跑一次。
        """
        if exit_state["exiting"]:
            return
        exit_state["exiting"] = True
        print("[exit] 收到完全退出请求")
        with window_lock:
            proc = window_state["proc"]
            window_state["proc"] = None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                pass
        try:
            httpd.shutdown()
        except Exception:
            pass
        exit_event.set()

    exit_event = threading.Event()

    # ---- 托盘图标 ----
    tray_mod = None
    try:
        from core import tray as tray_mod
    except Exception as exc:
        print(f"[tray] 托盘模块加载失败：{exc}")

    tray_icon = None
    if tray_mod is not None:
        tray_icon = tray_mod.TrayIcon(
            f"{APP_TITLE} · 我的游戏库",
            tray_mod.icon_path(),
            on_open=open_window_now,
            on_exit=request_exit,
        )
        if not tray_icon.start():
            print("[tray] 托盘图标注册失败（可能图标文件缺失）")
            tray_icon = None
        else:
            print("[tray] 托盘图标已就绪，左键点击可打开 / 完全退出")

    # 让 server 能调用退出（前端「完全退出」按钮）
    server.register_exit_hook(request_exit)

    # ---- 打开窗口（--minimized 时跳过）----
    if minimized:
        pass
    else:
        time.sleep(0.4)
        open_window_now()

    # ---- 窗口关闭监控：只做日志提示，不退出程序 ----
    def watch_window():
        while not exit_event.is_set():
            time.sleep(1.0)
            with window_lock:
                proc = window_state["proc"]
            if proc is not None and proc.poll() is not None:
                with window_lock:
                    if window_state["proc"] is proc:
                        window_state["proc"] = None
                        print("[window] 窗口已关闭，程序继续驻留托盘")

    threading.Thread(target=watch_window, name="GameHub-watch",
                     daemon=True).start()

    # ---- 主循环：程序常驻，直到收到退出请求 ----
    # 窗口被关闭不退出；只有托盘的「完全退出」或界面退出按钮才结束。
    print("[run] 程序已常驻。关闭窗口 = 缩到托盘；托盘左键 = 打开 / 完全退出")
    try:
        exit_event.wait()
    except KeyboardInterrupt:
        print("[exit] 收到中断，正在退出…")
        exit_state["exiting"] = True   # 跳过下面的 request_exit 提示

    # ---- 收尾 ----
    # 正常退出时 request_exit 已跑完（幂等，重复调用直接返回）；
    # Ctrl+C 打断时由这里触发一次。
    request_exit()
    if tray_icon is not None:
        tray_icon.stop()
        print("[tray] 托盘图标已移除")
    try:
        httpd.shutdown()
    except Exception:
        pass
    print("[exit] 已完全退出")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
