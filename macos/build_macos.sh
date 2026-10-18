#!/usr/bin/env bash
# =============================================================================
#  Portfolio Builder · macOS 一键打包脚本（.app + .dmg）
# -----------------------------------------------------------------------------
#  环境要求：macOS 11+（Intel 或 Apple Silicon），已安装 Xcode 命令行工具
#            （hdiutil / iconutil / codesign 随系统附带，无需额外安装）
#
#  用法：
#      bash macos/build_macos.sh                  # 完整构建（venv → 依赖 → 图标 → .app → .dmg）
#      bash macos/build_macos.sh --no-dmg         # 只产 .app
#      bash macos/build_macos.sh --skip-venv      # 复用已有 .venv-mac
#      PYTHON_BIN=/usr/local/bin/python3.12 bash macos/build_macos.sh
#      PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple bash macos/build_macos.sh
#      SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" bash macos/build_macos.sh
#      PYI_MIN_MACOS=10.15 bash macos/build_macos.sh          # Intel 老系统
#
#  产物：dist/Portfolio Builder.app
#        dist/PortfolioBuilder-macos-<arch>.dmg
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------- 基础变量
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

VENV="$ROOT/.venv-mac"
APP_NAME="Portfolio Builder"
APP_PATH="dist/$APP_NAME.app"
ARCH="$(uname -m)"
DMG_PATH="dist/PortfolioBuilder-macos-$ARCH.dmg"
ENTITLEMENTS="$SCRIPT_DIR/entitlements.plist"

MAKE_DMG=1
SKIP_VENV=0
for arg in "$@"; do
  case "$arg" in
    --no-dmg)     MAKE_DMG=0 ;;
    --skip-venv)  SKIP_VENV=1 ;;
    -h|--help)    sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "未知参数: $arg"; exit 1 ;;
  esac
done

c_info() { printf '\033[1;36m▸ %s\033[0m\n' "$*"; }
c_ok()   { printf '\033[1;32m✔ %s\033[0m\n' "$*"; }
c_warn() { printf '\033[1;33m! %s\033[0m\n' "$*"; }
c_err()  { printf '\033[1;31m✘ %s\033[0m\n' "$*" >&2; }

# ---------------------------------------------------------------- 0. 平台检查
if [ "$(uname -s)" != "Darwin" ]; then
  c_err "本脚本只能在 macOS 上运行（当前系统：$(uname -s)）。"
  c_err "PyInstaller 不支持跨平台打包，Windows/Linux 无法产出 .app。"
  exit 1
fi
c_info "系统：$(sw_vers -productVersion 2>/dev/null || echo '?') / 架构：$ARCH"

# ---------------------------------------------------------------- 1. 选择解释器
pick_python() {
  if [ -n "${PYTHON_BIN:-}" ]; then echo "$PYTHON_BIN"; return; fi
  for candidate in python3.12 python3.11 python3.10 python3.9 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      echo "$candidate"; return
    fi
  done
  if [ -x "$HOME/.local/bin/python3.12" ]; then echo "$HOME/.local/bin/python3.12"; return; fi
  echo ""
}

if [ "$SKIP_VENV" = "0" ]; then
  PY="$(pick_python)"
  if [ -z "$PY" ]; then
    c_err "未找到可用的 Python 3.9~3.12。"
    c_err "推荐：brew install python@3.12    或    https://www.python.org/downloads/macos/"
    exit 1
  fi
  PYVER="$("$PY" -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
  case "$PYVER" in
    3.9|3.10|3.11|3.12) ;;
    *) c_warn "当前解释器 Python $PYVER 可能没有 PyQt5 预编译轮子，建议改用 3.12。" ;;
  esac
  c_info "使用解释器：$PY (Python $PYVER)"

  if [ ! -d "$VENV" ]; then
    c_info "创建虚拟环境：$VENV"
    "$PY" -m venv "$VENV"
  fi
  VENV_PY="$VENV/bin/python"

  c_info "安装依赖（PyQt5 + PyInstaller）…"
  "$VENV_PY" -m pip install --upgrade pip >/dev/null
  # 不用数组：macOS 自带 bash 3.2，空数组展开在 set -u 下会报 unbound variable
  if [ -n "${PIP_INDEX_URL:-}" ]; then
    "$VENV_PY" -m pip install --index-url "$PIP_INDEX_URL" "PyQt5>=5.15.9" "pyinstaller>=6.0"
  else
    "$VENV_PY" -m pip install "PyQt5>=5.15.9" "pyinstaller>=6.0"
  fi
else
  VENV_PY="$VENV/bin/python"
  if [ ! -x "$VENV_PY" ]; then
    c_err "--skip-venv 指定了复用，但 $VENV 不存在。"
    exit 1
  fi
  c_info "复用已有虚拟环境：$VENV"
