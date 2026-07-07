"""Naming-rule table workflow for ProjectSettingsDrawer."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QPushButton, QWidget


class ProjectSettingsNamingMixin:
    def _custom_naming_fields(self) -> list[dict[str, str]]:
        editor = getattr(self, "_naming_custom_fields", None)
        if editor is None:
            return []
        try:
            return editor.fields()
        except Exception:
            return []

    def _rebuild_naming_unified_table(
        self,
        checked_required: dict[str, bool] | None = None,
        checked_components: set[str] | None = None,
    ) -> None:
        """Single pass: build the merged 字段 / 必填 / 参与编号 / 顺序 table."""
        from app.services.naming_field_catalog import (
            all_fields,
            component_fields,
            required_fields,
            field_label,
        )

        if checked_required is None:
            checked_required = {
                key: cb.isChecked()
                for key, cb in self._naming_required_checks.items()
            }
        if checked_components is None:
            checked_components = {
                key for key, cb in self._naming_component_checks.items()
                if cb.isChecked()
            }

        custom_fields = self._custom_naming_fields()
        catalog_keys = [f.key for f in component_fields(custom_fields)]
        order = list(getattr(self, "_naming_component_order", []) or catalog_keys)
        self._naming_component_order = [
            k for k in order if k in catalog_keys or k in checked_components
        ]
        for key in catalog_keys:
            if key not in self._naming_component_order:
                self._naming_component_order.append(key)

        req_keys = {f.key for f in required_fields(custom_fields)}
        comp_keys = set(catalog_keys)

        # Display order: component-ordered fields first, then required-only, then rest
        comp_set = set(self._naming_component_order)
        req_only = [f.key for f in all_fields(custom_fields) if f.key not in comp_set and f.key in req_keys]
        all_keys_ordered = list(self._naming_component_order) + req_only

        field_map = {f.key: f for f in all_fields(custom_fields)}
        last_comp_idx = len(self._naming_component_order) - 1

        lay = self._naming_unified_rows_lay
        while lay.count():
            item = lay.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

        self._naming_required_checks = {}
        self._naming_component_checks = {}

        for key in all_keys_ordered:
            f = field_map.get(key)
            if f is None:
                continue
            is_req = key in req_keys
            is_comp = key in comp_keys
            comp_pos = self._naming_component_order.index(key) if key in comp_set else -1

            row = QWidget()
            row_lay = QHBoxLayout(row)
            row_lay.setContentsMargins(2, 1, 2, 1)
            row_lay.setSpacing(0)

            lbl = QLabel(f.label)
            row_lay.addWidget(lbl, 1)

            # 必填 column (52 px)
            req_cell = QWidget()
            req_cell.setFixedWidth(52)
            rc_lay = QHBoxLayout(req_cell)
            rc_lay.setContentsMargins(0, 0, 0, 0)
            rc_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if is_req:
                cb_req = QCheckBox()
                cb_req.setChecked(bool(checked_required.get(key, f.default_required)))
                cb_req.stateChanged.connect(self._save_naming_rules)
                self._naming_required_checks[key] = cb_req
                rc_lay.addWidget(cb_req)
            else:
                dash = QLabel("—")
                dash.setObjectName("Muted")
                dash.setAlignment(Qt.AlignmentFlag.AlignCenter)
                rc_lay.addWidget(dash)
            row_lay.addWidget(req_cell)

            # 参与编号 column (68 px)
            comp_cell = QWidget()
            comp_cell.setFixedWidth(68)
            cc_lay = QHBoxLayout(comp_cell)
            cc_lay.setContentsMargins(0, 0, 0, 0)
            cc_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if is_comp:
                cb_comp = QCheckBox()
                cb_comp.setChecked(key in checked_components)
                cb_comp.stateChanged.connect(self._save_naming_rules)
                self._naming_component_checks[key] = cb_comp
                cc_lay.addWidget(cb_comp)
            else:
                dash2 = QLabel("—")
                dash2.setObjectName("Muted")
                dash2.setAlignment(Qt.AlignmentFlag.AlignCenter)
                cc_lay.addWidget(dash2)
            row_lay.addWidget(comp_cell)

            # 顺序 column (56 px): only for component fields
            ord_cell = QWidget()
            ord_cell.setFixedWidth(56)
            oc_lay = QHBoxLayout(ord_cell)
            oc_lay.setContentsMargins(0, 0, 0, 0)
            oc_lay.setSpacing(2)
            if is_comp and comp_pos >= 0:
                up_btn = QPushButton("↑")
                up_btn.setObjectName("Ghost")
                up_btn.setFixedSize(24, 22)
                up_btn.setEnabled(comp_pos > 0)
                up_btn.clicked.connect(lambda _=False, k=key: self._move_naming_component(k, -1))
                down_btn = QPushButton("↓")
                down_btn.setObjectName("Ghost")
                down_btn.setFixedSize(24, 22)
                down_btn.setEnabled(comp_pos < last_comp_idx)
                down_btn.clicked.connect(lambda _=False, k=key: self._move_naming_component(k, 1))
                oc_lay.addWidget(up_btn)
                oc_lay.addWidget(down_btn)
            row_lay.addWidget(ord_cell)

            lay.addWidget(row)

    def _rebuild_naming_required_rows(self, checked_required: dict[str, bool] | None = None) -> None:
        self._rebuild_naming_unified_table(checked_required=checked_required)

    def _rebuild_naming_component_rows(self, checked_components: set[str] | None = None) -> None:
        self._rebuild_naming_unified_table(checked_components=checked_components)

    def _move_naming_component(self, key: str, delta: int) -> None:
        try:
            idx = self._naming_component_order.index(key)
        except ValueError:
            return
        new_idx = idx + delta
        if new_idx < 0 or new_idx >= len(self._naming_component_order):
            return
        self._naming_component_order[idx], self._naming_component_order[new_idx] = (
            self._naming_component_order[new_idx],
            self._naming_component_order[idx],
        )
        self._rebuild_naming_unified_table()
        self._save_naming_rules()

    def _toggle_naming_advanced(self) -> None:
        body = getattr(self, "_naming_advanced_body", None)
        btn = getattr(self, "_naming_advanced_btn", None)
        if body is None or btn is None:
            return
        visible = not body.isVisible()
        body.setVisible(visible)
        btn.setText("收起高级命名设置" if visible else "展开高级命名设置")
