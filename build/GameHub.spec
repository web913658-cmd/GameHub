# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

构建命令：  pyinstaller build/GameHub.spec --noconfirm
产物：      dist/GameHub.exe （单文件，免安装）
"""

import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(SPEC)), ".."))

a = Analysis(
    [os.path.join(ROOT, "GameHub.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[
        (os.path.join(ROOT, "web"), "web"),
        (os.path.join(ROOT, "core", "rules"), "core/rules"),
        # 托盘图标。core/tray.py 用 LoadImage + LR_LOADFROMFILE 直接读这个文件，
        # 所以必须随包分发（PyInstaller 只把 exe 的图标嵌进资源，磁盘上没有）。
        (os.path.join(ROOT, "build", "icon.ico"), "build"),
    ],
    hiddenimports=[
        "core", "core.models", "core.vdf", "core.winutil", "core.store",
        "core.artwork", "core.launcher", "core.engines", "core.icon_extract",
        "core.tray", "core.autostart",
        "core.detectors", "core.detectors.steam", "core.detectors.epic",
        "core.detectors.gog", "core.detectors.battlenet", "core.detectors.ea",
        "core.detectors.ubisoft", "core.detectors.riot", "core.detectors.xbox",
        "core.detectors.amazon", "core.detectors.local", "core.detectors.standalone",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter", "unittest", "pydoc", "doctest", "test",
        "numpy", "pandas", "matplotlib", "PIL", "scipy",
    ],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="GameHub",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                    # 本机无 UPX，且压缩后的引导程序易被杀软误报
    upx_exclude=[],
    runtime_tmpdir=None,
    # False = 窗口化程序，双击后不再弹出命令行窗口，看起来才像个正经应用。
    # 诊断信息不受影响：GameHub.py 的 _install_log() 已把输出 tee 到
    # %LOCALAPPDATA%\GameHub\gamehub.log，且无控制台时 sys.stdout 为 None，
    # Tee 内部对 console.write 有 try/except 兜底，不会因此崩溃。
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, "build", "icon.ico"),
)
