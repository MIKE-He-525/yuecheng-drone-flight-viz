#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
越城区无人机飞行服务数据看板导出脚本

读取 clean_data/ 下清洗后的 CSV（fly_record、fly_telemetry、airport、route），
聚合为前端看板所需的 JSON 结构，并打包为可离线打开的单页 HTML。

输出（默认写入 dashboard/）：
  - data.json       总览 KPI、月度趋势与分布统计
  - map_data.json   机场、轨迹、航线与地图筛选元数据
  - dashboard.html  内嵌上述 JSON 的单页滚动看板（index + map 合并）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from geo_utils import compute_admin_gps_stats, district_geojson_for_map, points_in_district

# ---------------------------------------------------------------------------
# 路径与常量
# ---------------------------------------------------------------------------

DEFAULT_DATA_DIR = PROJECT_ROOT / "clean_data"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "data.json"
DEFAULT_MAP_OUTPUT = Path(__file__).resolve().parent / "map_data.json"

FACTORY_LABELS = {"DJ": "大疆", "YD": "一电", "ZH": "纵横", "MMC": "科比特"}
FLY_TYPE_LABELS = {1: "任务飞行", 2: "自由飞行"}
DURATION_MAX_MIN = 60
TRACK_MAX_POINTS = 100
SERIES_MAX_POINTS = 80  # 选中轨迹时序图最大点数（控制看板体积）
SPEED_MAX_MPS = 25  # 约 90 km/h，过滤 GPS 跳点造成的虚高速度
# 轨迹筛选起飞时间滑块固定区间（与 fly_record 统计口径一致）
FILTER_DATE_MIN = date(2024, 9, 14)
FILTER_DATE_MAX = date(2026, 5, 21)


# ---------------------------------------------------------------------------
# 轨迹时序与统计
# ---------------------------------------------------------------------------


