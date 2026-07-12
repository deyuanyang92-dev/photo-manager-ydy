"""project_settings_dialog.py — 在**项目根**(容器, 非工作区)上编辑项目级设置。

需求场景(用户 2026-07-12):
    "每个项目、子项目或工作区, 可以设计一些采集人、采集时间、坐标、经纬度、拍摄场地等信息吗,
     方便主界面右侧可以自动读取, 减少每次拍照都要填写?"

能力其实早就有了 —— project_settings_service.get_effective() 沿目录树向上继承(近的祖先赢),
项目根设一次, 下面所有断面/采样点自动继承, effective_new_specimen_prefill() 把它喂进工作台
右栏。**缺的是入口**: 项目根是容器(_data/region.json, 不是拍照工作区), 而 ProjectSettingsDrawer
只挂在工作台上、工作台又要求当前是工作区 —— 于是项目根的设置**根本没有 UI 可以编辑**。
这正是旧「新建项目」对话框非要一次问完 6 个字段的原因(它是唯一的机会)。

本模块补上那个入口: 项目树里右键项目 →「项目设置…」。没有它, 新建项目对话框砍掉的字段
就永远设不了 —— 所以这是整条链路的**前提条件**, 不是可选项。

详见 docs/specs/2026-07-12-slim-new-project-and-settings-inheritance.md §3.4
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import QDialog, QVBoxLayout, QWidget

from app.config.i18n import tr


class RootSettingsCtx:
    """把 ProjectSettingsDrawer 指向任意项目目录的轻量 ctx 代理。

    抽屉全程读 ``self.ctx.get_db()`` / ``self.ctx.current_project_dir``(10+ 处), 绑的是
    **当前工作区**。这里只覆盖这两处指向 *project_dir*, 其余属性(settings / collab_service /
    edit_unlocked …)一律 ``__getattr__`` 委托给真 ctx。

    **私有连接 + close() 是红线**: ``AppContext.get_db()`` 走 ``open_project_db()``, 那是
    **带缓存**的 —— 缓存连接会持有 ``_data/project.db`` 的文件锁直到进程退出, Windows 上
    项目文件夹就删不掉 / 移不动(历史 shutdown-lock bug)。故这里用 ``open_project_db_private``,
    对话框一关立刻 close。
    """

    def __init__(self, real_ctx, project_dir: str) -> None:
        self._real = real_ctx
        self._dir = str(project_dir)
        self._db: Optional[sqlite3.Connection] = None

    @property
    def current_project_dir(self) -> str:
        return self._dir

    @property
    def project_root(self) -> str:
        # 项目根自己就是继承链的顶 —— 抽屉里的 get_effective 走到这里为止。
        return self._dir

    def get_db(self, project_dir: Optional[str] = None) -> Optional[sqlite3.Connection]:
        from app.db.db_manager import open_project_db_private

        if self._db is None:
            self._db = open_project_db_private(project_dir or self._dir, create=True)
        return self._db

    def close(self) -> None:
        """提交并放锁。幂等 —— 关两次不抛。"""
        if self._db is None:
            return
        db, self._db = self._db, None
        try:
            db.commit()
        except sqlite3.Error:
            pass
        finally:
            db.close()

    def __getattr__(self, name: str):
        # 只在本类没有该属性时触发 —— settings / collab_service / edit_unlocked 等照旧委托。
        return getattr(self._real, name)


def open_project_settings_dialog(
    parent: Optional[QWidget], ctx, project_dir: str
) -> None:
    """在 *project_dir*(通常是项目根)上开设置抽屉(模态)。用完必关库, 不留文件锁。"""
    from app.widgets.project_settings_drawer import ProjectSettingsDrawer

    proxy = RootSettingsCtx(ctx, project_dir)
    dlg = QDialog(parent)
    dlg.setWindowTitle(f"{tr('项目设置')} — {Path(project_dir).name}")
    dlg.setMinimumSize(460, 640)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(0, 0, 0, 0)

    drawer = ProjectSettingsDrawer(proxy, parent=dlg)
    drawer.refresh()
    drawer.show()  # 抽屉在工作台里是 overlay(默认 hide()), 嵌进对话框要显式 show
    drawer.closed.connect(dlg.accept)
    lay.addWidget(drawer)

    try:
        dlg.exec()
    finally:
        proxy.close()  # 红线: 立刻放锁(Windows 上不放锁 → 项目文件夹移不动/删不掉)
