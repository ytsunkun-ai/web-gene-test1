#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 macOS 应用图标 AppIcon.icns —— 仅依赖 PyQt5，无需任何图片素材。

用法（在 macOS 上执行）：
    python macos/make_icon.py                     # 生成 macos/build/AppIcon.icns
    python macos/make_icon.py --png preview.png   # 只导出一张 PNG（可在任意系统预览）
    python macos/make_icon.py --out /tmp/x.icns   # 指定输出位置
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

# 渲染图标不需要显示器：CI / 无图形会话下强制离屏渲染，避免 Qt 找不到 platform plugin
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt5.QtWidgets import QApplication  # noqa: E402

import portfolio_builder as pb  # noqa: E402

# iconutil 要求的固定命名（1x / 2x 组合覆盖 16 ~ 1024px）
VARIANTS = (
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 Portfolio Builder 的 .icns 图标")
    parser.add_argument("--png", help="仅导出单张 PNG 到该路径（用于预览）")
    parser.add_argument("--size", type=int, default=1024, help="--png 模式的尺寸，默认 1024")
    parser.add_argument("--out", default=os.path.join(HERE, "build", "AppIcon.icns"), help=".icns 输出路径")
    args = parser.parse_args()

    app = QApplication(sys.argv)

    if args.png:
        png_dir = os.path.dirname(os.path.abspath(args.png))
        if png_dir:
            os.makedirs(png_dir, exist_ok=True)
        ok = pb.make_app_icon(args.size).save(os.path.abspath(args.png), "PNG")
        app.quit()
        print(("已导出 PNG: " if ok else "导出 PNG 失败: ") + os.path.abspath(args.png))
        return 0 if ok else 1

    iconset = os.path.join(HERE, "build", "AppIcon.iconset")
    if os.path.isdir(iconset):
        shutil.rmtree(iconset)
    os.makedirs(iconset, exist_ok=True)

    for name, size in VARIANTS:
        pixmap = pb.make_app_icon(size)
        target = os.path.join(iconset, name)
        if not pixmap.save(target, "PNG"):
            app.quit()
            print("图标写入失败:", target, file=sys.stderr)
            return 1
    app.quit()
    print("iconset 已生成:", iconset)

    if not shutil.which("iconutil"):
        print("提示：当前系统没有 iconutil（非 macOS），已跳过 .icns 合成，iconset PNG 可直接预览。")
        return 0

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    subprocess.run(["iconutil", "-c", "icns", iconset, "-o", os.path.abspath(args.out)], check=True)
    print("icns 已生成:", os.path.abspath(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