def build_track_series(group: pd.DataFrame, max_points: int = SERIES_MAX_POINTS) -> dict | None:
    """从单条轨迹遥测生成时序序列，供地图页选中轨迹后的 Chart.js 折线图使用。

    输出字段：相对起飞分钟数、飞行高度、地面高程（DEM）、速度、累计距离、经纬度。
    点数超过 max_points 时均匀降采样，以控制 map_data.json 体积。
    """
    g = group.sort_values("message_time").copy()
    g["message_time"] = pd.to_datetime(g["message_time"], errors="coerce")
    g = g[g["message_time"].notna()]
    if len(g) < 2:
        return None

    t0 = g["message_time"].iloc[0]
    t_min = ((g["message_time"] - t0).dt.total_seconds() / 60.0).to_numpy(dtype=float)
    # height：相对起飞点/机场的高度；elevation：DEM 地面海拔（二者可叠加对比地形）
    if "height" in g.columns:
        height = pd.to_numeric(g["height"], errors="coerce").to_numpy(dtype=float)
    else:
        height = np.full(len(g), np.nan, dtype=float)
    if "elevation" in g.columns:
        elevation = pd.to_numeric(g["elevation"], errors="coerce").to_numpy(dtype=float)
    else:
        elevation = np.full(len(g), np.nan, dtype=float)

    lon = g["longitude"].astype(float).to_numpy()
    lat = g["latitude"].astype(float).to_numpy()
    dt = g["message_time"].diff().dt.total_seconds().to_numpy(dtype=float)
    # 近似平面距离（度 → 米）
    step_m = np.sqrt(np.diff(lon) ** 2 + np.diff(lat) ** 2) * 111_000
    speed = np.full(len(g), np.nan, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        speed[1:] = step_m / dt[1:]
    speed[~np.isfinite(speed)] = np.nan
    speed[(speed <= 0) | (speed >= SPEED_MAX_MPS)] = np.nan
    # 孤立 NaN 用邻域前值填充，便于画线
    speed_s = pd.Series(speed).ffill().bfill()
    speed_kmh = speed_s.to_numpy(dtype=float) * 3.6

    if "flight_distance" in g.columns:
        flight_dist = pd.to_numeric(g["flight_distance"], errors="coerce")
    else:
        flight_dist = pd.Series(np.nan, index=g.index)
    if flight_dist.notna().sum() >= max(2, len(g) // 4):
        dist_m = flight_dist.ffill().bfill().to_numpy(dtype=float)
        # 若非单调递增，改用累计线段长度
        if np.nanmax(dist_m) <= 0 or np.sum(np.diff(dist_m) < -1) > len(g) * 0.2:
            dist_m = np.concatenate([[0.0], np.cumsum(np.nan_to_num(step_m, nan=0.0))])
    else:
        dist_m = np.concatenate([[0.0], np.cumsum(np.nan_to_num(step_m, nan=0.0))])

    # 均匀降采样：首尾点必保留，中间按索引等距抽取
    n = len(g)
    if n <= max_points:
        idx = np.arange(n)
    else:
        idx = np.linspace(0, n - 1, max_points)
        idx = np.unique(np.round(idx).astype(int))
        idx[-1] = n - 1

    def pack(arr: np.ndarray, decimals: int) -> list[float | None]:
        # 按字段语义保留不同小数位，NaN/Inf 输出 null 便于前端 JSON 解析
        out: list[float | None] = []
        for i in idx:
            v = arr[i]
            if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
                out.append(None)
            else:
                out.append(round(float(v), decimals))
        return out

    return {
        "t_min": pack(t_min, 2),
        "height_m": pack(height, 1),
        "elevation_m": pack(elevation, 1),
        "speed_kmh": pack(speed_kmh, 1),
        "distance_km": pack(dist_m / 1000.0, 3),
        "lon": pack(lon, 6),
        "lat": pack(lat, 6),
    }


def compute_track_telemetry_stats(group: pd.DataFrame) -> dict[str, float | None]:
    """从遥测点计算高度、速度等轨迹级统计（供地图筛选）。"""
    stats: dict[str, float | None] = {
        "max_height_m": None,
        "avg_height_m": None,
        "max_speed_kmh": None,
        "avg_speed_kmh": None,
    }
    if "height" in group.columns:
        heights = pd.to_numeric(group["height"], errors="coerce").dropna()
        if len(heights):
            stats["max_height_m"] = round(float(heights.max()), 1)
            stats["avg_height_m"] = round(float(heights.mean()), 1)

    if len(group) < 2 or "message_time" not in group.columns:
        return stats

    g = group.sort_values("message_time").copy()
    g["message_time"] = pd.to_datetime(g["message_time"], errors="coerce")
    g = g[g["message_time"].notna()]
    if len(g) < 2:
        return stats

    dt = g["message_time"].diff().dt.total_seconds()
    dlon = g["longitude"].astype(float).diff()
    dlat = g["latitude"].astype(float).diff()
    dist_m = np.sqrt(dlon**2 + dlat**2) * 111_000
    speed_mps = dist_m / dt
    speed_mps = speed_mps.replace([np.inf, -np.inf], np.nan).dropna()
    speed_mps = speed_mps[(speed_mps > 0) & (speed_mps < SPEED_MAX_MPS)]
    if len(speed_mps):
        stats["max_speed_kmh"] = round(float(speed_mps.max()) * 3.6, 1)
        stats["avg_speed_kmh"] = round(float(speed_mps.mean()) * 3.6, 1)
    return stats


def compute_filter_bounds(tracks: list[dict]) -> dict:
    """根据已导出轨迹汇总地图页多维筛选的滑块范围与枚举值。

    日期区间使用固定 FILTER_DATE_*（与 fly_record 统计口径一致）；
    时长/距离/高度/速度的 max 取真实极值并略留余量，避免默认条件静默排除边界轨迹。
    """

    def nums(key: str) -> list[float]:
        return [float(t[key]) for t in tracks if t.get(key) is not None]

    durations = nums("duration_min")
    distances_km = [float(t["distance_m"]) / 1000 for t in tracks if t.get("distance_m") is not None]
    heights = nums("max_height_m")
    speeds = nums("max_speed_kmh")

    dur_max = round(float(max(durations)), 1) if durations else float(DURATION_MAX_MIN)
    dist_max = round((max(distances_km) if distances_km else 25) + 0.05, 1)
    height_max = int(np.ceil(max(heights) if heights else 250))
    # 滑块默认上界覆盖真实极值，避免默认条件静默排除长时/高速轨迹
    speed_max = int(np.ceil(max(speeds))) if speeds else 90

    factories = sorted({t.get("factory") or "未知" for t in tracks if t.get("has_record")})
    fly_types = sorted({t.get("fly_type") or "未知" for t in tracks})
    day_span = (FILTER_DATE_MAX - FILTER_DATE_MIN).days

    return {
        "date": {
            "min": FILTER_DATE_MIN.isoformat(),
            "max": FILTER_DATE_MAX.isoformat(),
            "day_min": 0,
            "day_max": int(day_span),
            "unit": "日",
            "label": "起飞时间",
        },
        "duration_min": {"min": 0, "max": round(dur_max, 1), "unit": "分钟", "label": "飞行时长"},
        "distance_km": {"min": 0, "max": dist_max, "unit": "km", "label": "飞行距离"},
        "height_m": {"min": 0, "max": height_max, "unit": "m", "label": "飞行轨迹最大高度"},
        "speed_kmh": {"min": 0, "max": speed_max, "unit": "km/h", "label": "飞行最大速度"},
        "factories": factories,
        "fly_types": fly_types,
    }


# ---------------------------------------------------------------------------
# 数据加载与轨迹简化
# ---------------------------------------------------------------------------


def load_tables(data_dir: Path) -> dict[str, pd.DataFrame]:
    """从 clean_data/ 读取四张核心 CSV 表。"""
    tables = {}
    for name in ("fly_record", "fly_telemetry", "airport", "route"):
        tables[name] = pd.read_csv(data_dir / f"{name}.csv", encoding="utf-8-sig")
    return tables


def series_dict(series: pd.Series) -> dict[str, int]:
    """将 value_counts 结果转为 JSON 友好的 str→int 字典。"""
    return {str(k): int(v) for k, v in series.items()}


def factory_label(code: object) -> str:
    """厂商代码转中文展示名，未知代码原样返回。"""
    if pd.isna(code):
        return "未知"
    return FACTORY_LABELS.get(str(code), str(code))


def compute_default_view(airports: list[dict]) -> dict:
    """默认视野：以机场集群为中心，预留覆盖半径边距（不受离群轨迹影响）。"""
    lats = [a["latitude"] for a in airports]
    lons = [a["longitude"] for a in airports]
    max_radius_m = max((a["radius_m"] for a in airports), default=5000)
    pad_deg = max(max_radius_m / 111_000 * 0.45, 0.04)
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)
    return {
        "center": [round((min_lat + max_lat) / 2, 5), round((min_lon + max_lon) / 2, 5)],
        "southwest": [round(min_lat - pad_deg, 4), round(min_lon - pad_deg, 4)],
        "northeast": [round(max_lat + pad_deg, 4), round(max_lon + pad_deg, 4)],
        "max_zoom": 12,
    }


def simplify_track(coords: list[tuple[float, float]], max_points: int = TRACK_MAX_POINTS) -> list[list[float]]:
    """均匀抽稀轨迹坐标点，控制 map_data.json 体积；始终保留首尾点。"""
    if len(coords) <= max_points:
        return [[round(lon, 6), round(lat, 6)] for lon, lat in coords]
    step = len(coords) / max_points
    indices = sorted({int(i * step) for i in range(max_points)})
    indices[-1] = len(coords) - 1
    return [[round(coords[i][0], 6), round(coords[i][1], 6)] for i in indices]


def strip_embedded_json(html_path: Path, var_name: str) -> None:
    """移除 HTML 中的内嵌 JSON 常量（保持仓库模板不含业务数据）。"""
    if not html_path.exists():
        return
    text = html_path.read_text(encoding="utf-8")
    text, n = re.subn(
        rf"\n\s*const {var_name} = .*?;\n",
        "\n",
        text,
        count=1,
        flags=re.DOTALL,
    )
    if n:
        html_path.write_text(text, encoding="utf-8")
        print(f"已清理内嵌数据: {html_path}")


# ---------------------------------------------------------------------------
# HTML 单页打包
# ---------------------------------------------------------------------------


def _extract_style(html: str) -> str:
    """从模板 HTML 提取 <style> 块内容。"""
    match = re.search(r"<style>(.*?)</style>", html, flags=re.DOTALL)
    return match.group(1).strip() if match else ""


def _extract_script(html: str) -> str:
    """从模板 HTML 提取 </body> 前最后一个 <script> 块内容。"""
    match = re.search(r"<script>\s*(.*?)\s*</script>\s*</body>", html, flags=re.DOTALL)
    return match.group(1).strip() if match else ""


def _patch_dashboard_script(script: str) -> str:
    """改造 index.html 内联脚本：移除 fetch/主题自启，改为由内嵌 JSON 统一启动。"""
    script = re.sub(
        r"const EMBEDDED_DATA = .*?;",
        "",
        script,
        count=1,
        flags=re.DOTALL,
    )
    script = re.sub(
        r'fetch\("data\.json"\).*?\.catch\(err => \{.*?\}\);',
        "",
        script,
        count=1,
        flags=re.DOTALL,
    )
    script = re.sub(
        r"function setTheme\(name\) \{.*?\n    \}\n",
        "",
        script,
        count=1,
        flags=re.DOTALL,
    )
    script = re.sub(
        r'document\.querySelectorAll\("\.theme-btn"\)\.forEach\(btn => \{.*?\}\);\n',
        "",
        script,
        count=1,
        flags=re.DOTALL,
    )
    script = re.sub(
        r"\n\s*const savedTheme = localStorage\.getItem\(\"dashboard-theme\"\);\s*\n\s*if \(savedTheme.*?\n",
        "\n",
        script,
        count=1,
        flags=re.DOTALL,
    )
    script = re.sub(r"\n\s*bootDashboard\(\);\s*", "\n", script)
    script = re.sub(
        r"function applyLinearBorder\(\) \{",
        """function bootDashboard() {
      if (!EMBEDDED_DATA) throw new Error("总览数据为空");
      DATA = EMBEDDED_DATA;
      document.getElementById("meta-range").textContent =
        `时间范围：${DATA.meta.date_range.start} ~ ${DATA.meta.date_range.end}`;
      document.getElementById("meta-range").style.color = "";
      renderKPIs();
      applyLinearBorder();
      if (typeof Chart === "undefined") {
        throw new Error("Chart.js 未加载，请检查网络后刷新");
      }
      renderCharts();
    }

    function applyLinearBorder() {""",
        script,
        count=1,
    )
    return script


def _patch_map_script(script: str) -> str:
    """剥离模板自启动逻辑，供单页嵌入后由启动脚本统一初始化。"""
    script = re.sub(
        r"const EMBEDDED_MAP_DATA = .*?;",
        "",
        script,
        count=1,
        flags=re.DOTALL,
    )
    script = re.sub(
        r"\n\s*const savedTheme = localStorage\.getItem\(\"dashboard-theme\"\);\n"
        r"\s*if \(savedTheme\) \{\n\s*document\.documentElement\.dataset\.theme = savedTheme;\n\s*\}\n",
        "\n",
        script,
        count=1,
    )
    script = re.sub(
        r"\n\s*bindMapUi\(\);\s*\n\s*fetch\(\"map_data\.json\"\).*?\.catch\(err => \{.*?\}\);",
        "\n",
        script,
        count=1,
        flags=re.DOTALL,
    )
    script = script.replace("function initMap()", "function initMapView()")
    script = script.replace("initMap();", "initMapView();")
    return script


def _load_map_section_css(dashboard_dir: Path) -> str:
    """地图区样式唯一来源：dashboard/map_section.css（map.html 通过 link 引用）。"""
    css_path = dashboard_dir / "map_section.css"
    if not css_path.exists():
        raise FileNotFoundError(f"缺少地图样式文件: {css_path}")
    return css_path.read_text(encoding="utf-8").strip()


# 单页路由脚本：总览立即启动，地图区滚动可见时再懒加载（减轻首屏解析压力）
ROUTER_SCRIPT = """
    let EMBEDDED_DATA = null;
    let mapPayloadCache = null;
    let mapViewReady = false;
    let mapUiBound = false;

    function setDashboardStatus(msg, isError = false) {
      const el = document.getElementById("meta-range");
      if (!el) return;
      el.textContent = msg;
      el.style.color = isError ? "var(--negative, #F53F3F)" : "";
    }

    function readJsonScript(id) {
      const node = document.getElementById(id);
      if (!node || !node.textContent.trim()) {
        throw new Error("未找到内嵌数据 #" + id);
      }
      return JSON.parse(node.textContent);
    }

    function ensureMapData() {
      if (MAP_DATA) return MAP_DATA;
      const t0 = performance.now();
      setMapStatus("正在解析地图数据（约 1.2MB，首次可能需数秒）…");
      if (!mapPayloadCache) {
        mapPayloadCache = readJsonScript("embedded-map-data");
      }
      MAP_DATA = mapPayloadCache;
      const ms = Math.round(performance.now() - t0);
      setMapStatus("地图数据已就绪（解析 " + ms + " ms）");
      return MAP_DATA;
    }

    function applyTheme(name) {
      document.documentElement.dataset.theme = name;
      document.querySelectorAll(".theme-btn").forEach(btn => {
        btn.classList.toggle("active", btn.dataset.theme === name);
      });
      localStorage.setItem("dashboard-theme", name);
      if (typeof DATA !== "undefined" && DATA) {
        renderKPIs();
        applyLinearBorder();
        if (typeof Chart !== "undefined") renderCharts();
      }
      if (typeof applyBasemap === "function" && map) applyBasemap(name);
      if (selectedTrackId != null && typeof MAP_DATA !== "undefined" && MAP_DATA) {
        const t = MAP_DATA.tracks.find(x => x.id === selectedTrackId);
        if (t && typeof renderTrackSeriesChart === "function") renderTrackSeriesChart(t);
      }
    }

    function bootMapSection() {
      if (mapViewReady) {
        if (map) setTimeout(() => map.invalidateSize(), 50);
        return;
      }
      try {
        ensureMapData();
        if (!mapUiBound) {
          bindMapUi();
          mapUiBound = true;
        }
        if (typeof L === "undefined") {
          throw new Error("Leaflet 未加载，请检查网络后刷新");
        }
        setMapStatus(
          "时间范围：" + MAP_DATA.meta.date_range.start + " ~ " + MAP_DATA.meta.date_range.end,
        );
        renderFactoryFilters();
        updateFilterStatus();
        renderTrackList();
        initMapView();
        mapViewReady = true;
        setTimeout(() => { if (map) map.invalidateSize(); }, 80);
      } catch (err) {
        setMapStatus("地图加载失败：" + err.message, true);
        console.error(err);
      }
    }

    function watchMapSection() {
      const el = document.getElementById("section-map");
      if (!el || typeof IntersectionObserver === "undefined") {
        bootMapSection();
        return;
      }
      const io = new IntersectionObserver((entries) => {
        if (entries.some(e => e.isIntersecting)) {
          io.disconnect();
          bootMapSection();
        }
      }, { rootMargin: "240px 0px" });
      io.observe(el);
    }

    function startup() {
      try {
        setDashboardStatus("正在读取总览数据…");
        EMBEDDED_DATA = readJsonScript("embedded-dashboard-data");
        bootDashboard();
      } catch (err) {
        setDashboardStatus("数据加载失败：" + err.message, true);
        console.error(err);
      }
      watchMapSection();
    }

    document.querySelectorAll(".theme-btn").forEach(btn => {
      btn.addEventListener("click", () => applyTheme(btn.dataset.theme));
    });

    const savedTheme = localStorage.getItem("dashboard-theme");
    if (savedTheme && THEMES.includes(savedTheme)) applyTheme(savedTheme);

    window.addEventListener("resize", () => {
      if (map) map.invalidateSize();
    });

    startup();
"""


def build_standalone_html(
    dashboard_dir: Path,
    payload: dict,
    map_payload: dict,
) -> None:
    """合并 index.html 与 map.html 为单页滚动 dashboard.html。

    流程：抽取两页 body/style/script → 补丁去除独立 fetch 与自启动 →
    内嵌 data.json / map_data.json → 注入 ROUTER_SCRIPT 统一初始化。
    """
    index_path = dashboard_dir / "index.html"
    map_path = dashboard_dir / "map.html"
    output_path = dashboard_dir / "dashboard.html"
    if not index_path.exists() or not map_path.exists():
        print("跳过单文件打包: 缺少 index.html 或 map.html")
        return

    index_html = index_path.read_text(encoding="utf-8")
    map_html = map_path.read_text(encoding="utf-8")

    dash_body_match = re.search(
        r'(<div class="app">.*?</section>\s*</div>)',
        index_html,
        flags=re.DOTALL,
    )
    map_body_match = re.search(
        r'(<section id="section-map"[\s\S]*?</section>)',
        map_html,
        flags=re.DOTALL,
    )
    if not dash_body_match or not map_body_match:
        raise RuntimeError("无法解析 index.html / map.html 结构")

    # 将 map section 插入 index 的 .app 容器内，形成上下滚动的单页布局
    dash_body = dash_body_match.group(1).rstrip()
    if not dash_body.endswith("</div>"):
        raise RuntimeError("无法解析 index.html 的 .app 闭合标签")
    dash_inner = dash_body[: -len("</div>")].rstrip()
    map_body = map_body_match.group(1)
    page_body = f"{dash_inner}\n\n{map_body}\n  </div>"

    index_style = _extract_style(index_html)
    map_section_css = _load_map_section_css(dashboard_dir)
    dash_script = _patch_dashboard_script(_extract_script(index_html))
    map_script = _patch_map_script(_extract_script(map_html))

    dashboard_json = json.dumps(payload, ensure_ascii=False)
    map_json = json.dumps(map_payload, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN" data-theme="saas">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>越城区无人机飞行服务 · 数据看板</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Noto+Sans+SC:wght@400;500;600;700&display=swap" rel="stylesheet" />
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" crossorigin="" />
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" crossorigin=""></script>
  <link rel="stylesheet" href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css" crossorigin="" />
  <script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js" crossorigin=""></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
{index_style}
{map_section_css}
  </style>
</head>
<body>
{page_body}
  <script type="application/json" id="embedded-dashboard-data">{dashboard_json}</script>
  <script type="application/json" id="embedded-map-data">{map_json}</script>
  <script>
{dash_script}

{map_script}

{ROUTER_SCRIPT}
  </script>
</body>
</html>
"""
    output_path.write_text(html, encoding="utf-8")
    _validate_standalone_script(html)
    size_kb = output_path.stat().st_size // 1024
    print(f"已写入: {output_path} ({size_kb} KB)")


def _validate_standalone_script(html: str) -> None:
    """打包后做简单语法嗅探，避免 map 脚本尾部残留导致总览图表全部失效。"""
    match = re.search(r"<script>\s*(.*?)\s*</script>\s*</body>", html, flags=re.DOTALL)
    if not match:
        return
    script = match.group(1)
    if re.search(r"\}\);\s*document\.getElementById\(\"btn-apply-filters\"\)", script):
        raise RuntimeError("dashboard.html 脚本存在残留 '});'，请检查 _patch_map_script")
    if "function bootDashboard()" not in script or "function renderCharts()" not in script:
        raise RuntimeError("dashboard.html 缺少 bootDashboard/renderCharts")


# ---------------------------------------------------------------------------
# JSON 载荷构建
# ---------------------------------------------------------------------------


def build_payload(tables: dict[str, pd.DataFrame]) -> dict:
    """构建总览看板所需的 JSON 结构（KPI、趋势与分布统计）。"""
    rec = tables["fly_record"]
    apt = tables["airport"]
    tel = tables["fly_telemetry"]
    route = tables["route"]

    rec = rec.copy()
    rec["real_start_time"] = pd.to_datetime(rec["real_start_time"])

    monthly = (
        rec.set_index("real_start_time")
        .resample("ME")
        .size()
        .to_frame("count")
        .reset_index()
    )
    monthly["month"] = monthly["real_start_time"].dt.strftime("%Y-%m")

    factory_rec = rec["factory_code"].fillna("未知").map(factory_label).value_counts()
    factory_apt = apt["factory_code"].fillna("未知").map(factory_label).value_counts()
    fly_type = rec["fly_type"].apply(lambda x: FLY_TYPE_LABELS.get(x, "未知")).value_counts()
    top_tasks = (
        rec.loc[rec["fly_type"] == 1, "task_name"]
        .value_counts()
        .head(12)
        .sort_values()
    )

    duration = rec.loc[rec["real_duration"] > 0, "real_duration"] / 60
    duration = duration[duration <= DURATION_MAX_MIN]
    hist, edges = pd.cut(duration, bins=12, retbins=True)
    duration_hist = hist.value_counts().sort_index()
    duration_labels = [f"{int(edges[i])}-{int(edges[i + 1])}" for i in range(len(edges) - 1)]

    valid_tel = tel.dropna(subset=["longitude", "latitude", "fly_record_id"])
    valid_tel = valid_tel[(valid_tel["longitude"] != 0) | (valid_tel["latitude"] != 0)]
    track_sizes = valid_tel.groupby("fly_record_id").size()
    tracks_drawable = int((track_sizes >= 2).sum())
    telemetry_valid = int(len(valid_tel))

    return {
        "meta": {
            "title": "越城区无人机飞行服务数据看板",
            "date_range": {
                "start": rec["real_start_time"].min().strftime("%Y-%m-%d"),
                "end": rec["real_start_time"].max().strftime("%Y-%m-%d"),
            },
            "kpi_notes": {
                "telemetry": "有效 GPS 点：经纬度非空且非 (0,0)",
                "tracks_with_telemetry": "至少 2 个有效 GPS 点，可绘制轨迹",
            },
        },
        "kpis": {
            "flights": int(len(rec)),
            "airports": int(len(apt)),
            "telemetry": telemetry_valid,
            "telemetry_total_rows": int(len(tel)),
            "routes": int(len(route)),
            "tracks_with_telemetry": tracks_drawable,
        },
        "monthly": {
            "labels": monthly["month"].tolist(),
            "values": monthly["count"].astype(int).tolist(),
        },
        "factory_record": series_dict(factory_rec),
        "factory_airport": series_dict(factory_apt),
        "fly_type": series_dict(fly_type),
        "top_tasks": {
            "labels": top_tasks.index.tolist(),
            "values": top_tasks.astype(int).tolist(),
        },
        "duration": {
            "labels": duration_labels,
            "values": [int(duration_hist.get(cat, 0)) for cat in duration_hist.index],
        },
    }


def build_map_payload(tables: dict[str, pd.DataFrame]) -> dict:
    """构建地图页 JSON：机场点位、简化轨迹、筛选范围、行政区边界与航线统计。"""
    rec = tables["fly_record"].copy()
    apt = tables["airport"].copy()
    tel = tables["fly_telemetry"].copy()
    route = tables["route"].copy()

    rec["real_start_time"] = pd.to_datetime(rec["real_start_time"], errors="coerce")
    rec_lookup = rec.set_index("id", drop=False)

    flights_by_dock = (
        rec.loc[rec["dock_sn"].notna(), "dock_sn"]
        .value_counts()
        .to_dict()
    )

    airports: list[dict] = []
    for _, row in apt.iterrows():
        lon = float(row["longitude"])
        lat = float(row["latitude"])
        device_sn = str(row["device_sn"]) if pd.notna(row["device_sn"]) else ""
        # air_line 字段在此复用为机场覆盖半径（米），缺失时默认 5000
        radius_raw = row.get("air_line")
        radius_m = int(float(radius_raw)) if pd.notna(radius_raw) and str(radius_raw).strip() else 5000
        airports.append(
            {
                "id": str(row["id"]),
                "name": str(row["model_name"]) if pd.notna(row["model_name"]) else device_sn,
                "device_sn": device_sn,
                "factory": factory_label(row.get("factory_code")),
                "factory_code": str(row["factory_code"]) if pd.notna(row["factory_code"]) else "",
                "model_name": str(row["model_name"]) if pd.notna(row["model_name"]) else "",
                "longitude": round(lon, 6),
                "latitude": round(lat, 6),
                "radius_m": radius_m,
                "height": float(row["height"]) if pd.notna(row.get("height")) else None,
                "relative_height": float(row["relative_height"]) if pd.notna(row.get("relative_height")) else None,
                "department": str(row["department"]) if pd.notna(row.get("department")) else "",
                "labels": str(row["labels"]) if pd.notna(row.get("labels")) else "",
                "firmware_version": str(row["firmware_version"]) if pd.notna(row.get("firmware_version")) else "",
                "flight_count": int(flights_by_dock.get(device_sn, 0)),
            }
        )

    apt_by_sn = {a["device_sn"]: a for a in airports if a["device_sn"]}

    valid_tel = tel.dropna(subset=["longitude", "latitude", "fly_record_id"]).copy()
    valid_tel = valid_tel[(valid_tel["longitude"] != 0) | (valid_tel["latitude"] != 0)]
    valid_tel["message_time"] = pd.to_datetime(valid_tel["message_time"], errors="coerce")

    # 逐点判断 GPS 是否落在越城区行政面内，供轨迹级 in_district_ratio 与 KPI
    tel_in_district = pd.Series(
        points_in_district(
            valid_tel["longitude"].astype(float),
            valid_tel["latitude"].astype(float),
        ).values,
        index=valid_tel.index,
    )
    admin_stats = compute_admin_gps_stats(
        valid_tel["longitude"].astype(float),
        valid_tel["latitude"].astype(float),
    )

    tracks: list[dict] = []
    for fid, group in valid_tel.groupby("fly_record_id"):
        group = group.sort_values("message_time")
        coords_raw = list(zip(group["longitude"].astype(float), group["latitude"].astype(float)))
        coords = simplify_track(coords_raw)
        if len(coords) < 2:
            continue

        meta: dict = {
            "id": int(fid),
            "coords": coords,
            "point_count": int(len(group)),
            "in_district_ratio": round(float(tel_in_district.loc[group.index].mean()), 3),
        }
        meta.update(compute_track_telemetry_stats(group))
        series = build_track_series(group)
        if series:
            meta["series"] = series

        # 关联 fly_record 补充任务元数据；无记录时为 orphan 遥测（has_record=False）
        if int(fid) in rec_lookup.index:
            r = rec_lookup.loc[int(fid)]
            if isinstance(r, pd.DataFrame):
                r = r.iloc[0]
            dock_sn = str(r["dock_sn"]) if pd.notna(r.get("dock_sn")) else ""
            meta.update(
                {
                    "fly_type": FLY_TYPE_LABELS.get(int(r["fly_type"]), "未知") if pd.notna(r.get("fly_type")) else "未知",
                    "task_name": str(r["task_name"]) if pd.notna(r.get("task_name")) else "",
                    "factory": factory_label(r.get("factory_code")),
                    "factory_code": str(r["factory_code"]) if pd.notna(r.get("factory_code")) else "",
                    "start_time": r["real_start_time"].strftime("%Y-%m-%d %H:%M") if pd.notna(r["real_start_time"]) else "",
                    "duration_min": round(float(r["real_duration"]) / 60, 1) if pd.notna(r.get("real_duration")) and r["real_duration"] > 0 else None,
                    "distance_m": round(float(r["real_distance"]), 1) if pd.notna(r.get("real_distance")) else None,
                    "dock_sn": dock_sn,
                    "airport_name": apt_by_sn.get(dock_sn, {}).get("name", ""),
                    "mission_id": int(r["mission_id"]) if pd.notna(r.get("mission_id")) and r["mission_id"] != 0 else None,
                    "uav_sn": str(r["uav_sn"]) if pd.notna(r.get("uav_sn")) else "",
                    "has_record": True,
                }
            )
        else:
            uav_sn = ""
            if "uav_sn" in group.columns:
                for v in group["uav_sn"]:
                    if pd.notna(v) and str(v).strip():
                        uav_sn = str(v)
                        break
            meta.update(
                {
                    "fly_type": "未知",
                    "task_name": "",
                    "factory": "未知",
                    "factory_code": "",
                    "start_time": "",
                    "duration_min": None,
                    "distance_m": None,
                    "dock_sn": "",
                    "airport_name": "",
                    "mission_id": int(group["mission_id"].iloc[0]) if pd.notna(group["mission_id"].iloc[0]) and group["mission_id"].iloc[0] != 0 else None,
                    "uav_sn": uav_sn,
                    "has_record": False,
                }
            )
        tracks.append(meta)

    tracks.sort(key=lambda t: t["point_count"], reverse=True)

    route_dist = route.loc[route["distance"] > 0, "distance"] / 1000
    route_hist, route_edges = pd.cut(route_dist, bins=10, retbins=True)
    route_hist_counts = route_hist.value_counts().sort_index()
    route_labels = [f"{route_edges[i]:.1f}-{route_edges[i + 1]:.1f}" for i in range(len(route_edges) - 1)]

    route_factory = (
        route["factory_code"]
        .fillna("未知")
        .map(factory_label)
        .value_counts()
    )

    all_lons = [a["longitude"] for a in airports] + [c[0] for t in tracks for c in t["coords"]]
    all_lats = [a["latitude"] for a in airports] + [c[1] for t in tracks for c in t["coords"]]

    return {
        "meta": {
            "title": "越城区无人机 · 空间地图",
            "date_range": {
                "start": rec["real_start_time"].min().strftime("%Y-%m-%d") if rec["real_start_time"].notna().any() else "",
                "end": rec["real_start_time"].max().strftime("%Y-%m-%d") if rec["real_start_time"].notna().any() else "",
            },
            "bounds": {
                "southwest": [round(min(all_lats), 4), round(min(all_lons), 4)],
                "northeast": [round(max(all_lats), 4), round(max(all_lons), 4)],
            },
            "default_view": compute_default_view(airports),
            "district_name": admin_stats.get("district_name", "越城区"),
            "district_adcode": admin_stats.get("district_adcode", 330602),
            "district_boundary": district_geojson_for_map(),
            "filter_bounds": compute_filter_bounds(tracks),
        },
        "kpis": {
            "airports": len(airports),
            "tracks": len(tracks),
            "telemetry_points": int(len(valid_tel)),
            "telemetry_in_district": int(admin_stats["in_district_points"]),
            "telemetry_out_of_district": int(admin_stats["out_of_district_points"]),
            "telemetry_in_district_pct": admin_stats["in_district_pct"],
            "routes": int(len(route)),
            "flights_total": int(len(rec)),
        },
        "airports": airports,
        "tracks": tracks,
        "routes": {
            "distance_km": {
                "min": round(float(route_dist.min()), 2) if not route_dist.empty else 0,
                "max": round(float(route_dist.max()), 2) if not route_dist.empty else 0,
                "median": round(float(route_dist.median()), 2) if not route_dist.empty else 0,
                "mean": round(float(route_dist.mean()), 2) if not route_dist.empty else 0,
            },
            "histogram": {
                "labels": route_labels,
                "values": [int(route_hist_counts.get(cat, 0)) for cat in route_hist_counts.index],
            },
            "by_factory": series_dict(route_factory),
        },
        "factory_summary": {
            "airport": series_dict(apt["factory_code"].fillna("未知").map(factory_label).value_counts()),
            "track": series_dict(
                pd.Series([t["factory"] for t in tracks if t.get("has_record")]).value_counts()
            ),
        },
    }


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def main() -> None:
    """读取 clean_data/，写出 data.json、map_data.json 并打包 dashboard.html。"""
    parser = argparse.ArgumentParser(description="导出 dashboard JSON")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--map-output", type=Path, default=DEFAULT_MAP_OUTPUT)
    args = parser.parse_args()

    tables = load_tables(args.data_dir)
    dashboard_dir = args.output.parent

    payload = build_payload(tables)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入: {args.output}")

    map_payload = build_map_payload(tables)
    args.map_output.write_text(json.dumps(map_payload, ensure_ascii=False), encoding="utf-8")
    print(f"已写入: {args.map_output} ({args.map_output.stat().st_size // 1024} KB)")
    build_standalone_html(dashboard_dir, payload, map_payload)


if __name__ == "__main__":
    main()
