"""记忆面板：显示/编辑/删除用户偏好。

右栏组件，实时反映 MemoryStore 中的偏好。
支持手动编辑值、删除偏好、刷新。
偏好变化时发 changed 信号通知主窗口。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.memory import MemoryStore, Preference, KEY_LABELS


class MemoryPanel(QWidget):
    """偏好记忆面板。"""

    changed = Signal()  # 偏好增删改后发射

    def __init__(self, memory_store: MemoryStore, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.memory = memory_store
        self._init_ui()
        self.refresh()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        layout.addWidget(QLabel("用户偏好"))

        self.list_widget = QListWidget()
        self.list_widget.setToolTip("双击编辑，右键删除")
        self.list_widget.itemDoubleClicked.connect(self._edit_selected)
        layout.addWidget(self.list_widget, stretch=1)

        # 按钮行
        btn_row = QHBoxLayout()
        edit_btn = QPushButton("编辑")
        edit_btn.clicked.connect(self._edit_selected)
        del_btn = QPushButton("删除")
        del_btn.clicked.connect(self._delete_selected)
        add_btn = QPushButton("添加")
        add_btn.clicked.connect(self._add_manual)
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self.refresh)
        btn_row.addWidget(edit_btn)
        btn_row.addWidget(del_btn)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(refresh_btn)
        layout.addLayout(btn_row)

    def refresh(self) -> None:
        """从 MemoryStore 重新加载偏好列表。"""
        self.list_widget.clear()
        prefs = self.memory.get_all_active()
        if not prefs:
            item = QListWidgetItem("（暂无偏好）")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.list_widget.addItem(item)
            return
        for p in prefs:
            label = KEY_LABELS.get(p.key, p.key)
            text = f"{label}：{p.value}"
            sub = f"  [{p.source}, 置信度 {p.confidence:.2f}, 命中 {p.hit_count}次]"
            item = QListWidgetItem(text + sub)
            item.setData(Qt.ItemDataRole.UserRole, p.key)
            self.list_widget.addItem(item)

    def _edit_selected(self, *_args) -> None:
        item = self.list_widget.currentItem()
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        if not key:
            return
        prefs = {p.key: p for p in self.memory.get_all_active()}
        p = prefs.get(key)
        if not p:
            return
        new_value, ok = QInputDialog.getText(
            self, "编辑偏好", f"修改 {KEY_LABELS.get(key, key)}（{key}）的值：",
            text=p.value,
        )
        if ok and new_value.strip():
            self.memory.update_value(key, new_value.strip())
            self.refresh()
            self.changed.emit()

    def _delete_selected(self) -> None:
        item = self.list_widget.currentItem()
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        if not key:
            return
        reply = QMessageBox.question(
            self, "删除偏好",
            f"确定删除偏好 '{KEY_LABELS.get(key, key)}' 吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.memory.delete(key)
            self.refresh()
            self.changed.emit()

    def _add_manual(self) -> None:
        """手动添加一条偏好。"""
        key, ok = QInputDialog.getText(
            self, "添加偏好", "偏好键名（如 name / occupation / primary_language）："
        )
        if not ok or not key.strip():
            return
        key = key.strip()
        value, ok = QInputDialog.getText(
            self, "添加偏好", f"{KEY_LABELS.get(key, key)} 的值："
        )
        if not ok or not value.strip():
            return
        self.memory.upsert(Preference(
            key=key,
            value=value.strip(),
            category="preference",
            confidence=0.9,
            source="manual",
        ))
        self.refresh()
        self.changed.emit()