fi

"$VENV_PY" - <<'PY'
import sys
from PyQt5.QtCore import QT_VERSION_STR
import PyInstaller
print("   Python %s | PyQt5(Qt %s) | PyInstaller %s" % (sys.version.split()[0], QT_VERSION_STR, PyInstaller.__version__))
PY

# ---------------------------------------------------------------- 2. 生成图标
c_info "生成应用图标 AppIcon.icns …"
"$VENV_PY" "$SCRIPT_DIR/make_icon.py"
[ -f "$SCRIPT_DIR/build/AppIcon.icns" ] || c_warn "未生成 .icns，将以默认图标打包"

# ---------------------------------------------------------------- 3. 打包
c_info "PyInstaller 打包中（首次约 1~3 分钟）…"
rm -rf build/PortfolioBuilder dist/"$APP_NAME" "dist/PortfolioBuilder"
"$VENV_PY" -m PyInstaller --noconfirm --clean "$SCRIPT_DIR/PortfolioBuilder.spec"

if [ ! -d "$APP_PATH" ]; then
  c_err "打包失败：未找到 $APP_PATH"
  exit 1
fi
DISPLAY_NAME="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleDisplayName' "$APP_PATH/Contents/Info.plist" 2>/dev/null || echo '')"
SIZE_MB="$(du -sm "$APP_PATH" | awk '{print $1}')"
c_ok "已生成 $APP_PATH（${SIZE_MB} MB，CFBundleDisplayName=${DISPLAY_NAME:-未设置}）"

# ---------------------------------------------------------------- 4. 签名（可选）
if [ -n "${SIGN_IDENTITY:-}" ]; then
  c_info "使用证书签名：$SIGN_IDENTITY"
  SIGN_ARGS=(--force --deep --timestamp --options runtime --sign "$SIGN_IDENTITY")
  if [ -f "$ENTITLEMENTS" ]; then
    SIGN_ARGS+=(--entitlements "$ENTITLEMENTS")
  fi
  /usr/bin/codesign "${SIGN_ARGS[@]}" "$APP_PATH"
  /usr/bin/codesign --verify --deep --strict --verbose=2 "$APP_PATH" && c_ok "签名校验通过"
  if [ -n "${NOTARY_PROFILE:-}" ]; then
    c_info "提交 Apple 公证（notarytool profile: $NOTARY_PROFILE）…"
    ZIP_TMP="dist/PortfolioBuilder-notarize.zip"
    /usr/bin/ditto -c -k --keepParent "$APP_PATH" "$ZIP_TMP"
    xcrun notarytool submit "$ZIP_TMP" --keychain-profile "$NOTARY_PROFILE" --wait
    xcrun stapler staple "$APP_PATH" && c_ok "公证票据已装订（可直接分发给他人）"
    rm -f "$ZIP_TMP"
  else
    c_warn "未设置 NOTARY_PROFILE，跳过公证。他人首次打开需右键 →「打开」，或执行："
    c_warn "   xattr -dr com.apple.quarantine \"/Applications/$APP_NAME.app\""
  fi
else
  c_warn "未设置 SIGN_IDENTITY，产物为未签名版本（本机可运行，跨机分发会被 Gatekeeper 拦）"
  c_warn "本机若提示损坏/无法验证，执行：xattr -dr com.apple.quarantine \"$APP_PATH\""
fi

# ---------------------------------------------------------------- 5. 打包 DMG
if [ "$MAKE_DMG" = "1" ]; then
  c_info "制作 DMG …"
  rm -f "$DMG_PATH"
  STAGE="$(mktemp -d)"
  cp -R "$APP_PATH" "$STAGE/"
  ln -s /Applications "$STAGE/Applications"
  hdiutil create -volname "$APP_NAME" -srcfolder "$STAGE" -ov -format UDZO "$DMG_PATH" >/dev/null
  rm -rf "$STAGE"
  if [ -n "${SIGN_IDENTITY:-}" ]; then
    /usr/bin/codesign --force --sign "$SIGN_IDENTITY" "$DMG_PATH" >/dev/null 2>&1 || true
  fi
  c_ok "已生成 $DMG_PATH（$(du -sm "$DMG_PATH" | awk '{print $1}') MB）"
fi

# ---------------------------------------------------------------- 6. 完成
echo
c_ok "构建完成 🎉"
echo "  应用：$ROOT/$APP_PATH"
if [ "$MAKE_DMG" = "1" ]; then
  echo "  安装包：$ROOT/$DMG_PATH"
fi
echo
echo "  本机试运行： open \"$ROOT/$APP_PATH\""
echo "  命令行调试： \"$APP_PATH/Contents/MacOS/PortfolioBuilder\""
echo
