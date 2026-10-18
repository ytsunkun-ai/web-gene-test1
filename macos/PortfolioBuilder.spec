# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（macOS 出 .app，Windows 出 exe，同一份 spec 通用）。

用法：
    pyinstaller --noconfirm --clean macos/PortfolioBuilder.spec

可用环境变量：
    PYI_TARGET_ARCH   macOS 目标架构（arm64 / x86_64 / universal2），默认跟随当前解释器
    PYI_MIN_MACOS     LSMinimumSystemVersion，默认 11.0

说明：
    * 本程序不需要任何外部资源文件（网页模板全部内嵌在 portfolio_builder.py 中），
      因此 datas / binaries 均为空。
    * 纯 QWidgets 应用，排除用不到的 Qt 子模块与大型科学计算库，体积可减少一半以上。
"""
import os
import sys

HERE = SPECPATH if "SPECPATH" in globals() else os.path.dirname(os.path.abspath(SPEC))
ROOT = os.path.dirname(HERE)
IS_MAC = sys.platform == "darwin"

ICON_MAC = os.path.join(HERE, "build", "AppIcon.icns")
ICON = ICON_MAC if os.path.exists(ICON_MAC) else None

TARGET_ARCH = os.environ.get("PYI_TARGET_ARCH") or None
MIN_MACOS = os.environ.get("PYI_MIN_MACOS", "11.0")

EXCLUDES = [
    "PyQt5.QtWebEngine",
    "PyQt5.QtWebEngineCore",
    "PyQt5.QtWebEngineWidgets",
    "PyQt5.QtWebChannel",
    "PyQt5.QtQml",
    "PyQt5.QtQuick",
    "PyQt5.QtQuickWidgets",
    "PyQt5.QtMultimedia",
    "PyQt5.QtMultimediaWidgets",
    "PyQt5.QtWebSockets",
    "PyQt5.QtBluetooth",
    "PyQt5.QtNfc",
    "PyQt5.QtPositioning",
    "PyQt5.QtLocation",
    "PyQt5.QtSql",
    "PyQt5.QtTest",
    "PyQt5.QtDesigner",
    "PyQt5.QtHelp",
    "PyQt5.QtSensors",
    "PyQt5.QtSerialPort",
    "PyQt5.QtCharts",
    "PyQt5.QtDataVisualization",
    "tkinter",
    "unittest",
    "pydoc_data",
    "test",
    "numpy",
    "scipy",
    "pandas",
    "matplotlib",
    "PIL",
    "setuptools",
    "pip",
]

a = Analysis(
    [os.path.join(ROOT, "portfolio_builder.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PortfolioBuilder",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=TARGET_ARCH,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    getattr(a, "zipfiles", []),
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PortfolioBuilder",
)

if IS_MAC:
    app = BUNDLE(
        coll,
        name="Portfolio Builder.app",
        icon=ICON,
        bundle_identifier="com.ytsun.portfoliobuilder",
        version="1.0.0",
        info_plist={
            "CFBundleName": "Portfolio Builder",
            "CFBundleDisplayName": "作品集生成器",
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion": "1.0.0",
            "CFBundleDevelopmentRegion": "zh_CN",
            "CFBundleInfoDictionaryVersion": "6.0",
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
            "LSApplicationCategoryType": "public.app-category.graphics-design",
            "LSMinimumSystemVersion": MIN_MACOS,
            "NSSupportsAutomaticGraphicsSwitching": True,
            "NSHumanReadableCopyright": "© 2026 · Portfolio Builder",
        },
    )
