#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
越城区无人机飞行服务数据 — 简单可视化

环境：Anaconda env `coding`
  C:\\Users\\hetia\\anaconda3\\envs\\coding\\python.exe

依赖：pandas、matplotlib、seaborn、geopandas、contextily

用法：
  python visualize.py
  python visualize.py --data-dir clean_data --output-dir figures

输出：figures/ 目录下的 PNG 图表
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import contextily as cx
import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
from matplotlib.axes import Axes
from matplotlib.container import BarContainer
from matplotlib.figure import Figure
from shapely.geometry import LineString

# ---------------------------------------------------------------------------
# 路径与样式
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "clean_data"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "figures"

FACTORY_LABELS = {"DJ": "大疆", "YD": "一电", "ZH": "纵横"}
FLY_TYPE_LABELS = {1: "任务飞行", 2: "自由飞行"}
FACTORY_COLORS = {"DJ": "#E74C3C", "YD": "#3498DB", "ZH": "#2ECC71"}
# OSM Mapnik 瓦片（等同 cx.providers.OpenStreetMap.Mapnik，避免 xyzservices 类型存根缺失）
BASEMAP_SOURCE = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
FIG_DPI = 300
GEO_FIG_HEIGHT = 10.0
DURATION_PLOT_MAX_MIN = 60  # 作图上限（分钟），超出视为极端值

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def setup_style() -> None:
    """配置中文字体与 seaborn 主题。"""
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    sns.set_theme(style="whitegrid", font="Microsoft YaHei")


def load_tables(data_dir: Path) -> dict[str, pd.DataFrame]:
    """读取清洗后的 CSV 表。"""
    tables = {}
    for name in ("fly_record", "fly_telemetry", "airport", "route"):
        path = data_dir / f"{name}.csv"
        if not path.exists():
            raise FileNotFoundError(f"未找到数据文件: {path}")
        tables[name] = pd.read_csv(path, encoding="utf-8-sig")
        logger.info("已加载 %s: %d 行", name, len(tables[name]))
    return tables


