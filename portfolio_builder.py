#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Portfolio Builder · 多项目作品集网页生成器（PyQt5 桌面端）
=========================================================

功能概览
--------
1) Master-Detail 桌面界面：左侧管理多个项目（批量创建 / 增删 / 上移下移 / 鼠标拖拽排序），
   右侧编辑当前项目的一级封面（标题、文案、单张封面图）与二级详情（详情文案、多张详情图，可拖拽排序）。
2) 全局「查看口令（分享码）」：口令经 SHA-256 校验；项目 JSON（含图片 Base64）经
   「口令派生密钥 + SHA-256 链式密钥流 + XOR」混淆后再 Base64，写入单文件 HTML。
3) 生成高奢暗色风格单文件网页：封面流 + 详情沉浸画廊 + 左上角抽屉导航 + 分享码解锁层 +
   Canvas 渲染防盗图 + 全方位防截屏高斯模糊锁定。

依赖：Python 3.7+ / PyQt5（仅此一项第三方库，其余全部为标准库）
运行：python portfolio_builder.py
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import operator
import os
import re
import sys
import uuid

try:
    from PyQt5.QtCore import Qt, QSize, QTimer, QUrl, pyqtSignal
    from PyQt5.QtGui import QColor, QDesktopServices, QFont, QIcon, QPixmap
    from PyQt5.QtWidgets import (
        QAbstractItemView,
        QApplication,
        QComboBox,
        QFileDialog,
        QFormLayout,
        QFrame,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMessageBox,
        QProgressDialog,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QSpinBox,
        QSplitter,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )
except ImportError:  # pragma: no cover
    sys.stderr.write("未检测到 PyQt5，请先执行： pip install PyQt5\n")
    raise


APP_NAME = "Portfolio Builder · 作品集网页生成器"
APP_VERSION = "1.2.1"

SUPPORTED_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")
MIME_MAP = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
}
IMAGE_FILTER = "图片文件 (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;所有文件 (*.*)"


# ============================================================================
# 1. 加密 / 混淆核心（Python 端与网页 JS 端算法严格对齐）
# ============================================================================
#
#   key        = SHA256( password_utf8 )                       -> 32 bytes
#   block[0]   = SHA256( key )                                 -> 32 bytes
#   block[i]   = SHA256( block[i-1] )                          -> 32 bytes
#   keystream  = block[0] + block[1] + ...                     （可无限延长）
#   ciphertext = plaintext XOR keystream
#   payload    = BASE64( ciphertext )
#
#   JS 端：先校验 SHA256(输入口令) 是否等于写入 HTML 的哈希，通过后再用完全相同的
#          密钥流（链式 SHA-256）异或解密，最后按 UTF-8 还原中文，绝无乱码。
# ============================================================================


def sha256_bytes(data: bytes) -> bytes:
    """返回 32 字节 SHA-256 摘要。"""
    return hashlib.sha256(data).digest()


def password_hash_hex(password: str) -> str:
    """口令校验哈希（写入 HTML，供前端比对）。"""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def derive_keystream(password: str, length: int) -> bytes:
    """基于口令派生任意长度密钥流：SHA256 链式迭代。"""
    if length <= 0:
        return b""
    state = sha256_bytes(password.encode("utf-8"))
    chunks = []
    produced = 0
    while produced < length:
        state = sha256_bytes(state)
        chunks.append(state)
        produced += 32
    return b"".join(chunks)[:length]


def xor_bytes(left: bytes, right: bytes) -> bytes:
    """恒定长度异或；大数据走 int 位运算（C 级速度），小数据走 map。"""
    size = min(len(left), len(right))
    if size == 0:
        return b""
    left = left[:size]
    right = right[:size]
    if size >= 262144:
        try:
            merged = int.from_bytes(left, "big") ^ int.from_bytes(right, "big")
            return merged.to_bytes(size, "big")
        except Exception:
            pass
    return bytes(map(operator.xor, left, right))


def encrypt_payload(raw: bytes, password: str) -> str:
    """明文 JSON(UTF-8) -> XOR 混淆 -> Base64 文本。"""
    stream = derive_keystream(password, len(raw))
    return base64.b64encode(xor_bytes(raw, stream)).decode("ascii")


def read_image_payload(path: str) -> dict:
    """读取本地图片 -> {"mime": ..., "data": base64}。"""
    with open(path, "rb") as handle:
        raw = handle.read()
    ext = os.path.splitext(path)[1].lower()
    mime = MIME_MAP.get(ext, "image/jpeg")
    return {"mime": mime, "data": base64.b64encode(raw).decode("ascii")}


def describe_aspect_ratio(width: int, height: int) -> str:
    """返回图片比例的中文描述，便于一眼识别长图 / 异形图。"""
    if width <= 0 or height <= 0:
        return "比例未知"
    ratio = width / float(height)
    if ratio >= 2.6:
        return "超宽横幅 %.1f:1" % ratio
    if ratio >= 1.15:
        return "横图 %.2f:1" % ratio
    if ratio >= 0.87:
        return "方图 ≈1:1"
    if ratio >= 0.45:
        return "竖图 1:%.2f" % (1.0 / ratio)
    return "长图 1:%.1f" % (1.0 / ratio)


def fill_template(template: str, mapping: dict) -> str:
    """单次正则替换 __PLACEHOLDER__，避免多次 replace 相互污染。"""
    pattern = re.compile(r"__([A-Z0-9_]+)__")

    def _sub(match: "re.Match[str]") -> str:
        return mapping.get(match.group(1), match.group(0))

    return pattern.sub(_sub, template)


