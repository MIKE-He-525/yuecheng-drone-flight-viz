#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""越城区行政区划边界工具（330602，WGS84）。"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

PROJECT_ROOT = Path(__file__).resolve().parent
DISTRICT_GEOJSON = PROJECT_ROOT / "geo" / "yuecheng_330602.geojson"
DISTRICT_ADCODE = 330602
DISTRICT_NAME = "越城区"


@lru_cache(maxsize=1)
def load_district_gdf() -> gpd.GeoDataFrame:
    if not DISTRICT_GEOJSON.exists():
        raise FileNotFoundError(
            f"缺少行政区划文件: {DISTRICT_GEOJSON}\n"
            "请运行: python geo_utils.py --download"
        )
    return gpd.read_file(DISTRICT_GEOJSON).to_crs(4326)


@lru_cache(maxsize=1)
def district_polygon():
    return load_district_gdf().union_all()


def district_geojson_for_map(simplify_tolerance: float = 0.0008) -> dict[str, Any]:
    """导出供 Leaflet 使用的 GeoJSON（略作简化以减小体积）。"""
    gdf = load_district_gdf()[["geometry"]].copy()
    if simplify_tolerance > 0:
        gdf["geometry"] = gdf.geometry.simplify(simplify_tolerance, preserve_topology=True)
    return json.loads(gdf.to_json())


def points_in_district(lons: pd.Series, lats: pd.Series) -> pd.Series:
    """批量判断 WGS84 坐标是否在越城区行政面内。"""
    poly = district_polygon()
    geometry = [Point(float(x), float(y)) for x, y in zip(lons, lats)]
    gdf = gpd.GeoDataFrame(geometry=geometry, crs=4326)
    return gdf.geometry.within(poly)


def compute_admin_gps_stats(lons: pd.Series, lats: pd.Series) -> dict[str, Any]:
    """有效 GPS 点的行政内外统计。"""
    inside = points_in_district(lons, lats)
    total = int(len(inside))
    in_n = int(inside.sum())
    out_n = total - in_n
    return {
        "district_adcode": DISTRICT_ADCODE,
        "district_name": DISTRICT_NAME,
        "geojson_file": DISTRICT_GEOJSON.name,
        "valid_gps_points": total,
        "in_district_points": in_n,
        "out_of_district_points": out_n,
        "in_district_pct": round(in_n / total * 100, 1) if total else 0.0,
    }


def download_district_geojson(path: Path | None = None) -> Path:
    """从 DataV 公开接口下载越城区边界。"""
    import urllib.request

    target = path or DISTRICT_GEOJSON
    target.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://geo.datav.aliyun.com/areas_v3/bound/{DISTRICT_ADCODE}.json"
    with urllib.request.urlopen(url, timeout=60) as resp:
        data = json.load(resp)
    target.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return target


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="越城区行政区划边界工具")
    parser.add_argument("--download", action="store_true", help="下载/更新 GeoJSON")
    args = parser.parse_args()
    if args.download:
        p = download_district_geojson()
        print(f"已保存: {p} ({p.stat().st_size} 字节)")