def save_fig(
    fig: Figure,
    output_dir: Path,
    name: str,
    *,
    tight: bool = True,
) -> None:
    """保存图表为 PNG。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{name}.png"
    kwargs: dict = {"dpi": FIG_DPI, "facecolor": "white"}
    if tight:
        kwargs["bbox_inches"] = "tight"
        kwargs["pad_inches"] = 0.08
    fig.savefig(path, **kwargs)
    plt.close(fig)
    logger.info("  已保存: %s", path.name)


def geo_figsize(minx: float, miny: float, maxx: float, maxy: float) -> tuple[float, float]:
    """按 Web Mercator 数据范围计算等比例 figsize，避免底图被拉伸。"""
    width_m = max(maxx - minx, 1.0)
    height_m = max(maxy - miny, 1.0)
    ratio = width_m / height_m
    height = GEO_FIG_HEIGHT
    width = height * ratio
    width = max(8.0, min(16.0, width))
    return width, height


def finalize_geo_ax(ax: Axes, *, padding: float = 0.12) -> None:
    """设置等比例范围并叠加 OSM 底图。"""
    xmin, xmax = ax.get_xlim()
    ymin, ymax = ax.get_ylim()
    dx = (xmax - xmin) * padding
    dy = (ymax - ymin) * padding
    ax.set_xlim(xmin - dx, xmax + dx)
    ax.set_ylim(ymin - dy, ymax + dy)
    ax.set_aspect("equal", adjustable="box")
    cx.add_basemap(
        ax,
        crs="EPSG:3857",
        source=BASEMAP_SOURCE,
        zorder=0,
        attribution_size=7,
    )
    ax.set_axis_off()


def to_mercator(
    df: pd.DataFrame,
    *,
    lon_col: str = "longitude",
    lat_col: str = "latitude",
) -> gpd.GeoDataFrame:
    """将含经纬度的 DataFrame 转为 Web Mercator (EPSG:3857) GeoDataFrame。"""
    geo = df.dropna(subset=[lon_col, lat_col]).copy()
    gdf = gpd.GeoDataFrame(
        geo,
        geometry=gpd.points_from_xy(geo[lon_col], geo[lat_col]),
        crs="EPSG:4326",
    )
    return gdf.to_crs(epsg=3857)


# ---------------------------------------------------------------------------
# 图表
# ---------------------------------------------------------------------------

def plot_monthly_flights(record: pd.DataFrame, output_dir: Path) -> None:
    """按月统计飞行次数趋势。"""
    df = record.copy()
    df["real_start_time"] = pd.to_datetime(df["real_start_time"])
    monthly = (
        df.set_index("real_start_time")
        .resample("ME")
        .size()
        .to_frame("count")
        .reset_index()
    )
    monthly["month"] = monthly["real_start_time"].dt.strftime("%Y-%m")

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(monthly["real_start_time"], monthly["count"], marker="o")
    ax.set_title("飞行次数 — 按月趋势")
    ax.set_xlabel("月份")
    ax.set_ylabel("飞行次数")
    ax.tick_params(axis="x", rotation=45)
    fig.tight_layout()
    save_fig(fig, output_dir, "01_monthly_flights")


def annotate_bar_values(ax: Axes, *, fmt: str = "{:.0f}") -> None:
    """在柱状图每个柱顶标注数值。"""
    for container in ax.containers:
        if not isinstance(container, BarContainer):
            continue
        labels = [fmt.format(v.get_height()) for v in container]
        ax.bar_label(container, labels=labels, padding=3, fontsize=10)


def plot_factory_distribution(record: pd.DataFrame, airport: pd.DataFrame, output_dir: Path) -> None:
    """飞行记录与机场设备的厂商分布。"""
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))

    rec_counts = (
        record["factory_code"]
        .fillna("未知")
        .map(lambda x: FACTORY_LABELS.get(x, x))
        .value_counts()
    )
    sns.barplot(
        x=rec_counts.index, y=rec_counts.values, hue=rec_counts.index,
        ax=axes[0], palette="Blues_d", legend=False,
    )
    annotate_bar_values(axes[0])
    axes[0].set_title("飞行记录 — 厂商分布")
    axes[0].set_xlabel("厂商")
    axes[0].set_ylabel("次数")
    axes[0].set_ylim(0, rec_counts.max() * 1.12)

    apt_counts = (
        airport["factory_code"]
        .fillna("未知")
        .map(lambda x: FACTORY_LABELS.get(x, x))
        .value_counts()
    )
    sns.barplot(
        x=apt_counts.index, y=apt_counts.values, hue=apt_counts.index,
        ax=axes[1], palette="Greens_d", legend=False,
    )
    annotate_bar_values(axes[1])
    axes[1].set_title("机场设备 — 厂商分布")
    axes[1].set_xlabel("厂商")
    axes[1].set_ylabel("数量")
    axes[1].set_ylim(0, apt_counts.max() * 1.15)

    fig.tight_layout()
    save_fig(fig, output_dir, "02_factory_distribution")


def plot_fly_type(record: pd.DataFrame, output_dir: Path) -> None:
    """飞行类型占比。"""
    counts = (
        record["fly_type"]
        .apply(lambda x: FLY_TYPE_LABELS.get(x, "未知"))
        .value_counts()
    )

    fig, ax = plt.subplots(figsize=(6, 4))
    colors = sns.color_palette("Set2", n_colors=len(counts))
    pie_values = counts.to_numpy(dtype=float)
    pie_labels = [str(label) for label in counts.index]
    ax.pie(pie_values, labels=pie_labels, autopct="%1.1f%%", colors=colors, startangle=90)
    ax.set_title("飞行类型分布")
    fig.tight_layout()
    save_fig(fig, output_dir, "03_fly_type")


def plot_duration_distance(record: pd.DataFrame, output_dir: Path) -> None:
    """飞行时长与距离分布（仅统计有效正值；时长截断极端值以便阅读）。"""
    df = record.copy()
    duration_all = df.loc[df["real_duration"] > 0, "real_duration"] / 60  # 分钟
    n_duration_outliers = int((duration_all > DURATION_PLOT_MAX_MIN).sum())
    duration = duration_all[duration_all <= DURATION_PLOT_MAX_MIN]
    distance = df.loc[df["real_distance"] > 0, "real_distance"] / 1000  # 公里

    fig, axes = plt.subplots(1, 2, figsize=(10, 4))

    sns.histplot(duration, bins=30, kde=True, ax=axes[0], color="#4C72B0")
    axes[0].set_xlim(0, DURATION_PLOT_MAX_MIN)
    title = f"飞行时长分布（≤{DURATION_PLOT_MAX_MIN} 分钟"
    if n_duration_outliers:
        title += f"，{n_duration_outliers} 条极端值未展示"
    title += "）"
    axes[0].set_title(title)
    axes[0].set_xlabel("时长（分钟）")
    axes[0].set_ylabel("频次")

    sns.histplot(distance, bins=40, kde=True, ax=axes[1], color="#55A868")
    axes[1].set_title("飞行距离分布")
    axes[1].set_xlabel("距离（公里）")
    axes[1].set_ylabel("频次")

    fig.tight_layout()
    save_fig(fig, output_dir, "04_duration_distance")


def plot_top_tasks(record: pd.DataFrame, output_dir: Path, top_n: int = 10) -> None:
    """任务飞行 Top N 任务名称。"""
    top = (
        record.loc[record["fly_type"] == 1, "task_name"]
        .value_counts()
        .head(top_n)
        .sort_values()
    )

    fig, ax = plt.subplots(figsize=(8, 5))
    sns.barplot(x=top.values, y=top.index, hue=top.index, ax=ax, palette="viridis", legend=False)
    ax.set_title(f"任务飞行 Top {top_n} 任务")
    ax.set_xlabel("飞行次数")
    ax.set_ylabel("")
    fig.tight_layout()
    save_fig(fig, output_dir, "05_top_tasks")


def plot_airport_map(airport: pd.DataFrame, output_dir: Path) -> None:
    """机场点位散点图（Web Mercator + OSM 底图）。"""
    gdf = to_mercator(airport)
    minx, miny, maxx, maxy = gdf.total_bounds
    fig, ax = plt.subplots(figsize=geo_figsize(minx, miny, maxx, maxy))

    for code, group in gdf.groupby("factory_code"):
        code_str = str(code or "")
        label = FACTORY_LABELS.get(code_str, code_str or "未知")
        color = FACTORY_COLORS.get(code_str, "#95A5A6")
        xs = group.geometry.x
        ys = group.geometry.y
        ax.scatter(
            xs, ys,
            s=200,
            alpha=0.92,
            label=label,
            c=color,
            edgecolors="white",
            linewidths=1.0,
            zorder=3,
        )
        for _, row in group.iterrows():
            name = str(row.get("model_name") or row.get("device_sn") or "")
            if len(name) > 12:
                name = name[:10] + "…"
            ax.annotate(
                name,
                (row.geometry.x, row.geometry.y),
                fontsize=9,
                alpha=0.9,
                color="#2C3E50",
                xytext=(5, 5),
                textcoords="offset points",
                zorder=4,
            )

    ax.set_title("机场点位分布", fontsize=14, pad=10)
    finalize_geo_ax(ax)
    ax.legend(title="厂商", loc="upper left", framealpha=0.92, fontsize=10, title_fontsize=10)
    fig.subplots_adjust(left=0.02, right=0.98, top=0.94, bottom=0.02)
    save_fig(fig, output_dir, "06_airport_map", tight=False)


def plot_route_distance(route: pd.DataFrame, output_dir: Path) -> None:
    """预定义航线距离分布。"""
    dist = route.loc[route["distance"] > 0, "distance"] / 1000

    fig, ax = plt.subplots(figsize=(7, 4))
    sns.histplot(dist, bins=30, kde=True, ax=ax, color="#C44E52")
    ax.set_title("航线距离分布")
    ax.set_xlabel("距离（公里）")
    ax.set_ylabel("航线数量")
    fig.tight_layout()
    save_fig(fig, output_dir, "07_route_distance")


def plot_battery_sample(telemetry: pd.DataFrame, output_dir: Path) -> None:
    """选取一次有电池数据的飞行，绘制电量随时间变化。"""
    valid = telemetry.dropna(subset=["battery_capacity_percent", "message_time"])
    if valid.empty:
        logger.warning("无电池数据，跳过电量曲线图")
        return

    # 选遥测点最多且电池数据较完整的一次飞行
    flight_counts = valid.groupby("fly_record_id").size().to_frame("point_count").reset_index()
    sample_id = flight_counts.loc[flight_counts["point_count"].idxmax(), "fly_record_id"]
    sample = (
        valid.loc[valid["fly_record_id"] == sample_id]
        .sort_values(by="message_time")
        .copy()
    )
    sample["message_time"] = pd.to_datetime(sample["message_time"])
    t0 = sample["message_time"].iloc[0]
    sample["elapsed_min"] = (sample["message_time"] - t0).dt.total_seconds() / 60

    fig, ax = plt.subplots(figsize=(9, 4))
    ax.plot(
        sample["elapsed_min"],
        sample["battery_capacity_percent"],
        color="#E67E22",
        linewidth=1.5,
    )
    ax.fill_between(
        sample["elapsed_min"],
        sample["battery_capacity_percent"],
        alpha=0.15,
        color="#E67E22",
    )
    ax.set_title(f"单次飞行电量变化（fly_record_id={sample_id}）")
    ax.set_xlabel("起飞后时间（分钟）")
    ax.set_ylabel("电量（%）")
    ax.set_ylim(0, 100)
    fig.tight_layout()
    save_fig(fig, output_dir, "08_battery_sample")


def plot_telemetry_tracks(telemetry: pd.DataFrame, output_dir: Path, max_flights: int = 5) -> None:
    """叠加绘制若干次飞行的 GPS 轨迹（Web Mercator + OSM 底图）。"""
    valid = telemetry.dropna(subset=["longitude", "latitude", "fly_record_id"])
    valid = valid[(valid["longitude"] != 0) | (valid["latitude"] != 0)]

    flight_counts = valid.groupby("fly_record_id").size().to_frame("point_count").reset_index()
    top_ids = flight_counts.nlargest(max_flights, "point_count")["fly_record_id"]

    lines_mercator: list[gpd.GeoSeries] = []
    for fid in top_ids:
        track = valid.loc[valid["fly_record_id"] == fid].sort_values(by="message_time")
        line = gpd.GeoSeries(
            [LineString(zip(track["longitude"], track["latitude"]))],
            crs="EPSG:4326",
        ).to_crs(epsg=3857)
        lines_mercator.append(line)

    bounds = gpd.GeoSeries(
        [line.iloc[0] for line in lines_mercator],
        crs="EPSG:3857",
    ).total_bounds
    minx, miny, maxx, maxy = bounds
    fig, ax = plt.subplots(figsize=geo_figsize(minx, miny, maxx, maxy))
    palette = sns.color_palette("tab10", n_colors=len(top_ids))

    for color, fid, line in zip(palette, top_ids, lines_mercator):
        xs, ys = line.iloc[0].xy
        ax.plot(xs, ys, linewidth=2.5, alpha=0.88, color=color, label=f"#{fid}", zorder=2)
        ax.scatter(
            xs[0], ys[0], s=80, color=color, marker="o",
            edgecolors="white", linewidths=0.8, zorder=3,
        )

    ax.set_title(f"飞行轨迹示例（Top {max_flights} 遥测点最多的飞行）", fontsize=14, pad=10)
    finalize_geo_ax(ax)
    ax.legend(title="飞行记录 ID", fontsize=10, title_fontsize=10, loc="upper left", framealpha=0.92)
    fig.subplots_adjust(left=0.02, right=0.98, top=0.94, bottom=0.02)
    save_fig(fig, output_dir, "09_flight_tracks", tight=False)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run(data_dir: Path, output_dir: Path) -> None:
    """生成全部图表。"""
    setup_style()
    logger.info("数据目录: %s", data_dir)
    logger.info("输出目录: %s", output_dir)

    tables = load_tables(data_dir)
    record = tables["fly_record"]
    telemetry = tables["fly_telemetry"]
    airport = tables["airport"]
    route = tables["route"]

    logger.info("生成图表 …")
    plot_monthly_flights(record, output_dir)
    plot_factory_distribution(record, airport, output_dir)
    plot_fly_type(record, output_dir)
    plot_duration_distance(record, output_dir)
    plot_top_tasks(record, output_dir)
    plot_airport_map(airport, output_dir)
    plot_route_distance(route, output_dir)
    plot_battery_sample(telemetry, output_dir)
    plot_telemetry_tracks(telemetry, output_dir)

    logger.info("可视化完成，共 %d 张图 → %s", len(list(output_dir.glob("*.png"))), output_dir)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="越城区无人机数据简单可视化")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=f"清洗后数据目录（默认: {DEFAULT_DATA_DIR.name}）",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"图表输出目录（默认: {DEFAULT_OUTPUT_DIR.name}）",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.data_dir.exists():
        raise FileNotFoundError(f"数据目录不存在: {args.data_dir}")
    run(args.data_dir, args.output_dir)


if __name__ == "__main__":
    main()