def js_literal(value: str) -> str:
    """把 Python 字符串安全地嵌入 <script>（防 </script> 逃逸）。"""
    dumped = json.dumps(value, ensure_ascii=False)
    return (
        dumped.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def html_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ============================================================================
# 2. 数据模型 & 列表排序工具
# ============================================================================


def new_project(index: int = 1) -> dict:
    """创建单个项目的数据结构。

    注意：uid 是唯一标识。QListWidgetItem 里只存 uid 字符串——
    因为 PyQt5 会把 setData(Qt.UserRole, dict) 转成 QVariantMap 快照（拷贝），
    直接存 dict 会导致「编辑后标签不同步」和「排序后编辑内容丢失」。
    """
    return {
        "uid": uuid.uuid4().hex[:16],
        "title": "项目 %02d" % index,
        "meta": "",
        "cover_intro": "",
        "cover_image": "",
        "detail_intro": "",
        "detail_images": [],
    }


def move_list_rows(view: QListWidget, delta: int) -> bool:
    """在 QListWidget 内整体移动选中行（支持多选块），delta 为 -1 / +1。"""
    rows = sorted(index.row() for index in view.selectedIndexes())
    if not rows:
        return False
    total = view.count()
    if delta < 0 and rows[0] == 0:
        return False
    if delta > 0 and rows[-1] == total - 1:
        return False

    items = [view.item(i) for i in range(total)]
    for _ in range(total):
        view.takeItem(0)

    if delta < 0:
        order = list(range(0, rows[0] - 1)) + rows + [rows[0] - 1] + list(range(rows[-1] + 1, total))
    else:
        order = list(range(0, rows[0])) + [rows[-1] + 1] + rows + list(range(rows[-1] + 2, total))

    for index in order:
        view.addItem(items[index])

    start = order.index(rows[0])
    for offset in range(len(rows)):
        view.item(start + offset).setSelected(True)
    view.setCurrentRow(start)
    return True


class ProjectListWidget(QListWidget):
    """支持鼠标拖拽排序的项目列表，拖放后发出 reordered 信号。"""

    reordered = pyqtSignal()

    def dropEvent(self, event):  # noqa: N802 (Qt 命名)
        before = [self.item(i).data(Qt.UserRole) for i in range(self.count())]
        super().dropEvent(event)
        after = [self.item(i).data(Qt.UserRole) for i in range(self.count())]
        if before != after:
            self.reordered.emit()


# ============================================================================
# 3. 右侧：当前项目详情编辑器
# ============================================================================


class ProjectEditor(QWidget):
    """编辑单个项目的封面（一级）与详情（二级）内容。"""

    changed = pyqtSignal()

    PREVIEW_W = 300
    PREVIEW_H = 186

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project = None
        self._loading = False
        self._build_ui()

    # ---------------- UI ----------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        root.addWidget(self._build_cover_group())
        root.addWidget(self._build_detail_group(), 1)

    def _build_cover_group(self) -> QGroupBox:
        group = QGroupBox("一级界面 · 项目封面")
        layout = QVBoxLayout(group)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        form.setSpacing(9)

        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("项目名称 / 封面标题（同时用于左侧导航目录）")

        self.meta_edit = QLineEdit()
        self.meta_edit.setPlaceholderText("可选 · 例如 2026 / BRAND IDENTITY")

        self.cover_intro = QTextEdit()
        self.cover_intro.setPlaceholderText("封面介绍文字：出现在首页封面旁，用于一两句点睛描述…")
        self.cover_intro.setFixedHeight(96)

        form.addRow("项目名称", self.title_edit)
        form.addRow("副标签", self.meta_edit)
        form.addRow("封面文案", self.cover_intro)
        layout.addLayout(form)

        picker = QHBoxLayout()
        picker.setSpacing(14)

        self.cover_preview = QLabel("未选择封面图\n\n（任意比例均可，长图会完整显示）")
        self.cover_preview.setObjectName("coverPreview")
        self.cover_preview.setFixedSize(self.PREVIEW_W, self.PREVIEW_H)
        self.cover_preview.setAlignment(Qt.AlignCenter)
        self.cover_preview.setWordWrap(True)
        picker.addWidget(self.cover_preview, 0, Qt.AlignTop)

        right = QVBoxLayout()
        right.setSpacing(8)
        self.cover_path = QLabel("尚未选择封面图")
        self.cover_path.setObjectName("pathLabel")
        self.cover_path.setWordWrap(True)
        self.cover_path.setMinimumHeight(46)
        right.addWidget(self.cover_path)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.btn_cover_pick = QPushButton("选择封面图…")
        self.btn_cover_pick.setObjectName("primaryGhost")
        self.btn_cover_clear = QPushButton("清除封面")
        buttons.addWidget(self.btn_cover_pick)
        buttons.addWidget(self.btn_cover_clear)
        buttons.addStretch(1)
        right.addLayout(buttons)
        right.addStretch(1)
        picker.addLayout(right, 1)
        layout.addLayout(picker)

        self.btn_cover_pick.clicked.connect(self._pick_cover)
        self.btn_cover_clear.clicked.connect(self._clear_cover)
        self.title_edit.textChanged.connect(self._on_text_changed)
        self.meta_edit.textChanged.connect(self._on_text_changed)
        self.cover_intro.textChanged.connect(self._on_text_changed)
        return group

    def _build_detail_group(self) -> QGroupBox:
        group = QGroupBox("二级界面 · 项目详情")
        layout = QVBoxLayout(group)
        layout.setSpacing(10)

        label = QLabel("二级详情介绍文字：进入项目详情页后的深度阐述（支持多行中文）。")
        label.setObjectName("hintLabel")
        layout.addWidget(label)

        self.detail_intro = QTextEdit()
        self.detail_intro.setPlaceholderText("在这里写下项目的背景、思路、材质与成果…")
        self.detail_intro.setFixedHeight(126)
        layout.addWidget(self.detail_intro)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.btn_add_images = QPushButton("添加详情图…（可多选）")
        self.btn_add_images.setObjectName("primaryGhost")
        self.btn_img_up = QPushButton("上移")
        self.btn_img_down = QPushButton("下移")
        self.btn_img_del = QPushButton("删除图片")
        self.btn_img_clear = QPushButton("清空")
        for widget in (
            self.btn_add_images,
            self.btn_img_up,
            self.btn_img_down,
            self.btn_img_del,
            self.btn_img_clear,
        ):
            bar.addWidget(widget)
        bar.addStretch(1)
        self.img_count = QLabel("0 张")
        self.img_count.setObjectName("hintLabel")
        bar.addWidget(self.img_count)
        layout.addLayout(bar)

        self.image_list = QListWidget()
        self.image_list.setObjectName("imageList")
        self.image_list.setIconSize(QSize(104, 68))
        self.image_list.setSpacing(2)
        self.image_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.image_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.image_list.setDefaultDropAction(Qt.MoveAction)
        self.image_list.setDropIndicatorShown(True)
        self.image_list.setMinimumHeight(240)
        self.image_list.setToolTip("提示：可直接用鼠标拖拽调整详情图顺序")
        self.image_list.model().rowsMoved.connect(self._on_images_moved)
        layout.addWidget(self.image_list, 1)

        self.btn_add_images.clicked.connect(self._pick_detail_images)
        self.btn_img_up.clicked.connect(lambda: self._move_images(-1))
        self.btn_img_down.clicked.connect(lambda: self._move_images(1))
        self.btn_img_del.clicked.connect(self._delete_images)
        self.btn_img_clear.clicked.connect(self._clear_images)
        self.detail_intro.textChanged.connect(self._on_text_changed)
        return group

    # ---------------- 数据绑定 ----------------
    def load_project(self, project: dict) -> None:
        self._loading = True
        self.project = project
        self.title_edit.setText(project.get("title", ""))
        self.meta_edit.setText(project.get("meta", ""))
        self.cover_intro.setPlainText(project.get("cover_intro", ""))
        self.detail_intro.setPlainText(project.get("detail_intro", ""))
        self._refresh_cover_preview()
        self._rebuild_image_list()
        self._loading = False

    def set_enabled_state(self, enabled: bool) -> None:
        for child in self.findChildren(QWidget):
            if child is not self:
                child.setEnabled(enabled)

    def _on_text_changed(self) -> None:
        if self._loading or self.project is None:
            return
        self.project["title"] = self.title_edit.text().strip()
        self.project["meta"] = self.meta_edit.text().strip()
        self.project["cover_intro"] = self.cover_intro.toPlainText()
        self.project["detail_intro"] = self.detail_intro.toPlainText()
        self.changed.emit()

    # ---------------- 封面 ----------------
    def _pick_cover(self) -> None:
        if self.project is None:
            return
        start_dir = os.path.dirname(self.project.get("cover_image") or "") or os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(self, "选择封面图（单选）", start_dir, IMAGE_FILTER)
        if not path:
            return
        self.project["cover_image"] = path
        self._refresh_cover_preview()
        self.changed.emit()

    def _clear_cover(self) -> None:
        if self.project is None:
            return
        self.project["cover_image"] = ""
        self._refresh_cover_preview()
        self.changed.emit()

    def _refresh_cover_preview(self) -> None:
        path = (self.project or {}).get("cover_image", "")
        if not path:
            self.cover_preview.setPixmap(QPixmap())
            self.cover_preview.setText("未选择封面图\n\n（任意比例均可，长图会完整显示）")
            self.cover_path.setText("尚未选择封面图")
            self.cover_path.setToolTip("")
            return
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.cover_preview.setText("图片无法读取")
            self.cover_path.setText("无法读取：%s" % path)
            self.cover_path.setToolTip(path)
            return
        # 完整显示（contain）：异形比例 / 长图不裁切，居中留边
        scaled = pixmap.scaled(
            max(1, self.cover_preview.width() - 2),
            max(1, self.cover_preview.height() - 2),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.cover_preview.setText("")
        self.cover_preview.setPixmap(scaled)
        size_kb = os.path.getsize(path) / 1024.0 if os.path.isfile(path) else 0
        self.cover_path.setText(
            "%s\n%d × %d px · %s · %.0f KB"
            % (
                os.path.basename(path),
                pixmap.width(),
                pixmap.height(),
                describe_aspect_ratio(pixmap.width(), pixmap.height()),
                size_kb,
            )
        )
        self.cover_path.setToolTip(path)

    # ---------------- 详情图 ----------------
    def _pick_detail_images(self) -> None:
        if self.project is None:
            return
        images = self.project.get("detail_images", [])
        start_dir = os.path.dirname(images[-1]) if images else os.path.expanduser("~")
        paths, _ = QFileDialog.getOpenFileNames(self, "选择二级详情图片（可多选）", start_dir, IMAGE_FILTER)
        if not paths:
            return
        for path in paths:
            if path not in images:
                images.append(path)
        self.project["detail_images"] = images
        self._rebuild_image_list()
        self.changed.emit()

    def _rebuild_image_list(self) -> None:
        self.image_list.clear()
        images = list((self.project or {}).get("detail_images", []))
        for index, path in enumerate(images):
            self._append_image_item(index, path)
        self._refresh_image_count()
        if images:
            self.image_list.setCurrentRow(0)

    def _append_image_item(self, index: int, path: str) -> None:
        item = QListWidgetItem()
        item.setData(Qt.UserRole, path)
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            item.setIcon(
                QIcon(
                    pixmap.scaled(104, 68, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
            )
        item.setText(self._image_label(index, path))
        item.setToolTip(path)
        item.setSizeHint(QSize(0, 74))
        self.image_list.addItem(item)

    @staticmethod
    def _image_label(index: int, path: str) -> str:
        name = os.path.basename(path)
        size_kb = ""
        if os.path.isfile(path):
            size_kb = "  ·  %.0f KB" % (os.path.getsize(path) / 1024.0)
        return "%02d   %s%s" % (index + 1, name, size_kb)

    def _refresh_image_count(self) -> None:
        self.img_count.setText("%d 张" % self.image_list.count())

    def _sync_image_order(self) -> None:
        if self.project is None:
            return
        paths = []
        for row in range(self.image_list.count()):
            item = self.image_list.item(row)
            path = item.data(Qt.UserRole)
            paths.append(path)
            item.setText(self._image_label(row, path))
        self.project["detail_images"] = paths
        self._refresh_image_count()

    def _on_images_moved(self, *args) -> None:
        if self._loading:
            return
        self._sync_image_order()
        self.changed.emit()

    def _move_images(self, delta: int) -> None:
        if move_list_rows(self.image_list, delta):
            self._sync_image_order()
            self.changed.emit()

    def _delete_images(self) -> None:
        rows = sorted(index.row() for index in self.image_list.selectedIndexes())
        if not rows:
            QMessageBox.information(self, "提示", "请先在列表中选择要删除的图片。")
            return
        for row in reversed(rows):
            self.image_list.takeItem(row)
        self._sync_image_order()
        self.changed.emit()

    def _clear_images(self) -> None:
        if self.image_list.count() == 0:
            return
        if QMessageBox.question(self, "确认", "确定清空当前项目的全部详情图吗？") != QMessageBox.Yes:
            return
        self.image_list.clear()
        self._sync_image_order()
        self.changed.emit()


# ============================================================================
# 4. 主窗口：Master-Detail 布局
# ============================================================================


class PortfolioBuilder(QMainWindow):
    def __init__(self):
        super().__init__()
        self.projects = [new_project(1)]
        self.current_index = 0
        self.project_file = None

        self.setWindowTitle("%s  v%s" % (APP_NAME, APP_VERSION))
        self.resize(1400, 920)
        self.setMinimumSize(1120, 720)

        self._build_ui()
        self._apply_style()
        self._sync_project_list()
        self._update_status("准备就绪：左侧管理项目，右侧编辑内容，最后点击「生成 HTML 网页」。")

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 16, 18, 10)
        root.setSpacing(12)

        root.addWidget(self._build_global_bar())

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(8)
        splitter.addWidget(self._build_master_panel())
        splitter.addWidget(self._build_detail_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([400, 1000])
        root.addWidget(splitter, 1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("statusLabel")
        root.addWidget(self.status_label)

    def _build_global_bar(self) -> QGroupBox:
        group = QGroupBox("全局设置 · GLOBAL SETTINGS")
        outer = QHBoxLayout(group)
        outer.setSpacing(18)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(9)

        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("例如：ATELIER NORD / SELECTED WORKS")
        self.title_edit.setText("PORTFOLIO · 作品集")

        self.subtitle_edit = QLineEdit()
        self.subtitle_edit.setPlaceholderText("例如：Branding · Editorial · 2020—2026")
        self.subtitle_edit.setText("Selected Works · 精选作品集")

        self.pwd_edit = QLineEdit()
        self.pwd_edit.setEchoMode(QLineEdit.Password)
        self.pwd_edit.setPlaceholderText("必填 · 用于网页解锁与图片数据解密")
        self.pwd_edit.setMinimumWidth(260)

        self.btn_pwd_eye = QPushButton("显示")
        self.btn_pwd_eye.setCheckable(True)
        self.btn_pwd_eye.setFixedWidth(64)
        self.btn_pwd_eye.toggled.connect(self._toggle_password)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("不允许复制 · 截图高斯模糊（最严格）", "strict")
        self.mode_combo.addItem("不允许复制 · 截图不模糊", "nocopy")
        self.mode_combo.addItem("允许复制 · 关闭截屏防护", "allow")
        self.mode_combo.setMinimumWidth(260)
        self.mode_combo.setToolTip(
            "严格：禁止选中 / 复制 / 右键；切屏、截图快捷键、F12 触发即高斯模糊 + 清空画面，需点击恢复。\n"
            "标准：禁止选中 / 复制 / 右键，但不做截屏模糊。\n"
            "自由：允许选中与复制文本，关闭截屏模糊和按键拦截（图片仍只以 Canvas 绘制，不产生 <img>）。"
        )

        self.mode_hint = QLabel("")
        self.mode_hint.setObjectName("hintLabel")

        grid.addWidget(QLabel("作品集主标题"), 0, 0)
        grid.addWidget(self.title_edit, 0, 1, 1, 3)
        grid.addWidget(QLabel("副标题（可选）"), 1, 0)
        grid.addWidget(self.subtitle_edit, 1, 1, 1, 3)
        grid.addWidget(QLabel("查看口令"), 2, 0)
        grid.addWidget(self.pwd_edit, 2, 1, 1, 2)
        grid.addWidget(self.btn_pwd_eye, 2, 3)
        grid.addWidget(QLabel("页面权限"), 3, 0)
        grid.addWidget(self.mode_combo, 3, 1, 1, 2)
        grid.addWidget(self.mode_hint, 4, 1, 1, 3)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        outer.addLayout(grid, 1)

        right = QVBoxLayout()
        right.setSpacing(8)
        self.btn_generate = QPushButton("一键生成 HTML 网页")
        self.btn_generate.setObjectName("primary")
        self.btn_generate.setMinimumHeight(46)
        right.addWidget(self.btn_generate)

        tools = QHBoxLayout()
        tools.setSpacing(8)
        self.btn_save = QPushButton("保存工程")
        self.btn_open = QPushButton("打开工程")
        self.btn_new = QPushButton("清空重来")
        for widget in (self.btn_save, self.btn_open, self.btn_new):
            tools.addWidget(widget)
        right.addLayout(tools)

        self.pwd_hint = QLabel("口令长度 0 位 · 建议 ≥ 6 位，字母 + 数字 + 符号混合")
        self.pwd_hint.setObjectName("hintLabel")
        right.addWidget(self.pwd_hint)
        outer.addLayout(right, 0)

        self.btn_generate.clicked.connect(self.generate_html)
        self.btn_save.clicked.connect(self.save_project_file)
        self.btn_open.clicked.connect(self.load_project_file)
        self.btn_new.clicked.connect(self.reset_all)
        self.pwd_edit.textChanged.connect(self._update_pwd_hint)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self._update_mode_hint()
        self.title_edit.textChanged.connect(lambda _t: self._touch())
        self.subtitle_edit.textChanged.connect(lambda _t: self._touch())
        return group

    def _build_master_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        group = QGroupBox("项目列表 · MASTER")
        inner = QVBoxLayout(group)
        inner.setSpacing(10)

        create_row = QHBoxLayout()
        create_row.setSpacing(8)
        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 200)
        self.count_spin.setValue(3)
        self.count_spin.setSuffix(" 个")
        self.btn_batch = QPushButton("批量创建")
        self.btn_batch.setObjectName("primaryGhost")
        create_row.addWidget(QLabel("数量"))
        create_row.addWidget(self.count_spin, 1)
        create_row.addWidget(self.btn_batch)
        inner.addLayout(create_row)

        self.project_list = ProjectListWidget()
        self.project_list.setObjectName("projectList")
        self.project_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.project_list.setDefaultDropAction(Qt.MoveAction)
        self.project_list.setDropIndicatorShown(True)
        self.project_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.project_list.setMinimumHeight(280)
        self.project_list.setToolTip("拖拽任意项目即可调整它在网页中的展示顺序")
        inner.addWidget(self.project_list, 1)

        manage = QHBoxLayout()
        manage.setSpacing(8)
        self.btn_add = QPushButton("新增项目")
        self.btn_dup = QPushButton("复制项目")
        self.btn_del = QPushButton("删除选中")
        for widget in (self.btn_add, self.btn_dup, self.btn_del):
            manage.addWidget(widget)
        inner.addLayout(manage)

        order = QHBoxLayout()
        order.setSpacing(8)
        self.btn_up = QPushButton("上移 ↑")
        self.btn_down = QPushButton("下移 ↓")
        order.addWidget(self.btn_up)
        order.addWidget(self.btn_down)
        inner.addLayout(order)

        tip = QLabel("提示：项目顺序 = 网页首页封面流顺序，也是抽屉导航 01/02/03… 的顺序。")
        tip.setObjectName("hintLabel")
        tip.setWordWrap(True)
        inner.addWidget(tip)

        layout.addWidget(group, 1)

        self.btn_batch.clicked.connect(self.batch_create)
        self.btn_add.clicked.connect(self.add_project)
        self.btn_dup.clicked.connect(self.duplicate_project)
        self.btn_del.clicked.connect(self.delete_project)
        self.btn_up.clicked.connect(lambda: self.move_projects(-1))
        self.btn_down.clicked.connect(lambda: self.move_projects(1))
        self.project_list.currentRowChanged.connect(self._on_project_selected)
        self.project_list.reordered.connect(self._on_projects_reordered)
        return panel

    def _build_detail_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("detailScroll")
        scroll.setFrameShape(QFrame.NoFrame)

        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(4, 0, 6, 0)
        layout.setSpacing(12)

        self.editor = ProjectEditor()
        self.editor.changed.connect(self._on_project_edited)
        layout.addWidget(self.editor, 1)
        scroll.setWidget(holder)
        return scroll

    # ------------------------------------------------------- 项目列表管理
    def _label_for(self, index: int, project: dict) -> str:
        name = project.get("title") or "未命名项目"
        cover = "有封面" if project.get("cover_image") else "缺封面"
        return "%02d   %s\n        %d 张详情图 · %s" % (
            index + 1,
            name,
            len(project.get("detail_images", [])),
            cover,
        )

    def _project_by_uid(self, uid):
        for project in self.projects:
            if project.get("uid") == uid:
                return project
        return None

    def _sync_project_list(self) -> None:
        self.project_list.blockSignals(True)
        self.project_list.clear()
        for index, project in enumerate(self.projects):
            item = QListWidgetItem(self._label_for(index, project))
            item.setData(Qt.UserRole, project.get("uid", ""))
            item.setSizeHint(QSize(0, 56))
            self.project_list.addItem(item)
        row = min(max(self.current_index, 0), self.project_list.count() - 1)
        self.project_list.setCurrentRow(row)
        self.project_list.blockSignals(False)
        self._on_project_selected(row)
        self._refresh_counters()

    def _refresh_labels(self) -> None:
        for index in range(self.project_list.count()):
            item = self.project_list.item(index)
            project = self._project_by_uid(item.data(Qt.UserRole))
            if project is not None:
                item.setText(self._label_for(index, project))

    def _refresh_counters(self) -> None:
        covers = sum(1 for p in self.projects if p.get("cover_image"))
        details = sum(len(p.get("detail_images", [])) for p in self.projects)
        self._update_status(
            "项目 %d 个 · 已配置封面 %d 张 · 详情图 %d 张" % (len(self.projects), covers, details)
        )

    def _on_project_selected(self, row: int) -> None:
        if row < 0 or row >= len(self.projects):
            return
        self.current_index = row
        self.editor.load_project(self.projects[row])

    def _on_projects_reordered(self) -> None:
        ordered = []
        for index in range(self.project_list.count()):
            project = self._project_by_uid(self.project_list.item(index).data(Qt.UserRole))
            if project is not None:
                ordered.append(project)
        if len(ordered) == len(self.projects):
            self.projects = ordered
            self.current_index = max(0, self.project_list.currentRow())
            self._refresh_labels()
            self._update_status("已调整项目顺序（网页展示顺序同步更新）。")

    def _on_project_edited(self) -> None:
        self._refresh_labels()
        self._refresh_counters()

    def batch_create(self) -> None:
        count = self.count_spin.value()
        start = len(self.projects)
        for offset in range(count):
            self.projects.append(new_project(start + offset + 1))
        self.current_index = len(self.projects) - 1
        self._sync_project_list()
        self._update_status("已批量创建 %d 个项目。" % count)

    def add_project(self) -> None:
        self.projects.append(new_project(len(self.projects) + 1))
        self.current_index = len(self.projects) - 1
        self._sync_project_list()

    def duplicate_project(self) -> None:
        if not (0 <= self.current_index < len(self.projects)):
            return
        source = self.projects[self.current_index]
        clone = {
            "uid": uuid.uuid4().hex[:16],
            "title": (source.get("title") or "项目") + " 副本",
            "meta": source.get("meta", ""),
            "cover_intro": source.get("cover_intro", ""),
            "cover_image": source.get("cover_image", ""),
            "detail_intro": source.get("detail_intro", ""),
            "detail_images": list(source.get("detail_images", [])),
        }
        self.projects.insert(self.current_index + 1, clone)
        self.current_index += 1
        self._sync_project_list()

    def delete_project(self) -> None:
        if len(self.projects) <= 1:
            QMessageBox.information(self, "提示", "至少需要保留一个项目。")
            return
        if not (0 <= self.current_index < len(self.projects)):
            return
        name = self.projects[self.current_index].get("title") or "该项目"
        if QMessageBox.question(self, "确认删除", "确定要删除「%s」吗？" % name) != QMessageBox.Yes:
            return
        self.projects.pop(self.current_index)
        self.current_index = min(self.current_index, len(self.projects) - 1)
        self._sync_project_list()

    def move_projects(self, delta: int) -> None:
        if move_list_rows(self.project_list, delta):
            self._on_projects_reordered()
            self.project_list.setCurrentRow(self.project_list.currentRow())

    # ----------------------------------------------------------- 工程存取
    def save_project_file(self) -> None:
        if not self.projects:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "保存工程文件", os.path.expanduser("~/portfolio_project.json"), "工程文件 (*.json)"
        )
        if not path:
            return
        data = {
            "app": APP_NAME,
            "version": APP_VERSION,
            "title": self.title_edit.text(),
            "subtitle": self.subtitle_edit.text(),
            "password": self.pwd_edit.text(),
            "page_mode": self.mode_combo.currentData(),
            "projects": self.projects,
        }
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
        except OSError as error:
            QMessageBox.critical(self, "保存失败", str(error))
            return
        self.project_file = path
        self._update_status("工程已保存：%s" % path)

    def load_project_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "打开工程文件", os.path.expanduser("~"), "工程文件 (*.json);;所有文件 (*.*)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception as error:  # noqa: BLE001
            QMessageBox.critical(self, "打开失败", str(error))
            return
        projects = data.get("projects") or []
        if not projects:
            QMessageBox.warning(self, "格式错误", "该工程文件中没有项目数据。")
            return
        self.projects = []
        for index, raw in enumerate(projects):
            project = new_project(index + 1)
            project.update(
                {
                    "title": raw.get("title", ""),
                    "meta": raw.get("meta", ""),
                    "cover_intro": raw.get("cover_intro", ""),
                    "cover_image": raw.get("cover_image", ""),
                    "detail_intro": raw.get("detail_intro", ""),
                    "detail_images": list(raw.get("detail_images", [])),
                }
            )
            self.projects.append(project)
        self.title_edit.setText(data.get("title", "") or "PORTFOLIO · 作品集")
        self.subtitle_edit.setText(data.get("subtitle", ""))
        self.pwd_edit.setText(data.get("password", ""))
        mode_index = self.mode_combo.findData(data.get("page_mode") or "strict")
        self.mode_combo.setCurrentIndex(mode_index if mode_index >= 0 else 0)
        self.current_index = 0
        self._sync_project_list()
        self._update_status("工程已载入：%s" % path)

    def reset_all(self) -> None:
        if QMessageBox.question(self, "确认", "清空当前全部项目与设置，重新开始？") != QMessageBox.Yes:
            return
        self.projects = [new_project(1)]
        self.current_index = 0
        self.title_edit.setText("PORTFOLIO · 作品集")
        self.subtitle_edit.setText("Selected Works · 精选作品集")
        self.pwd_edit.clear()
        self.mode_combo.setCurrentIndex(0)
        self._sync_project_list()

    # ------------------------------------------------------------- 生成逻辑
    def _validate(self) -> list:
        problems = []
        if not self.pwd_edit.text().strip():
            problems.append("「查看口令」为必填项，请先设置一个分享口令。")
        if not self.projects:
            problems.append("至少需要创建一个项目。")
        for index, project in enumerate(self.projects):
            name = project.get("title") or "未命名项目"
            if not (project.get("title") or "").strip():
                problems.append("项目 %02d：缺少项目名称 / 封面标题。" % (index + 1))
            cover = project.get("cover_image") or ""
            if not cover:
                problems.append("项目 %02d（%s）：尚未选择封面图。" % (index + 1, name))
            elif not os.path.isfile(cover):
                problems.append("项目 %02d（%s）：封面图文件不存在 → %s" % (index + 1, name, cover))
            for path in project.get("detail_images", []):
                if not os.path.isfile(path):
                    problems.append("项目 %02d（%s）：详情图文件不存在 → %s" % (index + 1, name, path))
        return problems

    def _collect_warnings(self) -> list:
        warnings = []
        pwd = self.pwd_edit.text()
        if pwd and len(pwd) < 6:
            warnings.append("查看口令长度仅 %d 位，建议使用 6 位以上混合字符。" % len(pwd))
        for index, project in enumerate(self.projects):
            if not project.get("detail_images"):
                warnings.append("项目 %02d（%s）没有二级详情图。" % (index + 1, project.get("title")))
            if not (project.get("detail_intro") or "").strip():
                warnings.append("项目 %02d（%s）没有填写二级详情文案。" % (index + 1, project.get("title")))
        return warnings

    def generate_html(self) -> None:
        problems = self._validate()
        if problems:
            QMessageBox.warning(
                self,
                "数据不完整",
                "请先修正以下问题：\n\n· " + "\n· ".join(problems[:12]),
            )
            return

        warnings = self._collect_warnings()
        if warnings:
            answer = QMessageBox.question(
                self,
                "存在提示项",
                "检测到以下提示（不影响生成）：\n\n· "
                + "\n· ".join(warnings[:10])
                + "\n\n是否继续生成网页？",
            )
            if answer != QMessageBox.Yes:
                return

        default_name = "%s.html" % (
            re.sub(r"[^\w\u4e00-\u9fff-]+", "_", self.title_edit.text().strip() or "portfolio")[:40]
        )
        save_dir = os.path.dirname(self.project_file or "") or os.path.expanduser("~")
        path, _ = QFileDialog.getSaveFileName(
            self, "保存生成的作品集网页", os.path.join(save_dir, default_name), "HTML 文件 (*.html)"
        )
        if not path:
            return
        if not path.lower().endswith(".html"):
            path += ".html"

        password = self.pwd_edit.text()
        image_total = sum(1 + len(p.get("detail_images", [])) for p in self.projects)
        dialog = QProgressDialog("正在读取并编码图片数据…", "取消", 0, image_total + 2, self)
        dialog.setWindowTitle("生成作品集网页")
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setValue(0)
        QApplication.processEvents()

        try:
            payload = self._build_payload(password, dialog, image_total)
        except RuntimeError:
            dialog.close()
            self._update_status("已取消生成。")
            return
        except Exception as error:  # noqa: BLE001
            dialog.close()
            QMessageBox.critical(self, "生成失败", "打包数据时出错：\n%s" % error)
            return

        if dialog.wasCanceled():
            dialog.close()
            return

        dialog.setLabelText("正在使用查看口令加密数据…")
        dialog.setValue(image_total + 1)
        QApplication.processEvents()

        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        cipher = encrypt_payload(raw, password)
        html_text = self._render_html(cipher, password, payload)

        dialog.setLabelText("正在写入 HTML 文件…")
        dialog.setValue(image_total + 2)
        QApplication.processEvents()

        try:
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(html_text)
        except OSError as error:
            dialog.close()
            QMessageBox.critical(self, "写入失败", str(error))
            return
        dialog.close()

        size_mb = os.path.getsize(path) / (1024.0 * 1024.0)
        box = QMessageBox(self)
        box.setWindowTitle("生成成功")
        box.setIcon(QMessageBox.Information)
        box.setText("作品集网页已生成！")
        box.setInformativeText(
            "文件：%s\n体积：%.2f MB\n项目：%d 个\n加密：XOR 口令密钥流 + Base64（SHA-256 校验）"
            % (path, size_mb, len(self.projects))
        )
        open_now = box.addButton("立即打开预览", QMessageBox.AcceptRole)
        show_dir = box.addButton("打开所在文件夹", QMessageBox.ActionRole)
        box.addButton("完成", QMessageBox.RejectRole)
        box.exec_()
        if box.clickedButton() is open_now:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        elif box.clickedButton() is show_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))
        self._update_status("生成完成：%s（%.2f MB）" % (path, size_mb))

    def _build_payload(self, password: str, dialog: QProgressDialog, image_total: int) -> dict:
        projects_data = []
        step = 0
        for index, project in enumerate(self.projects):
            dialog.setLabelText(
                "正在编码第 %d/%d 个项目：%s" % (index + 1, len(self.projects), project.get("title"))
            )
            QApplication.processEvents()
            if dialog.wasCanceled():
                raise RuntimeError("canceled")

            cover = read_image_payload(project["cover_image"])
            step += 1
            dialog.setValue(step)
            QApplication.processEvents()
            if dialog.wasCanceled():
                raise RuntimeError("canceled")

            images = []
            for path in project.get("detail_images", []):
                images.append(read_image_payload(path))
                step += 1
                dialog.setValue(step)
                QApplication.processEvents()
                if dialog.wasCanceled():
                    raise RuntimeError("canceled")

            projects_data.append(
                {
                    "title": project.get("title", ""),
                    "meta": project.get("meta", ""),
                    "coverIntro": project.get("cover_intro", ""),
                    "cover": cover,
                    "detailIntro": project.get("detail_intro", ""),
                    "images": images,
                }
            )

        return {
            "title": self.title_edit.text().strip() or "PORTFOLIO",
            "subtitle": self.subtitle_edit.text().strip(),
            "generated": datetime.datetime.now().strftime("%Y-%m-%d"),
            "year": datetime.datetime.now().strftime("%Y"),
            "totalImages": image_total,
            "projects": projects_data,
        }

    def _render_html(self, cipher: str, password: str, payload: dict) -> str:
        title = payload["title"]
        subtitle = payload["subtitle"] or "Selected Works"
        year = payload["year"]
        mode = self.mode_combo.currentData() or "strict"
        vault = (
            "{\n"
            "    title: %s,\n" % js_literal(title)
            + "    hash: %s,\n" % js_literal(password_hash_hex(password))
            + "    mode: %s,\n" % js_literal(mode)
            + "    payload: %s,\n" % js_literal(cipher)
            + "    count: %d,\n" % len(payload["projects"])
            + "    generated: %s\n" % js_literal(payload["generated"])
            + "  }"
        )
        mapping = {
            "TITLE_HTML": html_escape(title),
            "TITLE_JS": js_literal(title),
            "SUBTITLE_HTML": html_escape(subtitle),
            "YEAR": year,
            "COUNT": "%02d" % len(payload["projects"]),
            "VAULT": vault,
            "BUILD_DATE": payload["generated"],
        }
        return fill_template(HTML_TEMPLATE, mapping)

    # ------------------------------------------------------------- 杂项
    def _toggle_password(self, checked: bool) -> None:
        self.pwd_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        self.btn_pwd_eye.setText("隐藏" if checked else "显示")

    def _update_pwd_hint(self, text: str) -> None:
        length = len(text)
        if length == 0:
            level = "尚未设置口令"
        elif length < 6:
            level = "偏弱"
        elif length < 10:
            level = "可用"
        else:
            level = "较强"
        self.pwd_hint.setText("口令长度 %d 位 · %s · 建议 ≥ 6 位混合字符" % (length, level))

    def _on_mode_changed(self, _index: int) -> None:
        self._update_mode_hint()
        self._touch()

    def _update_mode_hint(self) -> None:
        mode = self.mode_combo.currentData()
        hints = {
            "strict": "禁止选中 / 复制 / 右键；切屏或截图快捷键触发高斯模糊，需点击「恢复浏览」。",
            "nocopy": "禁止选中 / 复制 / 右键；截图不做模糊，适合内部预览或投屏。",
            "allow": "允许选中与复制文本；关闭模糊与按键拦截，图片仍只以 Canvas 绘制。",
        }
        self.mode_hint.setText(hints.get(mode, ""))

    def _touch(self) -> None:
        self._update_status("设置已更新。")

    def _update_status(self, message: str) -> None:
        self.status_label.setText(message)

    def _apply_style(self) -> None:
        self.setStyleSheet(STYLE_SHEET)


STYLE_SHEET = """
QWidget {
    background: #0f1013;
    color: #e9e9ec;
    font-family: "Microsoft YaHei UI", "Segoe UI", "PingFang SC", sans-serif;
    font-size: 13px;
}
QMainWindow, QScrollArea, #detailScroll, QScrollArea > QWidget > QWidget { background: #0b0c0e; }
QGroupBox {
    background: #14161a;
    border: 1px solid #262a31;
    border-radius: 12px;
    margin-top: 16px;
    padding: 16px 14px 14px 14px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    padding: 0 8px;
    color: #ffffff;
    letter-spacing: 1px;
}
QLabel { background: transparent; }
QLabel#hintLabel { color: #7e828c; font-size: 12px; }
QLabel#pathLabel { color: #b9bcc4; font-size: 12px; }
QLabel#coverPreview {
    background: #0a0b0d;
    border: 1px dashed #333844;
    border-radius: 10px;
    color: #6f747f;
    font-size: 12px;
}
QLabel#statusLabel {
    color: #9aa0aa;
    padding: 6px 10px;
    background: #14161a;
    border: 1px solid #22252b;
    border-radius: 8px;
}
QLineEdit, QTextEdit, QSpinBox, QListWidget {
    background: #0a0b0d;
    border: 1px solid #2a2f38;
    border-radius: 8px;
    padding: 7px 9px;
    color: #eaeaf0;
    selection-background-color: #ffffff;
    selection-color: #14161a;
}
QLineEdit:focus, QTextEdit:focus, QSpinBox:focus, QListWidget:focus { border: 1px solid #ffffff; }
QLineEdit:disabled, QTextEdit:disabled, QListWidget:disabled { color: #5b6069; }
QSpinBox::up-button, QSpinBox::down-button { width: 16px; background: #1a1d22; border: none; }
QComboBox {
    background: #0a0b0d;
    border: 1px solid #2a2f38;
    border-radius: 8px;
    padding: 7px 9px;
    color: #eaeaf0;
    min-height: 18px;
}
QComboBox:hover, QComboBox:focus, QComboBox:on { border-color: #ffffff; }
QComboBox::drop-down { width: 26px; border: none; background: transparent; }
QComboBox QAbstractItemView {
    background: #14161a;
    border: 1px solid #2f343d;
    color: #e9e9ec;
    selection-background-color: #2a2f38;
    selection-color: #ffffff;
    outline: none;
    padding: 4px;
}
QPushButton {
    background: #1b1e24;
    border: 1px solid #31363f;
    border-radius: 8px;
    padding: 8px 14px;
    color: #e6e7ec;
}
QPushButton:hover { background: #23272f; border-color: #ffffff; color: #ffffff; }
QPushButton:pressed { background: #15181c; }
QPushButton:disabled { color: #5b6069; border-color: #24272d; }
QPushButton#primary {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #ffffff, stop:1 #c9c9c9);
    color: #17181b;
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 1px;
    border: none;
}
QPushButton#primary:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #ffffff, stop:1 #dedede); }
QPushButton#primaryGhost { border-color: #3a3e45; color: #e9e9e9; }
QPushButton#primaryGhost:hover { background: #1a1d22; border-color: #ffffff; }
QListWidget#projectList::item {
    padding: 8px 10px;
    border-radius: 8px;
    border: 1px solid transparent;
    color: #ccd0d8;
}
QListWidget#projectList::item:hover { background: #191c21; }
QListWidget#projectList::item:selected {
    background: #23262d;
    border: 1px solid #ffffff;
    color: #ffffff;
}
QListWidget#imageList::item {
    padding: 4px 8px;
    border-radius: 8px;
    border: 1px solid #1e2229;
    margin-bottom: 3px;
}
QListWidget#imageList::item:selected { background: #23262d; border: 1px solid #ffffff; color: #ffffff; }
QSplitter::handle { background: transparent; }
QScrollBar:vertical { background: #0b0c0e; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #2c313a; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #3d434f; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QScrollBar:horizontal { background: #0b0c0e; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: #2c313a; border-radius: 5px; min-width: 30px; }
QProgressDialog { background: #14161a; }
QProgressBar { background: #0a0b0d; border: 1px solid #2a2f38; border-radius: 6px; text-align: center; color: #e9e9ec; }
QProgressBar::chunk { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ffffff, stop:1 #c9c9c9); border-radius: 6px; }
QToolTip { background: #1b1e24; color: #e6e7ec; border: 1px solid #3a3f49; padding: 4px 6px; }
"""


# ============================================================================
# 5. 内嵌网页模板（单文件 HTML / CSS / JS）
# ============================================================================

_HTML_DOC_TOP = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
<meta name="robots" content="noindex, nofollow, noarchive, nosnippet" />
<meta name="referrer" content="no-referrer" />
<meta name="format-detection" content="telephone=no" />
<title>__TITLE_HTML__</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%230a0a0a'/%3E%3Cpath d='M18 44V20h10c6 0 9 3 9 8s-3 8-9 8h-4v8z' fill='%23ffffff'/%3E%3C/svg%3E" />
<style>
"""

_HTML_CSS = r"""
/* ===================== 设计变量 / BASE ===================== */
:root{
  --bg:#0a0a0a;
  --bg-2:#101012;
  --fg:#f5f5f5;
  --muted:#8b8b92;
  --dim:#5c5c64;
  --line:rgba(245,245,245,.12);
  --line-soft:rgba(245,245,245,.07);
  --accent:#ffffff;
  --ease:cubic-bezier(.16,1,.3,1);
  --ease-io:cubic-bezier(.62,.05,.28,1);
  --pad:clamp(22px,5vw,86px);
}
*{box-sizing:border-box;}
html,body{margin:0;padding:0;}
html{-webkit-text-size-adjust:100%;}
body{
  background:var(--bg);
  color:var(--fg);
  font-family:"Helvetica Neue","Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",system-ui,-apple-system,sans-serif;
  font-size:16px;line-height:1.75;letter-spacing:.012em;
  overflow-x:hidden;
  cursor:none;
  -webkit-font-smoothing:antialiased;
  -moz-osx-font-smoothing:grayscale;
  -webkit-tap-highlight-color:transparent;
  -webkit-touch-callout:none;
  -webkit-user-select:none;-moz-user-select:none;-ms-user-select:none;user-select:none;
}
body.locked{height:100%;overflow:hidden;}
body.drawer-open{overflow:hidden;}
body.shielded{cursor:auto;}
body.shielded .cursor-dot,body.shielded .cursor-ring{opacity:0;}
canvas,img{-webkit-user-drag:none;user-drag:none;pointer-events:none;}
input,textarea{-webkit-user-select:text;-moz-user-select:text;user-select:text;cursor:text;}
/* 允许复制模式：恢复文本选择、系统光标，并隐藏自定义光标 */
body.mode-allow{-webkit-user-select:text;-moz-user-select:text;-ms-user-select:text;user-select:text;cursor:auto;}
body.mode-allow input,body.mode-allow textarea{cursor:text;}
body.mode-allow button,body.mode-allow a{cursor:pointer;}
body.mode-allow .cursor-dot,body.mode-allow .cursor-ring{display:none;}
a{color:inherit;text-decoration:none;}
button{font:inherit;color:inherit;background:none;border:none;cursor:none;}
::selection{background:rgba(255,255,255,.28);}
[hidden]{display:none !important;}

/* 极简自定义跟随光标 */
.cursor-dot,.cursor-ring{position:fixed;top:0;left:0;z-index:12000;pointer-events:none;border-radius:50%;}
.cursor-dot{width:6px;height:6px;background:var(--accent);margin:-3px 0 0 -3px;transition:opacity .3s linear;}
.cursor-ring{
  width:38px;height:38px;margin:-19px 0 0 -19px;
  border:1px solid rgba(255,255,255,.55);
  transition:width .35s var(--ease),height .35s var(--ease),margin .35s var(--ease),background .35s var(--ease),opacity .3s linear;
}
.cursor-ring.is-hover{width:74px;height:74px;margin:-37px 0 0 -37px;background:rgba(255,255,255,.08);border-color:rgba(255,255,255,.8);}
.cursor-ring.is-press{width:24px;height:24px;margin:-12px 0 0 -12px;background:rgba(255,255,255,.22);}
@media (hover:none),(pointer:coarse){
  body,button{cursor:auto;}
  .cursor-dot,.cursor-ring{display:none;}
}

/* 顶部滚动进度 */
.progress{
  position:fixed;top:0;left:0;height:2px;width:0;z-index:9500;
  background:linear-gradient(90deg,rgba(255,255,255,.1),var(--accent));
  transition:width .12s linear;
}

/* ===================== 长图 / 完整大图查看层 ===================== */
.zoom{
  position:fixed;inset:0;z-index:9600;
  background:rgba(6,6,8,.97);
  overflow-y:auto;overflow-x:hidden;
  padding:70px 18px 54px;
  opacity:0;visibility:hidden;pointer-events:none;
  transition:opacity .35s var(--ease),visibility .35s var(--ease);
}
.zoom.show{opacity:1;visibility:visible;pointer-events:auto;}
.zoom-stage{display:flex;justify-content:center;align-items:flex-start;}
.zoom-canvas{display:block;border:1px solid rgba(255,255,255,.16);box-shadow:0 40px 120px rgba(0,0,0,.65);}
.zoom-close{
  position:fixed;top:20px;right:24px;z-index:9601;
  width:44px;height:44px;border-radius:50%;
  border:1px solid rgba(255,255,255,.3);color:#fff;
  background:rgba(12,12,14,.7);font-size:18px;line-height:1;
  transition:transform .35s var(--ease),border-color .35s var(--ease);
}
.zoom-close:hover{transform:rotate(90deg);border-color:#fff;}
.zoom-tip{position:fixed;left:24px;bottom:20px;z-index:9601;font-size:11px;letter-spacing:.18em;color:var(--dim);}
body.zoom-open{overflow:hidden;}

/* ===================== 解锁层 ===================== */
.gate{
  position:fixed;inset:0;z-index:10000;
  display:flex;align-items:center;justify-content:center;
  padding:24px;
  background:
    radial-gradient(1100px 620px at 50% 18%,rgba(255,255,255,.10),transparent 62%),
    radial-gradient(900px 700px at 80% 100%,rgba(90,120,255,.06),transparent 70%),
    #070708;
  transition:opacity .9s var(--ease),visibility .9s var(--ease);
}
.gate.hidden{opacity:0;visibility:hidden;pointer-events:none;}
.gate::after{
  content:"";position:absolute;inset:0;pointer-events:none;opacity:.5;
  background-image:linear-gradient(rgba(255,255,255,.028) 1px,transparent 1px);
  background-size:100% 3px;
}
.gate-card{
  position:relative;width:min(560px,94vw);padding:52px 44px 36px;
  text-align:center;
  background:rgba(16,16,18,.72);
  border:1px solid rgba(245,245,245,.10);
  border-radius:20px;
  backdrop-filter:blur(26px) saturate(140%);
  -webkit-backdrop-filter:blur(26px) saturate(140%);
  box-shadow:0 40px 120px rgba(0,0,0,.6);
  animation:gateIn 1.1s var(--ease) both;
}
@keyframes gateIn{from{opacity:0;transform:translateY(26px) scale(.98);}to{opacity:1;transform:none;}}
.gate-lock{
  width:52px;height:52px;margin:0 auto 22px;border-radius:50%;
  display:flex;align-items:center;justify-content:center;
  border:1px solid rgba(255,255,255,.45);color:var(--accent);
  background:radial-gradient(circle at 50% 30%,rgba(255,255,255,.16),transparent 70%);
}
.gate-title{margin:0;font-size:clamp(22px,3vw,30px);font-weight:600;letter-spacing:.16em;text-transform:uppercase;}
.gate-sub{margin-top:10px;color:var(--muted);font-size:13px;letter-spacing:.22em;text-transform:uppercase;}
.gate-divider{height:1px;margin:26px auto;width:64px;background:linear-gradient(90deg,transparent,var(--accent),transparent);}
.gate-note{margin:0 0 22px;color:var(--muted);font-size:13.5px;}
.gate-form{display:flex;gap:10px;align-items:stretch;}
.gate-form input{
  flex:1;min-width:0;padding:14px 16px;color:var(--fg);font-size:15px;letter-spacing:.08em;
  background:rgba(255,255,255,.03);border:1px solid rgba(245,245,245,.14);border-radius:10px;
  outline:none;transition:border-color .3s var(--ease),background .3s var(--ease);
}
.gate-form input::placeholder{color:#6a6a72;letter-spacing:.14em;font-size:13px;}
.gate-form input:focus{border-color:var(--accent);background:rgba(255,255,255,.05);}
.gate-form button{
  padding:0 26px;border-radius:10px;font-size:14px;letter-spacing:.24em;
  background:linear-gradient(135deg,#ffffff,#cfcfcf);color:#141416;font-weight:700;
  transition:transform .35s var(--ease),filter .35s var(--ease);
}
.gate-form button:hover{filter:brightness(1.08);transform:translateY(-1px);}
.gate-form button:active{transform:translateY(0) scale(.99);}
.gate-msg{min-height:22px;margin-top:14px;font-size:13px;color:#ff8080;letter-spacing:.04em;}
.gate-msg.ok{color:#8fe0a8;}
.gate-foot{
  margin-top:26px;padding-top:18px;border-top:1px solid var(--line-soft);
  display:flex;gap:10px;justify-content:center;flex-wrap:wrap;
  color:var(--dim);font-size:11.5px;letter-spacing:.16em;text-transform:uppercase;
}

/* ===================== 顶部栏 / 抽屉导航 ===================== */
.app{opacity:0;transition:none;}
.app.ready{opacity:1;}
.topbar{
  position:fixed;top:0;left:0;right:0;height:78px;z-index:600;
  display:flex;align-items:center;justify-content:space-between;
  padding:0 clamp(18px,3.4vw,44px);
}
.topbar::before{
  content:"";position:absolute;inset:0;z-index:-1;pointer-events:none;
  background:linear-gradient(180deg,rgba(10,10,10,.92),rgba(10,10,10,.55) 42%,transparent);
  opacity:0;transition:opacity .5s var(--ease);
}
.app.scrolled .topbar::before{opacity:1;}
.nav-btn{display:flex;align-items:center;gap:13px;padding:9px 4px;}
.nav-lines{display:flex;flex-direction:column;gap:5px;width:28px;}
.nav-lines i{display:block;height:1px;background:var(--fg);transition:transform .5s var(--ease),width .5s var(--ease),opacity .4s linear;}
.nav-lines i:nth-child(1){width:100%;}
.nav-lines i:nth-child(2){width:64%;}
.nav-lines i:nth-child(3){width:38%;}
.nav-btn:hover .nav-lines i:nth-child(2){width:100%;}
.nav-btn:hover .nav-lines i:nth-child(3){width:72%;}
.nav-label{font-size:11.5px;letter-spacing:.34em;text-transform:uppercase;color:var(--fg);}
.brand{font-size:12px;letter-spacing:.34em;text-transform:uppercase;color:var(--muted);}
.topbar-right{display:flex;align-items:center;gap:16px;}
.scroll-cue{
  display:flex;align-items:center;gap:9px;font-size:11px;letter-spacing:.3em;
  text-transform:uppercase;color:var(--dim);
}
.scroll-cue i{display:block;width:36px;height:1px;background:var(--line);position:relative;overflow:hidden;}
.scroll-cue i::after{
  content:"";position:absolute;inset:0;background:var(--accent);
  animation:cue 2.4s var(--ease-io) infinite;
}
@keyframes cue{0%{transform:translateX(-100%);}60%{transform:translateX(100%);}100%{transform:translateX(100%);}}
body.view-detail .scroll-cue{opacity:0;transition:opacity .4s linear;}

.drawer-mask{
  position:fixed;inset:0;z-index:700;opacity:0;visibility:hidden;
  background:rgba(5,5,6,.66);
  backdrop-filter:blur(14px) saturate(120%);
  -webkit-backdrop-filter:blur(14px) saturate(120%);
  transition:opacity .7s var(--ease),visibility .7s var(--ease);
}
body.drawer-open .drawer-mask{opacity:1;visibility:visible;}
.drawer{
  position:fixed;top:0;bottom:0;left:0;width:min(430px,88vw);z-index:800;
  display:flex;flex-direction:column;padding:36px clamp(24px,3vw,40px) 30px;
  background:linear-gradient(180deg,rgba(16,16,18,.94),rgba(11,11,12,.94));
  border-right:1px solid var(--line);
  backdrop-filter:blur(26px) saturate(150%);
  -webkit-backdrop-filter:blur(26px) saturate(150%);
  transform:translateX(-102%);
  transition:transform .85s var(--ease);
  box-shadow:40px 0 120px rgba(0,0,0,.55);
}
body.drawer-open .drawer{transform:translateX(0);}
.drawer-top{position:relative;padding-bottom:22px;border-bottom:1px solid var(--line-soft);}
.drawer-eyebrow{font-size:10.5px;letter-spacing:.4em;text-transform:uppercase;color:var(--accent);}
.drawer-title{margin-top:10px;font-size:26px;font-weight:600;letter-spacing:.06em;}
.drawer-close{
  position:absolute;top:0;right:0;width:38px;height:38px;border-radius:50%;
  border:1px solid var(--line);color:var(--muted);font-size:14px;line-height:1;
  display:flex;align-items:center;justify-content:center;
  transition:border-color .4s var(--ease),color .4s var(--ease),transform .5s var(--ease);
}
.drawer-close:hover{border-color:var(--accent);color:var(--accent);transform:rotate(90deg);}
.drawer-list{flex:1;overflow-y:auto;padding:22px 0;margin:0;display:flex;flex-direction:column;gap:2px;}
.drawer-item{
  display:flex;align-items:baseline;gap:16px;width:100%;text-align:left;
  padding:14px 12px;border-radius:10px;
  border-bottom:1px solid var(--line-soft);
  transition:background .45s var(--ease),padding-left .45s var(--ease),color .45s var(--ease);
  opacity:0;transform:translateX(-18px);
}
body.drawer-open .drawer-item{opacity:1;transform:none;transition:opacity .7s var(--ease),transform .7s var(--ease),background .45s var(--ease),padding-left .45s var(--ease);}
.drawer-item:hover{background:rgba(255,255,255,.07);padding-left:20px;}
.drawer-item.active{color:var(--accent);}
.drawer-item.active .di-num{color:var(--accent);}
.di-num{font-size:11px;letter-spacing:.24em;color:var(--dim);min-width:30px;}
.di-name{font-size:16.5px;letter-spacing:.02em;}
.drawer-home .di-name{color:var(--muted);}
.drawer-foot{padding-top:18px;border-top:1px solid var(--line-soft);color:var(--muted);font-size:12px;letter-spacing:.1em;}
.drawer-foot-dim{margin-top:6px;color:var(--dim);font-size:11px;letter-spacing:.18em;text-transform:uppercase;}

/* ===================== 首页封面流 ===================== */
.content{position:relative;}
.gallery{padding-top:0;}
.gallery.fade-out{opacity:0;transform:translateY(-26px);transition:opacity .42s var(--ease-io),transform .42s var(--ease-io);}
.hero{
  min-height:100vh;display:flex;flex-direction:column;justify-content:center;
  padding:120px var(--pad) 90px;
}
.hero-eyebrow{font-size:11px;letter-spacing:.46em;text-transform:uppercase;color:var(--accent);}
.hero-title{
  margin:26px 0 0;font-size:clamp(42px,8vw,120px);line-height:.98;font-weight:600;
  letter-spacing:-.015em;text-transform:uppercase;
}
.hero-sub{margin:26px 0 0;max-width:640px;color:var(--muted);font-size:clamp(14px,1.3vw,17px);letter-spacing:.06em;}
.hero-line{margin:44px 0 0;width:min(560px,70vw);height:1px;background:linear-gradient(90deg,var(--accent),transparent);}
.hero-meta{margin-top:24px;display:flex;gap:34px;flex-wrap:wrap;color:var(--dim);font-size:11.5px;letter-spacing:.3em;text-transform:uppercase;}
.hero > *{animation:heroUp 1.3s var(--ease) both;}
.hero > *:nth-child(2){animation-delay:.1s;}
.hero > *:nth-child(3){animation-delay:.2s;}
.hero > *:nth-child(4){animation-delay:.3s;}
.hero > *:nth-child(5){animation-delay:.4s;}
@keyframes heroUp{from{opacity:0;transform:translateY(34px);}to{opacity:1;transform:none;}}

.works{display:block;}
/* 一级：整屏吸附 —— 每个项目一屏，滚动自动吸附 */
html.snap-gallery{scroll-snap-type:y mandatory;}
html.snap-gallery .hero,
html.snap-gallery .work{scroll-snap-align:start;scroll-snap-stop:always;}
html.snap-gallery .site-foot{scroll-snap-align:end;}
.work{
  position:relative;display:block;width:100%;
  height:100vh;height:100svh;min-height:100vh;min-height:100svh;
  padding:0;border-top:1px solid var(--line-soft);
}
.work:first-child{border-top:none;}
.work-media{
  position:absolute;inset:0;width:100%;height:100%;overflow:hidden;background:var(--bg);
  border-radius:0;box-shadow:none;
}
.parallax{will-change:auto;}
/* 一级：全铺满横幅 —— 宽度 100%，高度严格按原图比例；极端比例限高后由 Canvas contain 完整显示 */
.work .media-inner{width:100%;height:100%;max-height:none;aspect-ratio:auto;}
.work .parallax{width:100%;height:100%;}
.media-inner{position:relative;width:100%;aspect-ratio:16/9;overflow:hidden;}
.media-canvas{position:absolute;inset:0;width:100%;height:100%;display:block;opacity:0;transition:opacity .9s var(--ease);}
.media-canvas.ready{opacity:1;}
/* 全屏封面：画布四边渐隐，柔顺融入深色背景 */
.work .media-canvas{
  -webkit-mask-image:radial-gradient(ellipse 92% 90% at 50% 50%,#000 52%,rgba(0,0,0,.72) 76%,transparent 100%);
  mask-image:radial-gradient(ellipse 92% 90% at 50% 50%,#000 52%,rgba(0,0,0,.72) 76%,transparent 100%);
}
.media-shield{
  position:absolute;inset:0;z-index:3;background:transparent;
  display:flex;align-items:flex-end;justify-content:flex-start;gap:8px;
  padding:22px 24px;
}
.media-zoom{
  position:absolute;right:16px;bottom:14px;
  font-size:11px;letter-spacing:.16em;color:rgba(245,245,245,.78);
  border:1px solid rgba(255,255,255,.26);background:rgba(8,8,10,.55);
  padding:6px 11px;border-radius:2px;
  opacity:0;transform:translateY(6px);
  transition:opacity .4s var(--ease),transform .4s var(--ease);
}
.media-shield:hover .media-zoom{opacity:1;transform:none;}
.media-badge,.media-badge-arrow{
  font-size:11px;letter-spacing:.28em;text-transform:uppercase;
  color:#141416;background:var(--accent);padding:9px 16px;border-radius:2px;
  opacity:0;transform:translateY(12px);
  transition:opacity .5s var(--ease),transform .6s var(--ease);
}
.media-badge-arrow{padding:9px 13px;}
.work-media:hover .media-badge,.work-media:hover .media-badge-arrow{opacity:1;transform:none;}
.work-info{
  position:absolute;left:0;right:0;bottom:0;z-index:5;
  padding:clamp(30px,5vw,84px) var(--pad) clamp(28px,4.5vw,64px);
  background:linear-gradient(to top,rgba(5,5,7,.88) 0%,rgba(5,5,7,.46) 40%,rgba(5,5,7,0) 100%);
  pointer-events:none;
}
.work-index{font-size:11px;letter-spacing:.42em;color:var(--accent);}
.work-title{
  margin:14px 0 0;font-size:clamp(30px,4.6vw,72px);line-height:1.02;font-weight:600;letter-spacing:-.015em;
}
.work-meta{margin-top:12px;color:#b9b9c2;font-size:11.5px;letter-spacing:.3em;text-transform:uppercase;}
.work-intro{margin:18px 0 0;max-width:56ch;color:#d2d2d8;font-size:15px;line-height:1.85;}
.work-cta{
  margin-top:24px;display:inline-flex;align-items:center;gap:14px;
  font-size:12px;letter-spacing:.3em;text-transform:uppercase;color:var(--fg);
  pointer-events:auto;
}
.work-cta i{display:block;width:44px;height:1px;background:var(--line);position:relative;transition:width .5s var(--ease),background .5s var(--ease);}
.work-cta:hover i{width:76px;background:var(--accent);}
.reveal{opacity:0;transform:translateY(38px);transition:opacity 1.1s var(--ease),transform 1.1s var(--ease);}
.reveal.in{opacity:1;transform:none;}

.site-foot{
  display:flex;justify-content:space-between;gap:18px;flex-wrap:wrap;
  padding:60px var(--pad) 68px;border-top:1px solid var(--line-soft);
  color:var(--muted);font-size:12px;letter-spacing:.16em;
}
.site-foot .dim{color:var(--dim);font-size:11px;letter-spacing:.22em;text-transform:uppercase;}

/* ===================== 详情（二级界面） ===================== */
.detail{opacity:0;transform:translateY(24px);transition:opacity .75s var(--ease),transform .75s var(--ease);}
.detail.show{opacity:1;transform:none;}
.detail-top{padding:118px var(--pad) 0;}
.back-btn{
  display:inline-flex;align-items:center;gap:12px;
  font-size:11.5px;letter-spacing:.3em;text-transform:uppercase;color:var(--muted);
  border:1px solid var(--line);border-radius:999px;padding:11px 20px;
  transition:color .4s var(--ease),border-color .4s var(--ease),gap .4s var(--ease);
}
.back-btn:hover{color:var(--accent);border-color:rgba(255,255,255,.55);gap:18px;}
.back-arrow{display:inline-block;transition:transform .5s var(--ease);}
.back-btn:hover .back-arrow{transform:translateX(-4px);}
.detail-head{padding:56px var(--pad) 26px;max-width:1180px;}
.detail-eyebrow{display:flex;align-items:center;gap:12px;font-size:11px;letter-spacing:.38em;text-transform:uppercase;color:var(--muted);}
.detail-eyebrow .dot{width:6px;height:6px;border-radius:50%;background:var(--accent);}
.detail-eyebrow b{color:var(--accent);font-weight:600;}
.detail-eyebrow .slash{color:var(--dim);}
.detail-title{margin:22px 0 0;font-size:clamp(32px,6vw,86px);line-height:1.02;font-weight:600;letter-spacing:-.015em;}
.detail-meta{margin-top:18px;color:var(--dim);font-size:11.5px;letter-spacing:.3em;text-transform:uppercase;}
.detail-intro{margin:30px 0 0;max-width:74ch;color:#c9c9d0;font-size:16px;line-height:2;white-space:pre-wrap;}
.detail-intro:empty{display:none;}
.detail-gallery{
  display:flex;flex-direction:column;align-items:center;gap:clamp(30px,6vh,80px);
  padding:clamp(50px,8vh,120px) var(--pad);
}
.detail-item{width:min(1080px,100%);display:flex;flex-direction:column;gap:14px;opacity:0;transform:translateY(34px);transition:opacity 1.1s var(--ease),transform 1.1s var(--ease);}
.detail-item.in{opacity:1;transform:none;}
/* 二级：每张图固定宽度、按原图比例显示，绝不裁剪 */
.detail-item.w-large,.detail-item.w-mid,.detail-item.w-small{width:min(1080px,100%);align-self:center;}
.detail-media{position:relative;overflow:hidden;border-radius:3px;background:#111113;box-shadow:0 30px 80px rgba(0,0,0,.45);}
.detail-media .media-inner{aspect-ratio:16/9;}
.detail-cap{
  display:flex;align-items:center;gap:12px;
  color:var(--dim);font-size:11px;letter-spacing:.3em;text-transform:uppercase;
}
.detail-cap::before{content:"";width:26px;height:1px;background:var(--line);}
.detail-foot{
  margin-top:20px;padding:clamp(46px,7vh,96px) var(--pad) 78px;border-top:1px solid var(--line-soft);
  display:flex;justify-content:space-between;align-items:flex-end;gap:26px;flex-wrap:wrap;
}
.next-label{font-size:11px;letter-spacing:.36em;text-transform:uppercase;color:var(--dim);}
.next-btn{
  margin-top:16px;display:flex;align-items:center;gap:22px;
  font-size:clamp(22px,3.2vw,44px);font-weight:600;letter-spacing:-.01em;
  transition:color .45s var(--ease),gap .45s var(--ease);
}
.next-btn:hover{color:var(--accent);gap:34px;}
.next-arrow{display:inline-block;font-size:.75em;color:var(--accent);}
.back-btn.ghost{border-color:transparent;padding-left:0;}

/* ===================== 防截屏遮罩 ===================== */
body.shielded .app{
  filter:blur(40px) saturate(.55) brightness(.62);
  pointer-events:none;
}
.app{transition:none;}
.shield{
  position:fixed;inset:0;z-index:11000;opacity:0;visibility:hidden;
  display:flex;align-items:center;justify-content:center;padding:26px;
  background:rgba(6,6,7,.86);
}
.shield.show{opacity:1;visibility:visible;}
.shield-card{
  width:min(540px,92vw);text-align:center;padding:44px 38px 34px;
  background:rgba(18,18,20,.9);
  border:1px solid rgba(255,255,255,.3);border-radius:18px;
  box-shadow:0 40px 120px rgba(0,0,0,.7);
}
.shield-eye{
  width:46px;height:46px;margin:0 auto 20px;border-radius:50%;
  display:flex;align-items:center;justify-content:center;color:var(--accent);
  border:1px solid rgba(255,255,255,.4);
  background:radial-gradient(circle at 50% 30%,rgba(255,255,255,.18),transparent 70%);
  animation:pulse 2.6s var(--ease-io) infinite;
}
@keyframes pulse{0%,100%{transform:scale(1);opacity:1;}50%{transform:scale(1.07);opacity:.75;}}
.shield-card h2{margin:0 0 12px;font-size:20px;font-weight:600;letter-spacing:.05em;}
.shield-card p{margin:0 0 26px;color:var(--muted);font-size:13.5px;line-height:1.9;}
.shield-card button{
  padding:14px 30px;border-radius:10px;font-size:13px;letter-spacing:.24em;
  background:linear-gradient(135deg,#ffffff,#cfcfcf);color:#141416;font-weight:700;
  transition:transform .35s var(--ease),filter .35s var(--ease);
}
.shield-card button:hover{filter:brightness(1.08);transform:translateY(-1px);}
.shield-hint{margin-top:18px;min-height:18px;color:#ff9a9a;font-size:12px;letter-spacing:.06em;}
body.shielded .shield-card button{cursor:pointer;}

.noscript{
  position:fixed;inset:0;display:flex;align-items:center;justify-content:center;
  background:#0a0a0a;color:#f5f5f5;font-size:15px;letter-spacing:.1em;text-align:center;padding:30px;
}

/* ===================== 响应式 ===================== */
@media (max-width:900px){
  .work .media-inner{max-height:none;}
  .work-info{padding:26px var(--pad) 30px;}
  .work-title{font-size:clamp(26px,8vw,46px);}
  .work-intro{display:none;}
  .detail-item.w-mid,.detail-item.w-small{width:100%;}
  .detail-title{font-size:clamp(28px,9vw,52px);}
  .hero{min-height:92vh;}
  .topbar{height:66px;}
  .scroll-cue{display:none;}
}

/* ===================== 打印保护 ===================== */
@media print{
  html,body{display:none !important;visibility:hidden !important;background:#000 !important;}
  *{visibility:hidden !important;}
}
</style>
</head>
"""

_HTML_BODY = r"""<body class="locked">

<!-- 极简跟随光标 -->
<div class="cursor-dot" id="cursorDot"></div>
<div class="cursor-ring" id="cursorRing"></div>
<div class="progress" id="progress"></div>

<!-- ================= 分享码解锁层 ================= -->
<div class="gate" id="gate">
  <div class="gate-card">
    <div class="gate-lock">
      <svg viewBox="0 0 24 24" width="19" height="19" aria-hidden="true">
        <path d="M7.2 10V7.1a4.8 4.8 0 0 1 9.6 0V10" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>
        <rect x="4.6" y="10" width="14.8" height="10.4" rx="2.4" fill="none" stroke="currentColor" stroke-width="1.4"/>
      </svg>
    </div>
    <h1 class="gate-title">__TITLE_HTML__</h1>
    <div class="gate-sub">__SUBTITLE_HTML__</div>
    <div class="gate-divider"></div>
    <p class="gate-note">该作品集受查看口令保护，请输入口令后浏览全部项目与图片。</p>
    <div class="gate-form">
      <input id="gateInput" type="password" placeholder="查看口令 / ACCESS CODE" autocomplete="off" spellcheck="false" />
      <button id="gateBtn" type="button">解 锁</button>
    </div>
    <div class="gate-msg" id="gateMsg"></div>
    <div class="gate-foot">
      <span>© __YEAR__ __TITLE_HTML__</span>
      <span>·</span>
      <span>__COUNT__ 个项目 · 图片数据已加密</span>
    </div>
  </div>
</div>

<!-- ================= 主应用 ================= -->
<div class="app" id="app">

  <header class="topbar">
    <button class="nav-btn" id="navBtn" type="button" aria-label="打开项目导航">
      <span class="nav-lines"><i></i><i></i><i></i></span>
      <span class="nav-label">Projects</span>
    </button>
    <div class="brand">__TITLE_HTML__</div>
    <div class="topbar-right">
      <span class="scroll-cue">Scroll<i></i></span>
    </div>
  </header>

  <aside class="drawer" id="drawer" aria-hidden="true">
    <div class="drawer-top">
      <div class="drawer-eyebrow">Index</div>
      <div class="drawer-title">项目导航</div>
      <button class="drawer-close" id="drawerClose" type="button" aria-label="关闭导航">✕</button>
    </div>
    <nav class="drawer-list" id="drawerList"></nav>
    <div class="drawer-foot">
      <div>__SUBTITLE_HTML__</div>
      <div class="drawer-foot-dim">© __YEAR__ · Protected Content</div>
    </div>
  </aside>
  <div class="drawer-mask" id="drawerMask"></div>

  <main class="content" id="content">

    <!-- 一级界面：封面流 -->
    <section class="gallery" id="galleryView">
      <div class="hero" id="hero">
        <div class="hero-eyebrow">Selected Works</div>
        <h1 class="hero-title">__TITLE_HTML__</h1>
        <p class="hero-sub">__SUBTITLE_HTML__</p>
        <div class="hero-line"></div>
        <div class="hero-meta">
          <span>__COUNT__ Projects</span>
          <span>Updated __BUILD_DATE__</span>
          <span>Confidential</span>
        </div>
      </div>
      <div class="works" id="worksList"></div>
      <footer class="site-foot">
        <div>© __YEAR__ __TITLE_HTML__</div>
        <div class="dim">All images are copyright protected · 禁止转载 / 截屏 / 下载</div>
      </footer>
    </section>

    <!-- 二级界面：项目详情 -->
    <section class="detail" id="detailView" hidden>
      <div class="detail-top">
        <button class="back-btn" id="backBtn" type="button">
          <span class="back-arrow">←</span> 返回首页 Back
        </button>
      </div>
      <header class="detail-head">
        <div class="detail-eyebrow">
          <span class="dot"></span>
          <span>Project</span>
          <b id="dIndex">01</b>
          <span class="slash">/</span>
          <span id="dTotal">01</span>
        </div>
        <h1 class="detail-title" id="dTitle"></h1>
        <div class="detail-meta" id="dMeta"></div>
        <p class="detail-intro" id="dIntro"></p>
      </header>
      <div class="detail-gallery" id="dGallery"></div>
      <div class="detail-foot">
        <div class="next-wrap">
          <div class="next-label">Next Project</div>
          <button class="next-btn" id="nextBtn" type="button">
            <span id="nextTitle">—</span>
            <span class="next-arrow">→</span>
          </button>
        </div>
        <button class="back-btn ghost" id="backBtn2" type="button">返回主画廊</button>
      </div>
    </section>

  </main>
</div>

<!-- ================= 防截屏模糊锁定层 ================= -->
<div class="shield" id="shield">
  <div class="shield-card">
    <div class="shield-eye">
      <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
        <path d="M2.5 12S6 5.8 12 5.8 21.5 12 21.5 12 18 18.2 12 18.2 2.5 12 2.5 12Z" fill="none" stroke="currentColor" stroke-width="1.3"/>
        <circle cx="12" cy="12" r="3" fill="none" stroke="currentColor" stroke-width="1.3"/>
        <path d="M4 20 20 4" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>
      </svg>
    </div>
    <h2>为保护版权，当前画面已模糊锁定</h2>
    <p>检测到可能的截屏 / 窗口切换行为，画面内容已临时隐藏（图片与文字均无法被截取）。<br/>请点击下方按钮恢复浏览。</p>
    <button id="shieldBtn" type="button">点击恢复浏览</button>
    <div class="shield-hint" id="shieldHint"></div>
  </div>
</div>

<!-- ================= 长图 / 完整大图查看层 ================= -->
<div class="zoom" id="zoomLayer" aria-hidden="true">
  <button class="zoom-close" id="zoomClose" type="button" aria-label="关闭">✕</button>
  <div class="zoom-stage" id="zoomStage"></div>
  <div class="zoom-tip">ESC 关闭 · 滚动查看完整长图</div>
</div>

<noscript><div class="noscript">本作品集需要启用 JavaScript 才能浏览。</div></noscript>

<script>
  /* 加密数据仓（由 Portfolio Builder 生成写入；全局名刻意避开占位符语法） */
  window.PB_VAULT = __VAULT__;
</script>
<script>
"""

_HTML_JS = r"""
(function () {
  'use strict';

  /* =========================================================
     0. 纯前端 SHA-256 / UTF-8 / Base64 工具（与 Python 端严格对齐）
     ========================================================= */
  var K256 = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
  ]);

  function rotr(x, n) { return ((x >>> n) | (x << (32 - n))) >>> 0; }

  function sha256(bytes) {
    var H = new Uint32Array([
      0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19
    ]);
    var len = bytes.length;
    var bitHi = Math.floor(len / 536870912);
    var bitLo = (len * 8) >>> 0;
    var total = (((len + 9 + 63) >> 6) << 6);
    var buf = new Uint8Array(total);
    buf.set(bytes, 0);
    buf[len] = 0x80;
    var dv = new DataView(buf.buffer);
    dv.setUint32(total - 8, bitHi);
    dv.setUint32(total - 4, bitLo);
    var w = new Uint32Array(64);
    var t, off, s0, s1, S0, S1, ch, maj, t1, t2;
    var a, b, c, d, e, f, g, h;
    for (off = 0; off < total; off += 64) {
      for (t = 0; t < 16; t++) { w[t] = dv.getUint32(off + t * 4); }
      for (t = 16; t < 64; t++) {
        s0 = (rotr(w[t - 15], 7) ^ rotr(w[t - 15], 18) ^ (w[t - 15] >>> 3)) >>> 0;
        s1 = (rotr(w[t - 2], 17) ^ rotr(w[t - 2], 19) ^ (w[t - 2] >>> 10)) >>> 0;
        w[t] = (w[t - 16] + s0 + w[t - 7] + s1) >>> 0;
      }
      a = H[0]; b = H[1]; c = H[2]; d = H[3]; e = H[4]; f = H[5]; g = H[6]; h = H[7];
      for (t = 0; t < 64; t++) {
        S1 = (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) >>> 0;
        ch = ((e & f) ^ ((~e) & g)) >>> 0;
        t1 = (h + S1 + ch + K256[t] + w[t]) >>> 0;
        S0 = (rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) >>> 0;
        maj = ((a & b) ^ (a & c) ^ (b & c)) >>> 0;
        t2 = (S0 + maj) >>> 0;
        h = g; g = f; f = e; e = (d + t1) >>> 0;
        d = c; c = b; b = a; a = (t1 + t2) >>> 0;
      }
      H[0] = (H[0] + a) >>> 0; H[1] = (H[1] + b) >>> 0; H[2] = (H[2] + c) >>> 0; H[3] = (H[3] + d) >>> 0;
      H[4] = (H[4] + e) >>> 0; H[5] = (H[5] + f) >>> 0; H[6] = (H[6] + g) >>> 0; H[7] = (H[7] + h) >>> 0;
    }
    var out = new Uint8Array(32);
    var odv = new DataView(out.buffer);
    for (t = 0; t < 8; t++) { odv.setUint32(t * 4, H[t]); }
    return out;
  }

  function bytesToHex(bytes) {
    var hex = '0123456789abcdef';
    var out = '';
    for (var i = 0; i < bytes.length; i++) {
      out += hex.charAt((bytes[i] >> 4) & 15) + hex.charAt(bytes[i] & 15);
    }
    return out;
  }

  function utf8Encode(str) {
    if (window.TextEncoder) { return new TextEncoder().encode(str); }
    var out = [];
    for (var i = 0; i < str.length; i++) {
      var c = str.charCodeAt(i);
      if (c < 0x80) { out.push(c); }
      else if (c < 0x800) { out.push(0xc0 | (c >> 6), 0x80 | (c & 63)); }
      else if (c >= 0xd800 && c <= 0xdbff && i + 1 < str.length) {
        var cp = 0x10000 + ((c - 0xd800) << 10) + (str.charCodeAt(++i) - 0xdc00);
        out.push(0xf0 | (cp >> 18), 0x80 | ((cp >> 12) & 63), 0x80 | ((cp >> 6) & 63), 0x80 | (cp & 63));
      } else { out.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63)); }
    }
    return new Uint8Array(out);
  }

  function utf8Decode(bytes) {
    if (window.TextDecoder) {
      try { return new TextDecoder('utf-8').decode(bytes); } catch (err) { /* 回退手写实现 */ }
    }
    var out = '', i = 0, c, cp;
    while (i < bytes.length) {
      c = bytes[i++];
      if (c < 0x80) { out += String.fromCharCode(c); }
      else if (c >= 0xc0 && c < 0xe0) { out += String.fromCharCode(((c & 31) << 6) | (bytes[i++] & 63)); }
      else if (c >= 0xe0 && c < 0xf0) {
        out += String.fromCharCode(((c & 15) << 12) | ((bytes[i++] & 63) << 6) | (bytes[i++] & 63));
      } else {
        cp = ((c & 7) << 18) | ((bytes[i++] & 63) << 12) | ((bytes[i++] & 63) << 6) | (bytes[i++] & 63);
        cp -= 0x10000;
        out += String.fromCharCode(0xd800 + (cp >> 10), 0xdc00 + (cp & 1023));
      }
    }
    return out;
  }

  function b64ToBytes(b64) {
    var bin = atob(b64);
    var len = bin.length;
    var out = new Uint8Array(len);
    for (var i = 0; i < len; i++) { out[i] = bin.charCodeAt(i); }
    return out;
  }

  /* 与 Python encrypt_payload 完全一致的解密流程 */
  function decryptVault(payloadB64, password) {
    var data = b64ToBytes(payloadB64);
    var state = sha256(utf8Encode(password));
    var out = new Uint8Array(data.length);
    var pos = 0, i;
    while (pos < data.length) {
      state = sha256(state);
      for (i = 0; i < 32 && pos < data.length; i++, pos++) {
        out[pos] = data[pos] ^ state[i];
      }
    }
    return JSON.parse(utf8Decode(out));
  }

  /* =========================================================
     1. 运行状态与 DOM 引用
     ========================================================= */
  var VAULT = window.PB_VAULT || { title: 'PORTFOLIO', hash: '', payload: '', count: 0, generated: '' };

  /* 页面权限：strict = 禁止复制 + 截屏高斯模糊；nocopy = 仅禁止复制；allow = 允许复制 */
  var MODE = VAULT.mode || 'strict';
  var BLUR_ON = MODE === 'strict';
  var COPY_BLOCK = MODE !== 'allow';

  var state = {
    unlocked: false,
    projects: [],
    current: 0,
    view: 'gallery',
    shielding: false,
    switching: false
  };

  var els = {
    gate: document.getElementById('gate'),
    gateInput: document.getElementById('gateInput'),
    gateBtn: document.getElementById('gateBtn'),
    gateMsg: document.getElementById('gateMsg'),
    app: document.getElementById('app'),
    navBtn: document.getElementById('navBtn'),
    drawer: document.getElementById('drawer'),
    drawerList: document.getElementById('drawerList'),
    drawerClose: document.getElementById('drawerClose'),
    drawerMask: document.getElementById('drawerMask'),
    galleryView: document.getElementById('galleryView'),
    worksList: document.getElementById('worksList'),
    detailView: document.getElementById('detailView'),
    dIndex: document.getElementById('dIndex'),
    dTotal: document.getElementById('dTotal'),
    dTitle: document.getElementById('dTitle'),
    dMeta: document.getElementById('dMeta'),
    dIntro: document.getElementById('dIntro'),
    dGallery: document.getElementById('dGallery'),
    nextBtn: document.getElementById('nextBtn'),
    nextTitle: document.getElementById('nextTitle'),
    backBtn: document.getElementById('backBtn'),
    backBtn2: document.getElementById('backBtn2'),
    shield: document.getElementById('shield'),
    shieldBtn: document.getElementById('shieldBtn'),
    shieldHint: document.getElementById('shieldHint'),
    progress: document.getElementById('progress'),
    cursorDot: document.getElementById('cursorDot'),
    cursorRing: document.getElementById('cursorRing')
  };

  var supportsHover = window.matchMedia ? window.matchMedia('(hover:hover) and (pointer:fine)').matches : true;
  var parallaxItems = [];
  var revealObserver = null;
  var lazyObserver = null;

  function pad(n) { return (n < 10 ? '0' : '') + n; }

  /* =========================================================
     2. 解锁流程（未解锁前不渲染任何项目结构与图片）
     ========================================================= */
  function showGateMessage(text, ok) {
    els.gateMsg.textContent = text;
    els.gateMsg.className = 'gate-msg' + (ok ? ' ok' : '');
  }

  function attemptUnlock() {
    var password = els.gateInput.value;
    if (!password) { showGateMessage('请输入查看口令'); return; }
    var hash;
    try {
      hash = bytesToHex(sha256(utf8Encode(password)));
    } catch (err) {
      showGateMessage('当前浏览器不支持所需的加密能力，请改用 Chrome / Edge / Safari 新版。');
      return;
    }
    if (hash !== VAULT.hash) {
      showGateMessage('口令不正确，请重新输入');
      els.gateInput.value = '';
      els.gateInput.focus();
      return;
    }
    showGateMessage('校验通过，正在解密作品数据…', true);
    var data;
    try {
      data = decryptVault(VAULT.payload, password);
    } catch (err) {
      showGateMessage('数据解密失败，请确认口令或联系作者。');
      return;
    }
    state.projects = (data && data.projects) || [];
    state.unlocked = true;
    document.documentElement.classList.add('snap-gallery');
    buildApp();
    setTimeout(function () {
      els.gate.classList.add('hidden');
      document.body.classList.remove('locked');
      els.app.classList.add('ready');
      requestAnimationFrame(function () { sweepDraw(); onScroll(); });
    }, 260);
  }

  /* =========================================================
     3. 构建一级封面流 / 抽屉导航 / 二级详情
     ========================================================= */
  function buildApp() {
    buildGallery();
    buildDrawer();
    buildDetailShell();
    collectParallax();
    sweepDraw();
    updateWorkFade(window.innerHeight || document.documentElement.clientHeight);
  }

  function buildGallery() {
    els.worksList.innerHTML = '';
    state.projects.forEach(function (project, index) {
      var article = document.createElement('article');
      article.className = 'work';
      article.style.transitionDelay = (Math.min(index, 3) * 0.07) + 's';

      var media = document.createElement('div');
      media.className = 'work-media';

      var parallax = document.createElement('div');
      parallax.className = 'parallax';
      parallax.setAttribute('data-amount', String(0.05 + (index % 2) * 0.015));

      var inner = document.createElement('div');
      inner.className = 'media-inner';

      var canvas = document.createElement('canvas');
      canvas.className = 'media-canvas';
      canvas.setAttribute('data-mode', 'contain');
      canvas.setAttribute('data-auto-ratio', 'work');
      canvas.__src = project.cover || null;

      var shield = document.createElement('div');
      shield.className = 'media-shield';

      inner.appendChild(canvas);
      inner.appendChild(shield);
      parallax.appendChild(inner);
      media.appendChild(parallax);

      var info = document.createElement('div');
      info.className = 'work-info';

      var indexLabel = document.createElement('div');
      indexLabel.className = 'work-index';
      indexLabel.textContent = pad(index + 1);

      var title = document.createElement('h2');
      title.className = 'work-title';
      title.textContent = project.title || ('Project ' + pad(index + 1));

      info.appendChild(indexLabel);
      info.appendChild(title);

      if (project.meta) {
        var meta = document.createElement('div');
        meta.className = 'work-meta';
        meta.textContent = project.meta;
        info.appendChild(meta);
      }
      if (project.coverIntro) {
        var intro = document.createElement('p');
        intro.className = 'work-intro';
        intro.textContent = project.coverIntro;
        info.appendChild(intro);
      }

      var cta = document.createElement('div');
      cta.className = 'work-cta';
      var ctaText = document.createElement('span');
      ctaText.textContent = '进入项目';
      var ctaLine = document.createElement('i');
      cta.appendChild(ctaText);
      cta.appendChild(ctaLine);
      info.appendChild(cta);

      media.appendChild(info);
      article.appendChild(media);
      els.worksList.appendChild(article);

      var open = function (event) {
        if (event) { event.preventDefault(); }
        var row = index;
        if (state.shielding) { return; }
        openProject(row);
      };
      media.addEventListener('click', open);
      cta.addEventListener('click', open);

      if (lazyObserver) { lazyObserver.observe(canvas); }
    });
  }

  function buildDrawer() {
    els.drawerList.innerHTML = '';

    var home = document.createElement('button');
    home.className = 'drawer-item drawer-home';
    home.type = 'button';
    home.style.transitionDelay = '0.04s';
    home.innerHTML = '<span class="di-num">—</span>';
    var homeName = document.createElement('span');
    homeName.className = 'di-name';
    homeName.textContent = '返回主画廊 / Main Gallery';
    home.appendChild(homeName);
    home.addEventListener('click', function () { closeDrawer(); goGallery(); });
    els.drawerList.appendChild(home);

    state.projects.forEach(function (project, index) {
      var item = document.createElement('button');
      item.className = 'drawer-item';
      item.type = 'button';
      item.setAttribute('data-index', String(index));
      item.style.transitionDelay = (0.06 + index * 0.035).toFixed(3) + 's';

      var number = document.createElement('span');
      number.className = 'di-num';
      number.textContent = pad(index + 1);

      var name = document.createElement('span');
      name.className = 'di-name';
      name.textContent = project.title || ('Project ' + pad(index + 1));

      item.appendChild(number);
      item.appendChild(name);
      item.addEventListener('click', function () {
        closeDrawer();
        openProject(index, true);
      });
      els.drawerList.appendChild(item);
    });
  }

  function markDrawerActive() {
    var items = els.drawerList.querySelectorAll('.drawer-item');
    for (var i = 0; i < items.length; i++) {
      var index = items[i].getAttribute('data-index');
      if (index !== null && Number(index) === state.current && state.view === 'detail') {
        items[i].classList.add('active');
      } else {
        items[i].classList.remove('active');
      }
    }
  }

  function buildDetailShell() {
    els.backBtn.addEventListener('click', goGallery);
    els.backBtn2.addEventListener('click', goGallery);
    els.nextBtn.addEventListener('click', function () {
      if (!state.projects.length) { return; }
      openProject((state.current + 1) % state.projects.length);
    });
  }
  /* =========================================================
     4. Canvas 渲染：所有图片只存在于内存 Image 对象中
     ========================================================= */
  function isVisible(el) {
    var rect = el.getBoundingClientRect();
    var vw = window.innerWidth || document.documentElement.clientWidth;
    var vh = window.innerHeight || document.documentElement.clientHeight;
    return rect.bottom > -320 && rect.top < vh + 320 && rect.right > -320 && rect.left < vw + 320 &&
           rect.width > 2 && rect.height > 2;
  }

  function lazyLoad(canvas) {
    if (!canvas || !canvas.__src) { return false; }
    var status = canvas.getAttribute('data-state');
    if (status === 'loading' || status === 'loaded') { return false; }
    canvas.setAttribute('data-state', 'loading');
    var img = new Image();
    img.decoding = 'async';
    img.onload = function () {
      canvas.__img = img;
      canvas.setAttribute('data-state', 'loaded');
      var autoRatio = canvas.getAttribute('data-auto-ratio');
      if (autoRatio === '1') {
        /* 二级固定宽度：严格按原图比例，绝不裁剪（长图就让页面变长） */
        var ratio = img.naturalWidth / Math.max(1, img.naturalHeight);
        var inner = canvas.parentNode;
        if (inner) { inner.style.aspectRatio = ratio.toFixed(4); }
      }
      drawCanvas(canvas);
      canvas.classList.add('ready');
      parallaxDirty = true;
      onScroll();
    };
    img.onerror = function () {
      canvas.setAttribute('data-state', 'error');
      canvas.classList.add('ready');
    };
    img.src = 'data:' + ((canvas.__src && canvas.__src.mime) || 'image/jpeg') + ';base64,' + (canvas.__src.data || '');
    return true;
  }

  function drawCanvas(canvas) {
    if (state.shielding) { return; }
    var img = canvas.__img;
    if (!img) { return; }
    if (!canvas.__src) { return; }
    var box = canvas.parentNode;
    var cssW = canvas.clientWidth || (box ? box.clientWidth : 0);
    var cssH = canvas.clientHeight || (box ? box.clientHeight : 0);
    if (cssW < 2 || cssH < 2) { return; }
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    var targetW = Math.max(2, Math.round(cssW * dpr));
    var targetH = Math.max(2, Math.round(cssH * dpr));
    if (canvas.width !== targetW || canvas.height !== targetH) {
      canvas.width = targetW;
      canvas.height = targetH;
    }
    var ctx = canvas.getContext('2d');
    if (!ctx) { return; }
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = '#111113';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    var iw = img.naturalWidth || img.width;
    var ih = img.naturalHeight || img.height;
    if (!iw || !ih) { return; }
    var boxW = canvas.width;
    var boxH = canvas.height;
    var force = canvas.getAttribute('data-mode') || 'auto';
    var imgRatio = iw / ih;
    var boxRatio = boxW / Math.max(1, boxH);
    var deviation = Math.max(imgRatio / boxRatio, boxRatio / imgRatio);
    /* auto：比例差异不大用 cover 满铺；异形比例（尤其长图）自动 contain，保证全图可见 */
    var useContain = force === 'contain' ? true : (force === 'cover' ? false : deviation > 1.14);
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    if (useContain) {
      var backScale = Math.max(boxW / iw, boxH / ih);
      var backW = iw * backScale;
      var backH = ih * backScale;
      var hasFilter = typeof ctx.filter === 'string';
      if (hasFilter) { ctx.filter = 'blur(26px) brightness(0.45) saturate(0.85)'; }
      ctx.drawImage(img, (boxW - backW) / 2, (boxH - backH) / 2, backW, backH);
      if (hasFilter) { ctx.filter = 'none'; }
      else {
        ctx.fillStyle = 'rgba(6,6,7,.74)';
        ctx.fillRect(0, 0, boxW, boxH);
      }
      var scale = Math.min(boxW / iw, boxH / ih);
      var drawW = iw * scale;
      var drawH = ih * scale;
      ctx.drawImage(img, (boxW - drawW) / 2, (boxH - drawH) / 2, drawW, drawH);
    } else {
      var coverScale = Math.max(boxW / iw, boxH / ih);
      var coverW = iw * coverScale;
      var coverH = ih * coverScale;
      ctx.drawImage(img, (boxW - coverW) / 2, (boxH - coverH) / 2, coverW, coverH);
    }
    if (box) {
      if (useContain) { box.classList.add('is-contain'); }
      else { box.classList.remove('is-contain'); }
    }
  }

  function allCanvases() { return document.getElementsByTagName('canvas'); }

  function sweepDraw() {
    if (state.shielding) { return; }
    var list = allCanvases();
    for (var i = 0; i < list.length; i++) {
      var canvas = list[i];
      if (!canvas.__src || !canvas.isConnected) { continue; }
      if (!isVisible(canvas)) { continue; }
      if (canvas.__img) { drawCanvas(canvas); } else { lazyLoad(canvas); }
    }
  }

  function wipeCanvases() {
    var list = allCanvases();
    for (var i = 0; i < list.length; i++) {
      var canvas = list[i];
      var ctx = canvas.getContext('2d');
      if (!ctx) { continue; }
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.fillStyle = '#0a0a0a';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
    }
  }

  function redrawAll() {
    var list = allCanvases();
    for (var i = 0; i < list.length; i++) {
      if (list[i].__img) { drawCanvas(list[i]); }
    }
    sweepDraw();
  }

  /* =========================================================
     5. 视差 / 滚动进度 / 自定义光标
     ========================================================= */
  function collectParallax() {
    /* 为保证「不裁剪」，全面停用视差缩放（缩放必然吃掉画面边缘） */
    parallaxItems = [];
    var nodes = document.querySelectorAll('.parallax');
    for (var i = 0; i < nodes.length; i++) {
      nodes[i].style.transform = '';
    }
  }

  var scrollPending = false;
  var parallaxDirty = false;

  function onScroll() {
    if (scrollPending) { return; }
    scrollPending = true;
    requestAnimationFrame(applyScroll);
  }

  /* 一级项目切换：按面板可见比例淡入淡出（上一屏淡出、下一屏淡入） */
  function updateWorkFade(vh) {
    var works = document.querySelectorAll('.gallery .work');
    for (var i = 0; i < works.length; i++) {
      var el = works[i];
      var rect = el.getBoundingClientRect();
      var h = rect.height || 1;
      var visible = Math.min(rect.bottom, vh) - Math.max(rect.top, 0);
      var ratio = Math.max(0, Math.min(1, visible / Math.min(h, vh)));
      var o = ratio <= 0 ? 0 : Math.pow(ratio, 0.85);
      el.style.opacity = o.toFixed(3);
    }
  }

  function applyScroll() {
    scrollPending = false;
    if (parallaxDirty) { parallaxDirty = false; collectParallax(); }
    var vh = window.innerHeight || document.documentElement.clientHeight;
    var doc = document.documentElement;
    var scrollable = (doc.scrollHeight - vh);
    var ratio = scrollable > 0 ? Math.min(1, Math.max(0, (window.pageYOffset || doc.scrollTop) / scrollable)) : 0;
    if (els.progress) { els.progress.style.width = (ratio * 100).toFixed(2) + '%'; }
    if (els.app) {
      if ((window.pageYOffset || doc.scrollTop) > 40) { els.app.classList.add('scrolled'); }
      else { els.app.classList.remove('scrolled'); }
    }
    updateWorkFade(vh);
    if (state.shielding) { return; }
    for (var i = 0; i < parallaxItems.length; i++) {
      var item = parallaxItems[i];
      if (!item.el.isConnected) { continue; }
      var host = item.host;
      var rect = host.getBoundingClientRect();
      if (rect.bottom < -400 || rect.top > vh + 400) { continue; }
      var h = rect.height || 1;
      var center = rect.top + h / 2 - vh / 2;
      var maxShift = item.amount * (vh / 2 + h / 2);
      var scale = 1 + (2 * maxShift) / h;
      var shift = -center * item.amount;
      item.el.style.transform = 'translate3d(0,' + shift.toFixed(1) + 'px,0) scale(' + scale.toFixed(4) + ')';
    }
  }

  var cursorTarget = { x: window.innerWidth / 2, y: window.innerHeight / 2 };
  var cursorRingPos = { x: cursorTarget.x, y: cursorTarget.y };
  var cursorVisible = false;

  function setupCursor() {
    if (!supportsHover || !els.cursorDot || !els.cursorRing) { return; }
    document.addEventListener('mousemove', function (event) {
      cursorTarget.x = event.clientX;
      cursorTarget.y = event.clientY;
      els.cursorDot.style.transform = 'translate3d(' + event.clientX + 'px,' + event.clientY + 'px,0)';
      if (!cursorVisible) {
        cursorVisible = true;
        els.cursorDot.style.opacity = '1';
        els.cursorRing.style.opacity = '1';
        cursorRingPos.x = event.clientX;
        cursorRingPos.y = event.clientY;
      }
    }, { passive: true });

    document.addEventListener('mouseover', function (event) {
      var target = event.target;
      var interactive = target && target.closest ? target.closest('button, .work-media, .work-cta, input, .drawer-item, .back-btn, .next-btn') : null;
      if (interactive && !state.shielding) { els.cursorRing.classList.add('is-hover'); }
      else { els.cursorRing.classList.remove('is-hover'); }
    }, { passive: true });

    document.addEventListener('mousedown', function () { els.cursorRing.classList.add('is-press'); }, { passive: true });
    document.addEventListener('mouseup', function () { els.cursorRing.classList.remove('is-press'); }, { passive: true });

    function loop() {
      cursorRingPos.x += (cursorTarget.x - cursorRingPos.x) * 0.17;
      cursorRingPos.y += (cursorTarget.y - cursorRingPos.y) * 0.17;
      els.cursorRing.style.transform =
        'translate3d(' + cursorRingPos.x.toFixed(1) + 'px,' + cursorRingPos.y.toFixed(1) + 'px,0)';
      requestAnimationFrame(loop);
    }
    requestAnimationFrame(loop);
  }

  /* =========================================================
     6. 抽屉导航 / 详情视图切换
     ========================================================= */
  function openDrawer() {
    document.body.classList.add('drawer-open');
    els.drawer.setAttribute('aria-hidden', 'false');
    markDrawerActive();
  }

  function closeDrawer() {
    document.body.classList.remove('drawer-open');
    els.drawer.setAttribute('aria-hidden', 'true');
  }

  function toggleDrawer() {
    if (document.body.classList.contains('drawer-open')) { closeDrawer(); }
    else { openDrawer(); }
  }

  function observeReveal(el) {
    if (revealObserver) { revealObserver.observe(el); }
    else { el.classList.add('in'); }
  }

  function renderDetail(index) {
    var project = state.projects[index] || {};
    els.dIndex.textContent = pad(index + 1);
    els.dTotal.textContent = pad(state.projects.length);
    els.dTitle.textContent = project.title || ('Project ' + pad(index + 1));
    els.dMeta.textContent = project.meta || '';
    els.dIntro.textContent = project.detailIntro || '';

    var stale = els.dGallery.querySelectorAll('canvas');
    for (var k = 0; k < stale.length; k++) {
      if (lazyObserver && lazyObserver.unobserve) { lazyObserver.unobserve(stale[k]); }
    }
    els.dGallery.innerHTML = '';

    var images = project.images || [];
    if (!images.length) {
      var empty = document.createElement('div');
      empty.className = 'detail-cap';
      empty.textContent = '该项目暂无二级详情图片';
      els.dGallery.appendChild(empty);
    }

    images.forEach(function (image, i) {
      var item = document.createElement('div');
      item.className = 'detail-item';

      var media = document.createElement('div');
      media.className = 'detail-media';

      var parallax = document.createElement('div');
      parallax.className = 'parallax';
      parallax.setAttribute('data-amount', '0.028');

      var inner = document.createElement('div');
      inner.className = 'media-inner';

      var canvas = document.createElement('canvas');
      canvas.className = 'media-canvas';
      canvas.setAttribute('data-mode', 'cover');
      canvas.setAttribute('data-auto-ratio', '1');
      canvas.__src = image;

      var shield = document.createElement('div');
      shield.className = 'media-shield';

      var zoomHint = document.createElement('span');
      zoomHint.className = 'media-zoom';
      zoomHint.textContent = '查看完整大图';
      shield.appendChild(zoomHint);
      shield.addEventListener('click', function () {
        if (!state.shielding && canvas.__img) { openZoom(canvas.__img); }
      });

      inner.appendChild(canvas);
      inner.appendChild(shield);
      parallax.appendChild(inner);
      media.appendChild(parallax);

      var caption = document.createElement('div');
      caption.className = 'detail-cap';
      caption.textContent = pad(i + 1) + ' / ' + pad(images.length);

      item.appendChild(media);
      item.appendChild(caption);
      els.dGallery.appendChild(item);

      if (lazyObserver) { lazyObserver.observe(canvas); }
      observeReveal(item);
    });

    if (state.projects.length > 1) {
      var next = state.projects[(index + 1) % state.projects.length];
      els.nextTitle.textContent = (next && next.title) || '';
      els.nextBtn.style.display = '';
    } else {
      els.nextTitle.textContent = '';
      els.nextBtn.style.display = 'none';
    }
    collectParallax();
  }

  function openProject(index) {
    if (!state.unlocked || state.switching || state.shielding) { return; }
    if (index < 0 || index >= state.projects.length) { return; }
    document.documentElement.classList.remove('snap-gallery');
    closeZoom();
    closeDrawer();
    state.current = index;
    state.view = 'detail';
    state.switching = true;
    renderDetail(index);
    markDrawerActive();
    document.body.classList.add('view-detail');

    if (els.galleryView.hidden) {
      els.detailView.hidden = false;
      window.scrollTo(0, 0);
      requestAnimationFrame(function () {
        els.detailView.classList.add('show');
        requestAnimationFrame(function () { sweepDraw(); onScroll(); });
      });
      state.switching = false;
      return;
    }

    els.galleryView.classList.add('fade-out');
    window.setTimeout(function () {
      els.galleryView.hidden = true;
      els.galleryView.classList.remove('fade-out');
      window.scrollTo(0, 0);
      els.detailView.hidden = false;
      requestAnimationFrame(function () {
        els.detailView.classList.add('show');
        requestAnimationFrame(function () { sweepDraw(); onScroll(); });
      });
      state.switching = false;
    }, 400);
  }

  function goGallery() {
    if (!state.unlocked || state.switching || state.shielding) { return; }
    document.documentElement.classList.add('snap-gallery');
    closeZoom();
    if (state.view === 'gallery') { closeDrawer(); return; }
    state.view = 'gallery';
    state.switching = true;
    markDrawerActive();
    document.body.classList.remove('view-detail');
    els.detailView.classList.remove('show');
    window.setTimeout(function () {
      els.detailView.hidden = true;
      window.scrollTo(0, 0);
      els.galleryView.hidden = false;
      requestAnimationFrame(function () {
        collectParallax();
        sweepDraw();
        onScroll();
      });
      state.switching = false;
    }, 420);
  }

  /* =========================================================
     7. 分享码校验 / 抽屉 / 遮罩 事件绑定
     ========================================================= */
  function setupGate() {
    els.gateBtn.addEventListener('click', attemptUnlock);
    els.gateInput.addEventListener('keydown', function (event) {
      if (event.key === 'Enter') { event.preventDefault(); attemptUnlock(); }
    });
    window.setTimeout(function () {
      try { els.gateInput.focus(); } catch (err) { /* ignore */ }
    }, 200);
  }

  function setupDrawer() {
    els.navBtn.addEventListener('click', function (event) { event.stopPropagation(); toggleDrawer(); });
    els.drawerClose.addEventListener('click', closeDrawer);
    els.drawerMask.addEventListener('click', closeDrawer);
    document.addEventListener('click', function (event) {
      if (!document.body.classList.contains('drawer-open')) { return; }
      var target = event.target;
      if (target && target.closest && target.closest('#drawer')) { return; }
      if (target && target.closest && target.closest('#navBtn')) { return; }
      closeDrawer();
    });
  }

  function setupObservers() {
    if ('IntersectionObserver' in window) {
      lazyObserver = new IntersectionObserver(function (entries) {
        for (var i = 0; i < entries.length; i++) {
          if (entries[i].isIntersecting) { lazyLoad(entries[i].target); }
        }
      }, { rootMargin: '420px 0px', threshold: 0.01 });

      revealObserver = new IntersectionObserver(function (entries) {
        for (var i = 0; i < entries.length; i++) {
          if (entries[i].isIntersecting) {
            entries[i].target.classList.add('in');
            revealObserver.unobserve(entries[i].target);
          }
        }
      }, { rootMargin: '0px 0px -6% 0px', threshold: 0.08 });
    }
  }

  /* =========================================================
     8. 防截屏：0ms 高斯模糊锁定
     ========================================================= */
  function clearClipboard() {
    function fallback() {
      try {
        var textarea = document.createElement('textarea');
        textarea.value = '';
        textarea.setAttribute('readonly', 'readonly');
        textarea.style.position = 'fixed';
        textarea.style.top = '-1000px';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        textarea.setSelectionRange(0, 0);
        document.execCommand('copy');
        document.body.removeChild(textarea);
      } catch (err) { /* 忽略 */ }
    }
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText('').then(function () { /* 已清空 */ }, fallback);
      } else { fallback(); }
    } catch (err) { fallback(); }
  }

  function shieldNow(reason) {
    if (!BLUR_ON) { return; }
    if (!state.unlocked || state.shielding) { return; }
    closeZoom();
    state.shielding = true;
    document.body.classList.add('shielded');
    els.shield.classList.add('show');
    els.shieldHint.textContent = reason || '';
    if (els.cursorDot) { els.cursorDot.style.opacity = '0'; }
    if (els.cursorRing) { els.cursorRing.style.opacity = '0'; }
    cursorVisible = false;
    wipeCanvases();
    clearClipboard();
    try {
      if (window.getSelection) { window.getSelection().removeAllRanges(); }
    } catch (err) { /* 忽略 */ }
  }

  function shieldLift() {
    if (!state.shielding) { return; }
    if (!document.hasFocus()) {
      els.shieldHint.textContent = '请先点击本页面使其获得焦点，然后再次点击“点击恢复浏览”。';
      try { window.focus(); } catch (err) { /* 忽略 */ }
      return;
    }
    state.shielding = false;
    els.shieldHint.textContent = '';
    els.shield.classList.remove('show');
    document.body.classList.remove('shielded');
    redrawAll();
    onScroll();
  }

  var modifierTimer = null;

  function armModifier() {
    if (modifierTimer) { window.clearTimeout(modifierTimer); }
    modifierTimer = window.setTimeout(function () {
      modifierTimer = null;
      shieldNow('检测到系统级截屏组合键（Meta / Alt 处于按下状态）');
    }, 320);
  }

  function cancelModifier() {
    if (modifierTimer) {
      window.clearTimeout(modifierTimer);
      modifierTimer = null;
    }
  }

  function isTypingTarget(target) {
    if (!target) { return false; }
    var tag = (target.tagName || '').toLowerCase();
    return tag === 'input' || tag === 'textarea' || target.isContentEditable === true;
  }

  function handleKeyEvent(event, phase) {
    if (state.shielding && event.key !== 'Escape' && event.key !== 'Tab') {
      event.preventDefault();
      return;
    }
    var key = event.key || '';
    var code = event.code || '';
    var lower = key.length === 1 ? key.toLowerCase() : key;
    var mod = event.ctrlKey || event.metaKey;
    var shift = event.shiftKey;
    var hit = null;

    /* 截屏 / 开发者工具检测只在「严格」模式生效 */
    if (BLUR_ON) {
      if (key === 'PrintScreen' || code === 'PrintScreen' || event.keyCode === 44) {
        hit = '检测到 PrintScreen 截屏按键';
      } else if (code === 'F12' || key === 'F12') {
        hit = '检测到开发者工具（F12）';
      } else if (event.metaKey && shift && (lower === 's' || code === 'KeyS')) {
        hit = '检测到 Windows 截屏（Win + Shift + S）';
      } else if (event.metaKey && shift && (lower === '3' || lower === '4' || lower === '5')) {
        hit = '检测到 macOS 截屏快捷键（Cmd + Shift + 3 / 4 / 5）';
      } else if (mod && shift && (lower === 'i' || lower === 'j' || lower === 'c' || lower === 'e')) {
        hit = '检测到开发者工具 / 抓取快捷键';
      } else if (mod && lower === 's') {
        hit = '检测到保存网页（Ctrl / Cmd + S）';
      } else if (mod && lower === 'p') {
        hit = '检测到打印 / 导出 PDF（Ctrl / Cmd + P）';
      } else if (mod && lower === 'u') {
        hit = '检测到查看网页源代码';
      } else if (event.ctrlKey && event.altKey && (lower === 'a' || lower === 'x' || lower === 'q')) {
        hit = '检测到第三方截图软件热键';
      } else if (key === 'F1' || code === 'F1') {
        hit = '检测到截图软件热键（F1）';
      } else if (key === 'Meta' || key === 'OS' || key === 'Win') {
        if (phase === 'down' && !event.repeat) { armModifier(); }
        if (phase === 'up') { cancelModifier(); }
        return;
      } else if (key === 'Alt') {
        if (phase === 'down' && !event.repeat) { armModifier(); }
        if (phase === 'up') { cancelModifier(); }
        return;
      }
    }

    if (hit) {
      event.preventDefault();
      event.stopPropagation();
      if (phase === 'down') { shieldNow(hit); }
      return;
    }

    if (phase !== 'down') { return; }
    if (key === 'Escape') {
      if (zoomState.open) { closeZoom(); }
      else if (document.body.classList.contains('drawer-open')) { closeDrawer(); }
      else if (state.view === 'detail') { goGallery(); }
      return;
    }
    if (isTypingTarget(event.target)) { return; }
    if (COPY_BLOCK && event.ctrlKey && !event.shiftKey && (lower === 'a' || lower === 'c')) {
      event.preventDefault();
    }
  }

  /* =========================================================
     8.5 长图 / 完整大图查看层（详情页点击图片放大，可滚动看完整长图）
     ========================================================= */
  var zoomState = { layer: null, stage: null, open: false };

  function setupZoom() {
    zoomState.layer = document.getElementById('zoomLayer');
    zoomState.stage = document.getElementById('zoomStage');
    var closeBtn = document.getElementById('zoomClose');
    if (!zoomState.layer || !zoomState.stage) { return; }
    if (closeBtn) { closeBtn.addEventListener('click', closeZoom); }
    zoomState.layer.addEventListener('click', function (event) {
      if (event.target === zoomState.layer) { closeZoom(); }
    });
  }

  function openZoom(img) {
    if (!img || !zoomState.layer || state.shielding) { return; }
    var iw = img.naturalWidth || img.width;
    var ih = img.naturalHeight || img.height;
    if (!iw || !ih) { return; }
    var cssW = Math.min(window.innerWidth * 0.92, 1240, iw);
    var cssH = cssW * ih / iw;
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    var maxPixels = 12 * 1000 * 1000;   /* 超长图的像素预算，避免爆内存 */
    var w = Math.max(2, Math.round(cssW * dpr));
    var h = Math.max(2, Math.round(cssH * dpr));
    if (w * h > maxPixels) {
      var k = Math.sqrt(maxPixels / (w * h));
      w = Math.max(2, Math.round(w * k));
      h = Math.max(2, Math.round(h * k));
    }
    var canvas = document.createElement('canvas');
    canvas.className = 'zoom-canvas';
    canvas.width = w;
    canvas.height = h;
    canvas.style.width = Math.round(cssW) + 'px';
    var ctx = canvas.getContext('2d');
    if (!ctx) { return; }
    ctx.fillStyle = '#0a0a0a';
    ctx.fillRect(0, 0, w, h);
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(img, 0, 0, w, h);
    zoomState.stage.innerHTML = '';
    zoomState.stage.appendChild(canvas);
    zoomState.layer.classList.add('show');
    zoomState.layer.setAttribute('aria-hidden', 'false');
    zoomState.layer.scrollTop = 0;
    document.body.classList.add('zoom-open');
    zoomState.open = true;
  }

  function closeZoom() {
    if (!zoomState.layer || !zoomState.open) { return; }
    zoomState.open = false;
    zoomState.layer.classList.remove('show');
    zoomState.layer.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('zoom-open');
    zoomState.stage.innerHTML = '';
  }

  function setupProtection() {
    if (MODE === 'allow') { document.body.classList.add('mode-allow'); }

    /* --- 键盘：Esc 等导航始终可用，防护逻辑在内部分模式处理 --- */
    document.addEventListener('keydown', function (event) { handleKeyEvent(event, 'down'); }, true);
    document.addEventListener('keyup', function (event) { handleKeyEvent(event, 'up'); }, true);

    /* --- 复制 / 右键 / 拖拽：strict 与 nocopy 模式禁止 --- */
    if (COPY_BLOCK) {
      document.addEventListener('contextmenu', function (event) { event.preventDefault(); }, true);
      document.addEventListener('dragstart', function (event) { event.preventDefault(); }, true);
      document.addEventListener('drop', function (event) { event.preventDefault(); }, true);
      document.addEventListener('selectstart', function (event) {
        if (!isTypingTarget(event.target)) { event.preventDefault(); }
      }, true);
      document.addEventListener('copy', function (event) {
        if (!isTypingTarget(event.target)) { event.preventDefault(); }
      }, true);
      document.addEventListener('cut', function (event) {
        if (!isTypingTarget(event.target)) { event.preventDefault(); }
      }, true);
    }

    /* --- 截屏模糊：仅 strict 模式 --- */
    if (!BLUR_ON) { return; }

    /* 截屏意图：窗口失焦 */
    window.addEventListener('blur', function () {
      shieldNow('检测到窗口失去焦点（可能正在使用截图工具）');
    });
    document.addEventListener('visibilitychange', function () {
      if (document.hidden) { shieldNow('检测到页面被切换到后台'); }
    });
    window.addEventListener('pagehide', function () {
      shieldNow('检测到页面隐藏');
    });

    /* 截屏意图：鼠标离开视口 */
    document.addEventListener('mouseleave', function () {
      shieldNow('检测到鼠标离开页面视口');
    });
    document.addEventListener('mouseout', function (event) {
      if (!event.relatedTarget && !event.toElement) {
        shieldNow('检测到鼠标离开页面视口');
      }
    });

    /* 打印 / 另存 */
    window.addEventListener('beforeprint', function () {
      shieldNow('检测到打印 / 导出 PDF 行为');
    });
    document.addEventListener('keydown', function (event) {
      if ((event.ctrlKey || event.metaKey) && (event.key === 'p' || event.key === 'P')) {
        event.preventDefault();
      }
    }, true);

    /* 兜底巡检：焦点丢失 */
    window.setInterval(function () {
      if (!state.unlocked || state.shielding) { return; }
      if (document.visibilityState !== 'visible') { return; }
      if (!document.hasFocus()) { shieldNow('检测到窗口未激活'); }
    }, 500);

    /* 恢复按钮 */
    els.shieldBtn.addEventListener('click', function () {
      try { window.focus(); } catch (err) { /* 忽略 */ }
      window.setTimeout(shieldLift, 40);
    });
  }

  /* =========================================================
     9. 初始化
     ========================================================= */
  function init() {
    setupObservers();
    setupGate();
    setupDrawer();
    setupProtection();
    setupZoom();
    setupCursor();

    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', function () {
      window.clearTimeout(init._resizeTimer);
      init._resizeTimer = window.setTimeout(function () {
        collectParallax();
        sweepDraw();
        onScroll();
      }, 200);
    });
    window.addEventListener('orientationchange', function () {
      window.setTimeout(function () { collectParallax(); sweepDraw(); }, 260);
    });

    if (!VAULT.payload) {
      showGateMessage('未检测到加密数据，请重新生成网页。');
    }
    document.title = VAULT.title || document.title;
  }

  init();
})();
"""

_HTML_DOC_END = r"""</script>
</body>
</html>
"""

HTML_TEMPLATE = _HTML_DOC_TOP + _HTML_CSS + _HTML_BODY + _HTML_JS + _HTML_DOC_END


# ============================================================================
# 6. 程序入口
# ============================================================================


def make_app_icon(size: int = 512) -> "QPixmap":
    """用代码绘制应用图标（不依赖任何外部资源，便于 PyInstaller 单文件打包）。"""
    from PyQt5.QtCore import QRectF
    from PyQt5.QtGui import (
        QBrush,
        QColor,
        QLinearGradient,
        QPainter,
        QPainterPath,
        QPen,
        QPixmap,
    )

    size = max(16, int(size))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)

    # 底板：近黑圆角方块 + 白色细描边
    radius = size * 0.225
    plate = QPainterPath()
    plate.addRoundedRect(QRectF(size * 0.02, size * 0.02, size * 0.96, size * 0.96), radius, radius)
    plate_gradient = QLinearGradient(0, 0, 0, size)
    plate_gradient.setColorAt(0.0, QColor("#1a1a1f"))
    plate_gradient.setColorAt(1.0, QColor("#08080a"))
    painter.fillPath(plate, QBrush(plate_gradient))
    pen = QPen(QColor(255, 255, 255, 110))
    pen.setWidthF(max(1.0, size * 0.008))
    painter.setPen(pen)
    painter.drawPath(plate)

    # 白色 P 字标（与生成网页的 favicon 使用同一套路径，坐标系 64×64）
    glyph = QPainterPath()
    glyph.moveTo(18.0, 44.0)
    glyph.lineTo(18.0, 20.0)
    glyph.lineTo(28.0, 20.0)
    glyph.cubicTo(34.0, 20.0, 37.0, 23.0, 37.0, 28.0)
    glyph.cubicTo(37.0, 33.0, 34.0, 36.0, 28.0, 36.0)
    glyph.lineTo(24.0, 36.0)
    glyph.lineTo(24.0, 44.0)
    glyph.closeSubpath()

    painter.save()
    painter.translate(size / 2.0, size / 2.0)
    painter.scale(size / 52.0, size / 52.0)
    painter.translate(-27.5, -32.0)
    gold = QLinearGradient(18, 20, 37, 44)
    gold.setColorAt(0.0, QColor("#ffffff"))
    gold.setColorAt(1.0, QColor("#cfcfcf"))
    painter.fillPath(glyph, QBrush(gold))
    painter.restore()
    painter.end()
    return pixmap


def pick_ui_font() -> "QFont":
    """按平台挑选一个可用中文字体（Windows / macOS / Linux 通用）。"""
    from PyQt5.QtGui import QFontDatabase

    try:
        families = set(QFontDatabase().families())
    except Exception:  # noqa: BLE001
        families = set()
    candidates = (
        "Microsoft YaHei UI",
        "Microsoft YaHei",
        "PingFang SC",
        "Hiragino Sans GB",
        "Noto Sans CJK SC",
        "Source Han Sans SC",
        "Segoe UI",
        "Helvetica Neue",
    )
    for name in candidates:
        if name in families:
            font = QFont(name)
            font.setPointSize(10 if sys.platform == "darwin" else 9)
            return font
    return QFont()


def main() -> None:
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    try:
        app.setWindowIcon(QIcon(make_app_icon(256)))
    except Exception:  # noqa: BLE001 - 图标失败不影响主流程
        pass
    app.setFont(pick_ui_font())
    window = PortfolioBuilder()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

# ============================================================================
# 运行说明
# ----------------------------------------------------------------------------
# 1) 安装依赖（仅需 PyQt5，其余全部为标准库）：
#        pip install PyQt5
#    建议 Python 3.8 ~ 3.13（PyQt5 官方 wheel 覆盖面最广）。
#
# 2) 启动桌面工具：
#        python portfolio_builder.py
#
# 3) 使用步骤：
#    ① 顶部填写「作品集主标题 / 副标题」，并设置「查看口令」（必填，网页解锁 + 图片解密用）；
#    ② 左侧「批量创建」或「新增项目」建立项目，可用鼠标拖拽或上移/下移调整顺序；
#    ③ 选中项目后在右侧填写：项目名称、副标签、封面文案、封面图（单选）；
#       以及二级详情文案与多张详情图（支持多选添加、拖拽排序、上下移、删除）；
#    ④ 点击「一键生成 HTML 网页」，选择保存路径，等待进度条完成；
#    ⑤ 弹窗中选择「立即打开预览」即可用浏览器查看（可直接双击 html 文件，无需服务器）。
#
# 4) 说明：
#    · 生成的 .html 是单文件，图片已全部 Base64 内嵌，任何设备打开都无需外部资源；
#    · 网页默认全屏毛玻璃锁定层，未解锁前 DOM 中不存在任何项目结构；
#    · 口令先用 SHA-256 校验，再用「口令派生密钥的 SHA-256 链式密钥流 XOR + Base64」解密，
#      Python 端加密与前端 JS 解密算法完全一致，中文 / emoji 均无乱码；
#    · 工程可通过「保存工程 / 打开工程」复用（保存的是图片路径与文案，不包含图片二进制）；
#    · 若需调整防截屏灵敏度（例如单独按 Alt / Meta 的预警），可在生成的 HTML 中修改
#      armModifier() 里的 320ms 阈值，或在 setupProtection() 中移除对应监听。
# ============================================================================
