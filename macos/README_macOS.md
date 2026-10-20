# Portfolio Builder · macOS 打包说明

> 目标产物：`Portfolio Builder.app`（原生 macOS 应用）+ `PortfolioBuilder-macos-<arch>.dmg`（安装镜像）

## 0. 先看这里：为什么不能在 Windows 上直接生成 .app

PyInstaller **不支持跨平台打包**：Windows 上只能产出 `.exe`，要产出 macOS 的 `.app` 必须有一台 **macOS 机器**
（或 macOS CI runner）。这不是配置问题，而是因为 `.app` 内含 Mach-O 可执行文件、Qt5 的 `.framework`、
以及 Apple 的代码签名 / 公证（notarization）工具链（`codesign`、`hdiutil`、`iconutil`、`notarytool`），
它们只存在于 macOS。

因此本目录提供一套「**在 Mac 上一键完成**」的构建工程，三条路线任选：

| 路线 | 适合谁 | 成本 |
|---|---|---|
| A. 本机 Mac 构建 | 手上有 Mac | 0 元，最快 |
| B. GitHub Actions 构建 | 没有 Mac | 公开仓库免费；私有仓库每月 2000 分钟免费额度 |
| C. 云 Mac 租用（MacStadium / MacinCloud 等） | 需要签名公证的公司场景 | 按小时计费 |

---

## 1. 目录内容

```
macos/
├── build_macos.sh          # 一键脚本：venv → 依赖 → 图标 → .app → .dmg（可选签名/公证）
├── PortfolioBuilder.spec   # PyInstaller 配置（排除无用 Qt 模块、写入 Info.plist）
├── make_icon.py            # 用 PyQt5 代码绘制图标并合成 AppIcon.icns（无需素材）
├── entitlements.plist      # 公证时 PyInstaller 引导程序常用豁免项
└── README_macOS.md         # 本文件
../.github/workflows/build-macos.yml   # GitHub Actions 自动构建（x86_64）
```

## 2. 路线 A：本机 Mac 一键构建

```bash
# 1) 准备 Python 3.12（推荐 Homebrew 或 python.org 安装包）
brew install python@3.12

# 2) 进入项目根目录，执行一键脚本
cd /path/to/Web-gene
bash macos/build_macos.sh

# 国内网络建议加镜像：
PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple bash macos/build_macos.sh
```

脚本会依次完成：

1. 检查系统为 macOS，并挑选 Python 3.9–3.12（可用 `PYTHON_BIN=` 指定）
2. 创建 `.venv-mac/` 并安装 `PyQt5` + `PyInstaller`
3. 调用 `make_icon.py` 生成 `macos/build/AppIcon.icns`（16→1024px 全尺寸）
4. 执行 `pyinstaller --noconfirm --clean macos/PortfolioBuilder.spec`
5. 产出 `dist/Portfolio Builder.app`，并打包成 `dist/PortfolioBuilder-macos-<arch>.dmg`

常用参数：

```bash
bash macos/build_macos.sh --no-dmg        # 只要 .app
bash macos/build_macos.sh --skip-venv     # 复用已建好的 .venv-mac（二次构建更快）
PYI_MIN_MACOS=10.15 bash macos/build_macos.sh   # Intel 老系统
```

构建完成后：

```bash
open "dist/Portfolio Builder.app"                       # 直接运行
"dist/Portfolio Builder.app/Contents/MacOS/PortfolioBuilder"   # 命令行看日志（排查崩溃用）
```

## 3. 路线 B：没有 Mac —— 用 GitHub Actions 构建

1. 把项目推到 GitHub（`.github/workflows/build-macos.yml` 已在仓库里）
2. 仓库页面 → **Actions** → 左侧 `build-macos` → **Run workflow**
3. 等待约 3–6 分钟，在 job 的 **Artifacts** 里下载：
   - `PortfolioBuilder-macos-x86_64` → Intel Mac（Apple Silicon 经 Rosetta 2 运行）

工作流会自动：装依赖 → 生成图标 → PyInstaller 打包 → 合成 DMG → 打印 `CFBundleDisplayName` 与 `file` 架构信息便于核对。

## 4. 架构说明（重要）

PyPI 上 PyQt5 的 macOS 轮子是 **按架构分开** 的，没有 universal2：

