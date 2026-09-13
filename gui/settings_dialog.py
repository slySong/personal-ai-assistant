"""设置对话框：DeepSeek API 地址、模型、温度、沙箱、超时等。

保存后更新 AppConfig 并持久化到 config.json。
模型切换会触发主窗口重建 Agent。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from config import AppConfig, LLMConfig
from core.llm_client import LLMClient


class SettingsDialog(QDialog):
    """应用设置对话框。"""

    def __init__(self, app_cfg: AppConfig, llm_cfg: LLMConfig, parent=None):
        super().__init__(parent)
        self.app_cfg = app_cfg
        self.llm_cfg = llm_cfg
        self.setWindowTitle("设置")
        self.setMinimumWidth(480)
        self._init_ui()
        self._load_values()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()

        # API 地址
        self.url_edit = QLineEdit()
        form.addRow("API 地址：", self.url_edit)

        # API Key（DeepSeek 等云端服务需要）
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("请填写 DeepSeek API Key")
        form.addRow("API Key：", self.key_edit)

        # 模型选择（下拉 + 刷新）
        model_row = QHBoxLayout()
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self._refresh_models)
        model_row.addWidget(self.model_combo, stretch=1)
        model_row.addWidget(refresh_btn)
        model_container = QWidget()
        model_container.setLayout(model_row)
        form.addRow("模型：", model_container)

        # 温度
        self.temp_spin = QDoubleSpinBox()
        self.temp_spin.setRange(0.0, 2.0)
        self.temp_spin.setSingleStep(0.1)
        self.temp_spin.setDecimals(2)
        form.addRow("温度（temperature）：", self.temp_spin)

        # 最大 token
        self.max_tokens_spin = QSpinBox()
        self.max_tokens_spin.setRange(512, 32768)
        self.max_tokens_spin.setSingleStep(512)
        form.addRow("最大输出 token：", self.max_tokens_spin)

        # 沙箱目录
        self.sandbox_edit = QLineEdit()
        form.addRow("沙箱目录：", self.sandbox_edit)

        # 执行超时
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(5, 300)
        self.timeout_spin.setSuffix(" 秒")
        form.addRow("代码执行超时：", self.timeout_spin)

        # LLM 提取间隔
        self.extract_spin = QSpinBox()
        self.extract_spin.setRange(1, 50)
        self.extract_spin.setSuffix(" 轮")
        form.addRow("LLM 偏好提取间隔：", self.extract_spin)

        # 可信模式
        self.trusted_check = QCheckBox("允许绝对路径访问，写文件/执行命令不再弹窗确认（谨慎开启）")
        form.addRow("可信模式：", self.trusted_check)

        layout.addLayout(form)

        # 状态标签（显示刷新模型结果）
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        # 按钮
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _load_values(self) -> None:
        self.url_edit.setText(self.llm_cfg.base_url)
        self.key_edit.setText(self.llm_cfg.api_key)
        self.temp_spin.setValue(self.llm_cfg.temperature)
        self.max_tokens_spin.setValue(self.llm_cfg.max_tokens)
        self.sandbox_edit.setText(self.app_cfg.sandbox.root_dir)
        self.timeout_spin.setValue(self.app_cfg.sandbox.exec_timeout)
        self.extract_spin.setValue(self.app_cfg.memory.llm_extract_interval)
        self.trusted_check.setChecked(self.app_cfg.sandbox.trusted_mode)
        self._refresh_models(select_current=self.llm_cfg.model)

    def _refresh_models(self, select_current: Optional[str] = None) -> None:
        """从后端 API 拉取模型列表填充下拉。"""
        self.status_label.setText("正在拉取模型列表...")
        self.status_label.setStyleSheet("color: gray;")
        try:
            client = LLMClient(LLMConfig(
                base_url=self.url_edit.text().strip(),
                api_key=self.key_edit.text().strip(),
            ))
            models = client.list_models()
            self.model_combo.clear()
            if models:
                self.model_combo.addItems(models)
                self.status_label.setText(f"✓ 找到 {len(models)} 个模型")
                self.status_label.setStyleSheet("color: green;")
            else:
                self.model_combo.addItem(self.llm_cfg.model)
                self.status_label.setText("⚠ 未找到模型，请检查 API 地址 / Key")
                self.status_label.setStyleSheet("color: #cc8800;")
            if select_current:
                idx = self.model_combo.findText(select_current)
                if idx >= 0:
                    self.model_combo.setCurrentIndex(idx)
                else:
                    self.model_combo.setEditText(select_current)
        except Exception as e:
            self.status_label.setText(f"✗ 拉取失败：{e}")
            self.status_label.setStyleSheet("color: red;")

    def _on_save(self) -> None:
        """保存设置到 AppConfig 并持久化。"""
        self.llm_cfg.base_url = self.url_edit.text().strip() or self.llm_cfg.base_url
        self.llm_cfg.api_key = self.key_edit.text().strip()
        self.llm_cfg.model = self.model_combo.currentText().strip() or self.llm_cfg.model
        self.llm_cfg.temperature = self.temp_spin.value()
        self.llm_cfg.max_tokens = self.max_tokens_spin.value()

        self.app_cfg.llm.base_url = self.llm_cfg.base_url
        self.app_cfg.llm.api_key = self.llm_cfg.api_key
        self.app_cfg.llm.model = self.llm_cfg.model
        self.app_cfg.llm.temperature = self.llm_cfg.temperature
        self.app_cfg.llm.max_tokens = self.llm_cfg.max_tokens

        self.app_cfg.sandbox.root_dir = self.sandbox_edit.text().strip()
        self.app_cfg.sandbox.exec_timeout = self.timeout_spin.value()
        self.app_cfg.sandbox.trusted_mode = self.trusted_check.isChecked()
        self.app_cfg.memory.llm_extract_interval = self.extract_spin.value()

        self.app_cfg.save()
        self.accept()
