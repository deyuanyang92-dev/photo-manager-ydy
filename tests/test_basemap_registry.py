"""test_basemap_registry.py — 底图注册表：发现底图 + 校准 sidecar + EPS 栅格化.

采集地图出版底图来源：用户目录（默认 `地图/`）的图片/EPS + 随包栅格 + OSM 交互 + 生成投影。
每张图片底图的控制点校准存为图片旁 `<image>.calib.json`。纯文件系统逻辑，无 Qt。

Run:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_basemap_registry.py -v
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app.services import basemap_registry as br


# ── 发现底图 ───────────────────────────────────────────────────────────────────

class TestDiscovery:
    def test_lists_user_images(self, tmp_path: Path):
        (tmp_path / "世界地图.jpg").write_bytes(b"\xff\xd8\xff")   # 假 jpg 头
        (tmp_path / "中国地图.eps").write_text("%!PS")
        (tmp_path / "readme.txt").write_text("ignore")            # 非图片忽略
        entries = br.list_user_basemaps(tmp_path)
        names = {e["name"] for e in entries}
        assert "世界地图" in names
        assert "中国地图" in names
        assert all(e["kind"] == "image" for e in entries)
        assert not any("readme" in e["name"] for e in entries)

    def test_osm_entry_always_present(self):
        entries = br.list_basemaps(user_dir=None)
        assert any(e["kind"] == "osm" for e in entries)

    def test_default_map_dir_is_repo_dir(self):
        d = br.default_map_dir()
        assert d.name == "地图"

    def test_unsupported_dir_returns_empty(self, tmp_path: Path):
        assert br.list_user_basemaps(tmp_path / "nope") == []


# ── 校准 sidecar ───────────────────────────────────────────────────────────────

class TestCalibration:
    def test_save_then_load_round_trip(self, tmp_path: Path):
        img = tmp_path / "m.jpg"
        img.write_bytes(b"x")
        model = {"order": 1, "cx": [1.0, 2.0, 0.0], "cy": [0.0, 0.0, 3.0], "rms_px": 0.0}
        cps = [[0, 0, 1, 0], [10, 0, 21, 0], [0, 10, 1, 30]]
        br.save_calibration(img, model, cps)
        loaded = br.load_calibration(img)
        assert loaded is not None
        assert loaded["model"] == model
        assert loaded["control_points"] == cps

    def test_load_missing_returns_none(self, tmp_path: Path):
        assert br.load_calibration(tmp_path / "absent.jpg") is None

    def test_is_calibrated(self, tmp_path: Path):
        img = tmp_path / "m.png"
        img.write_bytes(b"x")
        assert br.is_calibrated(img) is False
        br.save_calibration(img, {"order": 1, "cx": [0, 0, 0], "cy": [0, 0, 0],
                                  "rms_px": 0.0}, [])
        assert br.is_calibrated(img) is True

    def test_calib_path_alongside_image(self, tmp_path: Path):
        img = tmp_path / "world.jpg"
        p = br.calib_path(img)
        assert p.parent == tmp_path
        assert p.name.startswith("world")
        assert p.suffix == ".json"


# ── EPS 栅格化 ─────────────────────────────────────────────────────────────────

class TestEps:
    def test_gs_command_shape(self):
        cmd = br._gs_command(Path("/a/x.eps"), Path("/a/x.png"), dpi=200)
        assert cmd[0].endswith("gs") or "gs" in cmd[0]
        assert "-r200" in cmd
        assert any(str(a).endswith("x.png") for a in cmd)
        assert any(str(a).endswith("x.eps") for a in cmd)

    def test_rasterize_missing_gs_returns_none(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(br.shutil, "which", lambda _name: None)
        eps = tmp_path / "x.eps"
        eps.write_text("%!PS")
        assert br.rasterize_eps(eps) is None

    @pytest.mark.skipif(shutil.which("gs") is None, reason="ghostscript 未安装")
    def test_resolve_image_path_eps_rasterizes(self, tmp_path: Path):
        # 用仓库真实 EPS（若存在）验证栅格化产出 PNG
        repo_eps = next(iter(br.default_map_dir().glob("*.eps")), None)
        if repo_eps is None:
            pytest.skip("仓库 地图/ 无 EPS")
        out = br.rasterize_eps(repo_eps, cache_dir=tmp_path, dpi=72)
        assert out is not None and Path(out).exists()
        assert Path(out).stat().st_size > 0


# ── 按图名(stem)去重 + 内置发现 + 缓存改道 ──────────────────────────────────────

class TestStemDedup:
    def test_eps_and_jpg_same_stem_merge(self, tmp_path: Path):
        (tmp_path / "世界地图.eps").write_text("%!PS")
        (tmp_path / "世界地图.jpg").write_bytes(b"\xff\xd8\xff")
        entries = br.list_user_basemaps(tmp_path)
        wm = [e for e in entries if e["name"] == "世界地图"]
        assert len(wm) == 1                      # 合成一条
        e = wm[0]
        assert e["source"].endswith(".jpg")      # 显示用栅格
        assert e["vector"].endswith(".eps")      # 矢量母版留作高 DPI 导出

    def test_only_eps_uses_eps_as_source(self, tmp_path: Path):
        (tmp_path / "中国地图.eps").write_text("%!PS")
        e = br.list_user_basemaps(tmp_path)[0]
        assert e["name"] == "中国地图"
        assert e["source"].endswith(".eps")
        assert e["vector"].endswith(".eps")

    def test_bundled_basemaps_discovered(self):
        # 仓库已内置 世界地图 / 中国地图（resources/geo/basemaps/）
        names = {e["name"] for e in br.list_bundled_basemaps()}
        assert "世界地图" in names
        assert "中国地图" in names
        assert len(names) == 2                    # eps+jpg 各合一条

    def test_list_basemaps_dedupes_across_sources(self, tmp_path: Path):
        # 用户目录也放同名 世界地图 → 不应与内置重复出现两次
        (tmp_path / "世界地图.jpg").write_bytes(b"\xff\xd8\xff")
        entries = br.list_basemaps(user_dir=tmp_path)
        wm = [e for e in entries if e["name"] == "世界地图"]
        assert len(wm) == 1


class TestCacheLocation:
    def test_default_cache_dir_in_tempdir(self):
        import tempfile
        d = br._default_cache_dir()
        assert str(d).startswith(tempfile.gettempdir())

    def test_resolve_raster_no_rasterize(self, tmp_path: Path):
        # 栅格底图直接返回自身，不产生缓存文件
        img = tmp_path / "m.png"
        from PIL import Image
        Image.new("RGB", (8, 8), (1, 2, 3)).save(img)
        out = br.resolve_image_path({"source": str(img), "ext": ".png"})
        assert out == str(img)
        assert list(tmp_path.glob("*.r*.png")) == []