```
PyQt5-5.15.11-cp38-abi3-macosx_11_0_arm64.whl     ← Apple Silicon，要求 macOS 11+
PyQt5-5.15.11-cp38-abi3-macosx_11_0_x86_64.whl    ← Intel，要求 macOS 11+
```

所以：

- **在 Intel Mac 上构建 → 得到 x86_64 应用**；当前 CI 只构建 x86_64（用户确认够用）
- 想做「一个包通吃」的 universal2，需要 universal2 版 Python（python.org 官方安装包）+ 通用版 Qt，
  目前 PyQt5 的 PyPI 轮子不满足条件
- x86_64 应用在 Apple Silicon 上可通过 Rosetta 2 运行；arm64 应用无法在 Intel 上运行
- 想进一步放宽系统版本：`PYI_MIN_MACOS=10.15`（仅对 Intel 有效，arm64 受轮子限制最低 macOS 11）

## 5. 签名与公证（要分发给别人时必做）

未签名的 app 在他人机器上会被 Gatekeeper 拦截（提示「已损坏」或「无法验证开发者」）。正式分发流程：

```bash
# 一次性：把 Apple ID 凭据存进钥匙串
xcrun notarytool store-credentials "PB-NOTARY" \
  --apple-id "你的 Apple ID" --team-id "TEAMID" --password "App 专用密码"

# 一条命令完成 签名 → 公证 → 装订
SIGN_IDENTITY="Developer ID Application: 你的名字 (TEAMID)" \
NOTARY_PROFILE="PB-NOTARY" \
bash macos/build_macos.sh
```

> 需要 Apple Developer Program 会员（$99/年）才能拿到 `Developer ID Application` 证书。

**没有签名时**，本机或熟人使用可临时解除隔离：

```bash
xattr -dr com.apple.quarantine "/Applications/Portfolio Builder.app"
# 或者：右键点击 App →「打开」→ 在弹窗里再点「打开」
```

## 6. 常见问题

| 现象 | 处理 |
|---|---|
| `未找到可用的 Python 3.9~3.12` | `brew install python@3.12`，或 `PYTHON_BIN=/usr/local/bin/python3.12 bash macos/build_macos.sh` |
| pip 下载超时 / TLS 失败 | 加 `PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple` |
| 启动即闪退 | 用 `dist/.../Contents/MacOS/PortfolioBuilder` 直接跑，看终端报错；多为 Qt 插件缺失或架构不匹配 |
| 双击提示「已损坏」 | 见上：`xattr -dr com.apple.quarantine`，或做签名+公证 |
| 图标没生效 | 确认 `macos/build/AppIcon.icns` 已生成；Finder 图标缓存问题可 `killall Finder` |
| 体积约 90–140 MB | Qt5 框架本身的体积，属正常；spec 已排除 WebEngine/Qml/Multimedia 等无用模块 |
| 想同时出 exe | 在 Windows 上执行 `pyinstaller --noconfirm --clean macos/PortfolioBuilder.spec`（spec 会自动走 Windows 分支） |

## 7. 打包后自检清单

```bash
# 1) 是否为期望架构
file "dist/Portfolio Builder.app/Contents/MacOS/PortfolioBuilder"

# 2) Info.plist 关键字段
/usr/libexec/PlistBuddy -c 'Print :CFBundleDisplayName' "dist/Portfolio Builder.app/Contents/Info.plist"
/usr/libexec/PlistBuddy -c 'Print :LSMinimumSystemVersion' "dist/Portfolio Builder.app/Contents/Info.plist"

# 3) 签名是否有效（有签名时）
codesign --verify --deep --strict --verbose=2 "dist/Portfolio Builder.app"
spctl -a -vvv -t install "dist/Portfolio Builder.app"

# 4) 功能验证：打开 App → 批量创建 3 个项目 → 各选一张封面图 → 设置口令 →
#    一键生成 HTML → 用浏览器打开生成的 html → 输入口令 → 能正常解锁并浏览
```

## 8. 说明：App 内不含任何用户数据

打包产物里只有程序本体（Python 运行时 + Qt5 + 本项目的 `portfolio_builder.py`），
网页模板是内嵌在代码里的字符串，图片、口令、工程数据全部在运行时由用户输入并保存在用户指定位置，
所以这个 `.app` 可以直接分发给同事使用。
