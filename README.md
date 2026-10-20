# Portfolio Builder · 作品集网页生成器

单文件 PyQt5 桌面工具：主从式界面批量管理项目图片与文案，**一键生成**带防盗图 / 防截屏 / 口令解锁的
暗色高奢风格单文件网页（数据 Base64 加密内嵌，生成的 HTML 无任何外部依赖）。

## 功能一览

| 模块 | 说明 |
|---|---|
| 项目管理 | 左侧列表增删、上移下移、鼠标拖拽排序；顶部批量创建 N 个项目 |
| 内容编辑 | 封面标题 / 文案 / 单选封面图（带预览）；二级详情文案 / 多选详情图（可拖拽排序） |
| 口令加密 | 查看口令必填，SHA-256 校验 + XOR 链式密钥流加密，Python 端与前端 JS 完全对齐（纯 JS SHA-256，`file://` 可用） |
| 页面权限 | 全局设置三档：`不允许复制 + 截图高斯模糊` / `不允许复制（截图不模糊）` / `允许复制（关闭截屏防护）` |
| 异形比例 | 封面流与详情页自动识别长图 / 超宽图（比例偏差 > 1.14 即 contain 完整显示 + 模糊压暗底板），长图不再被裁切；详情页点击可看完整大图 |
| 防盗图 | 全程 Canvas 绘制，无 `<img>` 标签；禁用右键 / 拖拽 / 选中 / 长按 + 透明保护罩 |
| 防截屏 | 严格模式下 PrintScreen、Win+Shift+S、Cmd+Shift+3/4/5、F12、Ctrl/Cmd+Shift+I、Ctrl/Cmd+S/P 触发即 0ms 模糊清屏，点击恢复按钮才解除 |
| 锁定层 | 未解锁前 DOM 不渲染任何项目结构与图片，仅显示毛玻璃口令层 |
| 网页风格 | `#0a0a0a` / `#f5f5f5` 深色高奢（白色点缀）；左上角汉堡 + PROJECTS 抽屉导航；封面流点击进入二级详情 |

## 运行（源码方式）

```bash
# Windows：直接双击 run.bat，或
.venv\Scripts\python.exe portfolio_builder.py

# macOS / Linux
python3.12 -m venv .venv
.venv/bin/pip install PyQt5
.venv/bin/python portfolio_builder.py
```

## 打包为可执行程序

| 目标平台 | 命令 | 产物 |
|---|---|---|
| macOS（本机） | `bash macos/build_macos.sh` | `dist/Portfolio Builder.app` + `PortfolioBuilder-macos-<arch>.dmg` |
| macOS（云端，无需 Mac） | 仓库 Actions → **build-macos** → Run workflow | 双架构 `.dmg`（arm64 / x86_64） |
| Windows | `build_windows.bat` | `dist\PortfolioBuilder\PortfolioBuilder.exe` |

macOS 打包细节（签名、公证、Gatekeeper 处理）见 [macos/README_macOS.md](macos/README_macOS.md)。

> PyInstaller 不支持跨平台打包：Windows 上无法产出 `.app`，反之亦然，因此 macOS 版本通过 GitHub Actions 在 macOS 机器上构建。

## 目录结构

```
portfolio_builder.py      主程序（GUI + 加密 + 内嵌网页模板，单文件零依赖）
run.bat                   Windows 一键启动
build_windows.bat         Windows 一键打包 exe
macos/build_macos.sh      macOS 一键打包 .app / .dmg
macos/PortfolioBuilder.spec   PyInstaller 配置（macOS / Windows 通用）
macos/make_icon.py        代码绘制并合成 AppIcon.icns（无需素材）
macos/entitlements.plist  公证用豁免项
.github/workflows/build-macos.yml  云端构建工作流
```

## 依赖

- Python 3.9 ~ 3.12
- PyQt5（运行必需）；PyInstaller（仅打包时需要）

## 说明

生成的网页不含任何账号 / 服务器交互，口令只用于解密内嵌数据；程序本身不联网，
工程数据（图片、文案）全部在运行时由用户输入，不会写入仓库或安装包。
