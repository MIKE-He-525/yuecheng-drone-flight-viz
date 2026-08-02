#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
越城区无人机飞行服务数据清洗脚本

环境：Anaconda env `coding`
  C:\\Users\\hetia\\anaconda3\\envs\\coding\\python.exe

用法：
  python clean_data.py
  python clean_data.py --keep-no-message-time   # 保留无 message_time 的遥测行

流程概要：
  1. 读取 4 张源表（飞行记录 / 遥测分片 / 机场 / 航线）
  2. 分表清洗（删标记行、删冗余列、哨兵值转 NULL、类型转换）
  3. 可选：跨表关联有效性标注 + 字段/行级质量统计
  4. 写出 CSV、JSON 日志与 README

输出（默认写入 clean_data/）：
  - fly_record.csv / fly_telemetry.csv / airport.csv / route.csv
  - clean_summary.json   清洗后各表 Schema 与空值统计
  - clean_log.json       结构化清洗日志（行数变化、步骤、关联核查）
  - clean_log.txt        可读文本日志
  - README.md            清洗后数据库说明
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# 路径配置
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_INPUT_DIR = PROJECT_ROOT / "越城无人机数据+字段描述"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "clean_data"

FILE_RECORD = "越城区无人机飞行服务飞行数据记录信息.csv"
FILE_AIRPORT = "越城区无人机飞行服务机场点位信息1.csv"
FILE_ROUTE = "越城区无人机飞行服务航线信息.csv"
DIR_TELEMETRY = "越城区无人机飞行服务飞行数据上报信息-csv"
TELEMETRY_GLOB = "越城区无人机飞行服务飞行数据上报信息*.csv"

# 厂商代码 → 中文名（供质量报告与文档引用，清洗流程本身不写入 CSV）
FACTORY_LABELS = {"DJ": "大疆", "YD": "一电", "ZH": "纵横", "MMC": "科比特"}

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------

def read_csv(path: Path) -> pd.DataFrame:
    """读取 UTF-8 BOM 编码的 CSV，空字符串转为 NaN。"""
    df = pd.read_csv(path, encoding="utf-8-sig", dtype=str)
    return df.replace({"": np.nan, " ": np.nan})


def to_nullable_int(series: pd.Series) -> pd.Series:
    """将列转为可空整数类型 Int64。"""
    return pd.to_numeric(series, errors="coerce").astype("Int64")


def to_float(series: pd.Series) -> pd.Series:
    """将列转为浮点数，无法解析的值变为 NaN。"""
    return pd.to_numeric(series, errors="coerce")


def to_datetime(series: pd.Series) -> pd.Series:
    """将列转为 datetime，无法解析的值变为 NaT。"""
    return pd.to_datetime(series, errors="coerce")


def zero_to_null(series: pd.Series) -> pd.Series:
    """将哨兵值 0 / 0.0 转为 NaN（用于外键字段）。"""
    numeric = pd.to_numeric(series, errors="coerce")
    return series.mask(numeric == 0)


def zero_float_to_null(series: pd.Series) -> pd.Series:
    """将浮点列中的哨兵值 0 转为 NaN（如 endurance、distance）。"""
    numeric = to_float(series)
    return numeric.mask(numeric == 0)


def drop_all_null_columns(
    df: pd.DataFrame,
    columns: list[str],
) -> pd.DataFrame:
    """按列名列表删除指定列（忽略不存在的列名）。"""
    existing = [c for c in columns if c in df.columns]
    return df.drop(columns=existing, errors="ignore")


def filter_not_deleted(
    df: pd.DataFrame,
    col: str = "is_deleted",
) -> pd.DataFrame:
    """过滤掉删除标记为 1 的行。"""
    if col not in df.columns:
        return df
    deleted = pd.to_numeric(df[col], errors="coerce")
    deleted = deleted.fillna(0)
    return df[deleted != 1].copy()


def record_step(
    log: list[dict[str, Any]] | None,
    step: str,
    rows_before: int,
    rows_after: int,
    **details: Any,
) -> None:
    """记录清洗步骤到 pipeline 日志。"""
    if log is None:
        return
    entry: dict[str, Any] = {
        "step": step,
        "rows_before": rows_before,
        "rows_after": rows_after,
        "removed": rows_before - rows_after,
    }
    entry.update(details)
    log.append(entry)


def table_summary(df: pd.DataFrame, name: str) -> dict[str, Any]:
    """生成表的行数、列名、类型与空值统计摘要。"""
    return {
        "name": name,
        "rows": int(len(df)),
        "columns": list(df.columns),
        "column_count": len(df.columns),
        "dtypes": {c: str(df[c].dtype) for c in df.columns},
        "null_counts": {c: int(df[c].isna().sum()) for c in df.columns},
    }


def setup_file_logging(log_path: Path) -> None:
    """追加文件日志 handler。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_path, encoding="utf-8", mode="w")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)


# ---------------------------------------------------------------------------
# battery_info JSON 展开
# ---------------------------------------------------------------------------

def expand_battery_info(df: pd.DataFrame) -> pd.DataFrame:
    """解析 battery_info JSON，提取常用电池字段。"""

    def parse_one(raw: Any) -> dict[str, Any]:
        """解析单条 battery_info JSON 为扁平字典。"""
        if pd.isna(raw) or not str(raw).strip():
            return {}
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}

        result: dict[str, Any] = {
            "battery_capacity_percent": data.get("capacity_percent"),
            "battery_landing_power": data.get("landing_power"),
            "battery_return_home_power": data.get("return_home_power"),
            "battery_remain_flight_time": data.get("remain_flight_time"),
        }
        batteries = data.get("batteries") or []
        if batteries:
            # 多电池时仅取首块电池的 SN / 电压等（遥测上报常见结构）
            b0 = batteries[0]
            result.update(
                {
                    "battery_sn": b0.get("sn"),
                    "battery_voltage": b0.get("voltage"),
                    "battery_temperature": b0.get("temperature"),
                    "battery_loop_times": b0.get("loop_times"),
                }
            )
        return result

    parsed = df["battery_info"].apply(parse_one).apply(pd.Series)
    out = pd.concat([df.drop(columns=["battery_info"]), parsed], axis=1)
    return out


# ---------------------------------------------------------------------------
# 分表清洗（fly_record / fly_telemetry / airport / route）
# ---------------------------------------------------------------------------

def load_telemetry(input_dir: Path) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """读取遥测目录下所有分片 CSV 并按行合并，返回合并表与各文件行数统计。"""
    tel_dir = input_dir / DIR_TELEMETRY
    files = sorted(tel_dir.glob(TELEMETRY_GLOB))
    if not files:
        raise FileNotFoundError(f"未找到遥测分片文件: {tel_dir / TELEMETRY_GLOB}")

    logger.info("合并遥测分片: %d 个文件", len(files))
    file_stats: list[dict[str, Any]] = []
    parts: list[pd.DataFrame] = []
    for f in files:
        part = read_csv(f)
        file_stats.append({"file": f.name, "rows": len(part)})
        parts.append(part)
    merged = pd.concat(parts, ignore_index=True)
    return merged, file_stats


def clean_fly_record(
    df: pd.DataFrame,
    log: list[dict[str, Any]] | None = None,
) -> pd.DataFrame:
    """清洗飞行数据记录表。

    步骤：过滤软删除 → 删 pilot_id → 外键哨兵 0 转 NULL → 整型/浮点/时间类型转换。
    """
    logger.info("清洗飞行数据记录 …")
    before = len(df)
    record_step(log, "input", before, before)

    df = filter_not_deleted(df)
    record_step(log, "filter_not_deleted", before, len(df))
    before = len(df)

    df = drop_all_null_columns(df, ["pilot_id"])
    record_step(log, "drop_columns", before, len(df), columns=["pilot_id"])

    # 外键字段：源库用 0 表示「无关联」，统一转为 NULL 便于下游 JOIN
    for col in ["mission_id", "task_id", "task_record_id"]:
        if col in df.columns:
            df[col] = zero_to_null(df[col])
    record_step(log, "zero_to_null_foreign_keys", len(df), len(df), columns=["mission_id", "task_id", "task_record_id"])

    for col in ["id", "task_id", "task_record_id", "mission_id"]:
        if col in df.columns:
            df[col] = to_nullable_int(df[col])

    int_cols = [
        "fly_type", "media_count", "upload_count",
        "real_duration", "is_deleted",
    ]
    for col in int_cols:
        if col in df.columns:
            df[col] = to_nullable_int(df[col])

    if "real_distance" in df.columns:
        df["real_distance"] = to_float(df["real_distance"])

    for col in ["real_start_time", "real_end_time"]:
        if col in df.columns:
            df[col] = to_datetime(df[col])

    record_step(log, "type_conversion", len(df), len(df))
    return df


def clean_fly_telemetry(
    df: pd.DataFrame,
    *,
    drop_no_message_time: bool,
    log: list[dict[str, Any]] | None = None,
) -> pd.DataFrame:
    """清洗飞行数据上报（时序遥测）表。

    步骤：过滤软删除 → 删 ext_info → mission_id 哨兵处理 → 可选丢弃无 message_time 行
    → 类型转换 → 展开 battery_info JSON → GPS 质量审计（(0,0) 仅统计不删行）。
    """
    logger.info("清洗飞行数据上报 …")
    before = len(df)
    record_step(log, "input", before, before)

    df = filter_not_deleted(df)
    record_step(log, "filter_not_deleted", before, len(df))
    before = len(df)

    df = drop_all_null_columns(df, ["ext_info"])
    record_step(log, "drop_columns", before, len(df), columns=["ext_info"])

    if "mission_id" in df.columns:
        df["mission_id"] = zero_to_null(df["mission_id"])
    record_step(log, "zero_to_null_mission_id", len(df), len(df))

    # 默认丢弃无有效 message_time 的行（时序分析必需）；--keep-no-message-time 可保留
    if drop_no_message_time and "message_time" in df.columns:
        before = len(df)
        df["message_time"] = to_datetime(df["message_time"])
        df = df[df["message_time"].notna()].copy()
        removed = before - len(df)
        logger.info("  丢弃无 message_time 的行: %d", removed)
        record_step(log, "drop_no_message_time", before, len(df), reason="message_time 为空或无法解析")
    elif "message_time" in df.columns:
        df["message_time"] = to_datetime(df["message_time"])

    int_cols = [
        "id", "fly_record_id", "mission_id", "flight_time",
        "current_waypoint_index", "mission_percent",
    ]
    for col in int_cols:
        if col in df.columns:
            df[col] = to_nullable_int(df[col])

    float_cols = [
        "longitude", "latitude", "height", "elevation",
        "pitch", "roll", "head", "flight_distance",
    ]
    for col in float_cols:
        if col in df.columns:
            df[col] = to_float(df[col])

    for col in ["create_time", "update_time"]:
        if col in df.columns:
            df[col] = to_datetime(df[col])

    if "battery_info" in df.columns:
        before_cols = len(df.columns)
        df = expand_battery_info(df)
        record_step(
            log,
            "expand_battery_info",
            len(df),
            len(df),
            columns_added=len(df.columns) - before_cols + 1,
        )

    record_step(log, "type_conversion", len(df), len(df))

    # GPS 质量审计：经纬度 (0,0) 视为无效定位点，仅记入日志，不删除行
    if {"longitude", "latitude"}.issubset(df.columns):
        n = len(df)
        lon = pd.to_numeric(df["longitude"], errors="coerce")
        lat = pd.to_numeric(df["latitude"], errors="coerce")
        has_gps = lon.notna() & lat.notna()
        zero_zero = has_gps & (lon == 0) & (lat == 0)
        missing_gps = ~has_gps
        valid_gps = has_gps & ~zero_zero
        zero_n = int(zero_zero.sum())
        valid_n = int(valid_gps.sum())
        missing_n = int(missing_gps.sum())
        record_step(
            log,
            "audit_zero_zero_gps",
            n,
            n,
            reason=(
                f"经纬度 (0,0) 视为无效 GPS，不删除行；"
                f"无效 {zero_n} 条，缺失经纬度 {missing_n} 条，有效 GPS {valid_n} 条"
            ),
            columns=["longitude", "latitude"],
            zero_zero_points=zero_n,
            rows_missing_gps=missing_n,
            valid_gps_points=valid_n,
        )
        if zero_n:
            logger.info(
                "  (0,0) 无效 GPS: %d 条（保留在 CSV；有效 GPS %d 条）",
                zero_n,
                valid_n,
            )

    return df


def clean_airport(
    df: pd.DataFrame,
    log: list[dict[str, Any]] | None = None,
) -> pd.DataFrame:
    """清洗机场点位表。

    步骤：过滤软删除 → 删冗余列（update_time / area_* / model_id）→ 坐标与时间类型转换。
    """
    logger.info("清洗机场点位 …")
    before = len(df)
    record_step(log, "input", before, before)

    df = filter_not_deleted(df)
    record_step(log, "filter_not_deleted", before, len(df))
    before = len(df)

    drop_cols = ["update_time", "area_name", "area_code", "model_id"]
    df = drop_all_null_columns(df, drop_cols)
    record_step(log, "drop_columns", before, len(df), columns=drop_cols)

    for col in ["longitude", "latitude", "height", "relative_height"]:
        if col in df.columns:
            df[col] = to_float(df[col])

    if "air_line" in df.columns:
        df["air_line"] = to_nullable_int(df["air_line"])

    if "create_time" in df.columns:
        df["create_time"] = to_datetime(df["create_time"])

    record_step(log, "type_conversion", len(df), len(df))
    return df


def clean_route(
    df: pd.DataFrame,
    log: list[dict[str, Any]] | None = None,
) -> pd.DataFrame:
    """清洗航线信息表。

    步骤：删冗余列 → 过滤 is_delete → org_id 哨兵处理 → 整型/浮点/时间转换
    → endurance 等字段的 0 哨兵值转 NULL（保留行，供下游区分「未填写」与「真实零值缺失」）。
    """
    logger.info("清洗航线信息 …")
    before = len(df)
    record_step(log, "input", before, before)

    df = drop_all_null_columns(df, ["drone_sub_type", "heading_angle"])
    record_step(log, "drop_columns", before, len(df), columns=["drone_sub_type", "heading_angle"])
    before = len(df)

    if "is_delete" in df.columns:
        is_delete = pd.to_numeric(df["is_delete"], errors="coerce")
        df["is_delete"] = is_delete.fillna(0).astype("Int64")
        df = df[df["is_delete"] != 1].copy()
        record_step(log, "filter_is_delete", before, len(df))

    if "org_id" in df.columns:
        df["org_id"] = zero_to_null(df["org_id"])

    int_cols = [
        "id", "creat_type", "mission_version", "type", "mission_type",
        "mission_application", "head_mode", "gimbal_pitch_mode",
        "finish_action", "is_delete",
    ]
    for col in int_cols:
        if col in df.columns:
            df[col] = to_nullable_int(df[col])

    # 整型哨兵：0 表示「未设置」，转 NULL 后仍保留该行
    sentinel_int_cols = ["mission_version", "mission_application"]
    sentinel_details: dict[str, int] = {}
    for col in sentinel_int_cols:
        if col in df.columns:
            zeros = int((pd.to_numeric(df[col], errors="coerce") == 0).sum())
            df[col] = zero_to_null(df[col].astype("string"))
            df[col] = to_nullable_int(df[col])
            sentinel_details[col] = zeros
    if sentinel_details:
        record_step(log, "zero_to_null_sentinel_int", len(df), len(df), columns=sentinel_details)

    # 浮点哨兵：endurance / distance / pitch 的 0 同理视为缺失
    sentinel_float_details: dict[str, int] = {}
    for col in ["endurance", "distance", "pitch"]:
        if col in df.columns:
            zeros = int((pd.to_numeric(df[col], errors="coerce") == 0).sum())
            df[col] = zero_float_to_null(df[col])
            sentinel_float_details[col] = zeros
    if sentinel_float_details:
        record_step(log, "zero_to_null_sentinel_float", len(df), len(df), columns=sentinel_float_details)

    if "cjsj" in df.columns:
        df["cjsj"] = to_datetime(df["cjsj"])

    record_step(log, "type_conversion", len(df), len(df))
    return df


# ---------------------------------------------------------------------------
# 跨表一致性标注
# ---------------------------------------------------------------------------

def add_integrity_flags(
    record: pd.DataFrame,
    telemetry: pd.DataFrame,
    airport: pd.DataFrame,
    route: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """为记录表、遥测表添加关联有效性布尔列，不删除行。

    新增列：mission_id_valid / dock_sn_valid / fly_record_id_valid。
    策略为「仅标注、不剔除」，下游可按 *_valid 自行过滤。
    """

    # 预收集各表主键/外键集合，用于 O(1) 成员检测
    route_ids = set(route["id"].dropna().astype(str))
    dock_sns = set(airport["device_sn"].dropna().astype(str))
    record_ids = set(record["id"].dropna().astype(str))

    rec = record.copy()
    if "mission_id" in rec.columns:
        mission_ids = rec["mission_id"].astype("string")
        rec["mission_id_valid"] = mission_ids.isin(route_ids)
    if "dock_sn" in rec.columns:
        dock_ids = rec["dock_sn"].astype("string")
        rec["dock_sn_valid"] = dock_ids.isin(dock_sns)

    tel = telemetry.copy()
    if "fly_record_id" in tel.columns:
        fly_ids = tel["fly_record_id"].astype("string")
        tel["fly_record_id_valid"] = fly_ids.isin(record_ids)
    if "mission_id" in tel.columns:
        tel_mission_ids = tel["mission_id"].astype("string")
        tel["mission_id_valid"] = tel_mission_ids.isin(route_ids)

    return rec, tel


def compute_integrity_stats(
    record: pd.DataFrame,
    telemetry: pd.DataFrame,
) -> dict[str, Any]:
    """汇总 *_valid 标记列的 True/False 计数及 GPS 覆盖概况（写入 clean_log.json）。"""
    stats: dict[str, Any] = {}

    def flag_counts(df: pd.DataFrame, col: str) -> dict[str, int]:
        if col not in df.columns:
            return {}
        vc = df[col].value_counts(dropna=False)
        return {str(k): int(v) for k, v in vc.items()}

    stats["fly_record.mission_id_valid"] = flag_counts(record, "mission_id_valid")
    stats["fly_record.dock_sn_valid"] = flag_counts(record, "dock_sn_valid")
    stats["fly_telemetry.fly_record_id_valid"] = flag_counts(telemetry, "fly_record_id_valid")
    stats["fly_telemetry.mission_id_valid"] = flag_counts(telemetry, "mission_id_valid")

    # 有效 GPS：经纬度均非空，且不同时为 (0,0)
    tel_valid = telemetry.dropna(subset=["longitude", "latitude"])
    tel_valid = tel_valid[(tel_valid["longitude"] != 0) | (tel_valid["latitude"] != 0)]
    stats["fly_telemetry.valid_gps_points"] = int(len(tel_valid))
    stats["fly_telemetry.distinct_fly_record_ids"] = int(telemetry["fly_record_id"].nunique())

    return stats


def compute_quality_report(
    record: pd.DataFrame,
    telemetry: pd.DataFrame,
    airport: pd.DataFrame,
    route: pd.DataFrame,
) -> dict[str, Any]:
    """字段级 / 行级质量统计（写入 clean_log.json 的 quality 段）。"""
    report: dict[str, Any] = {
        "policy": {
            "fly_record_id": (
                "保留全部遥测行，不删除 fly_record_id 无法关联 fly_record.id 的记录；"
                "添加布尔列 fly_record_id_valid 供下游过滤。"
                "（对应 数据说明.md 7.6：可选排除，默认仅标注）"
            ),
            "endurance_mission_fields": (
                "route 表 endurance / distance / mission_version / mission_application "
                "的哨兵值 0 清洗为 NULL，保留行。"
            ),
        },
        "fields": {},
        "rows": {},
        "gps": {},
    }

    for table_name, df, cols in (
        ("route", route, ["endurance", "distance", "mission_version", "mission_application", "factory_code"]),
        ("fly_record", record, ["factory_code", "mission_id", "dock_sn"]),
        ("airport", airport, ["factory_code"]),
    ):
        for col in cols:
            if col not in df.columns:
                continue
            null_n = int(df[col].isna().sum())
            report["fields"][f"{table_name}.{col}"] = {
                "null": null_n,
                "non_null": int(len(df) - null_n),
                "null_pct": round(null_n / len(df) * 100, 1) if len(df) else 0,
            }

    # 行级：统计 fly_record_id / mission_id 无法关联的遥测行及样例 ID
    if "fly_record_id_valid" in telemetry.columns:
        invalid = telemetry[~telemetry["fly_record_id_valid"].fillna(False)]
        invalid_ids = invalid["fly_record_id"].dropna().unique()
        sample_ids = sorted(int(x) for x in invalid_ids[:20])
        report["rows"]["fly_telemetry.invalid_fly_record_id"] = {
            "invalid_rows": int(len(invalid)),
            "invalid_pct": round(len(invalid) / len(telemetry) * 100, 1) if len(telemetry) else 0,
            "distinct_invalid_ids": int(len(invalid_ids)),
            "sample_invalid_ids": sample_ids,
        }

    if "mission_id_valid" in telemetry.columns:
        bad = telemetry[~telemetry["mission_id_valid"].fillna(False) & telemetry["mission_id"].notna()]
        report["rows"]["fly_telemetry.invalid_mission_id"] = {
            "rows_with_mission_id": int(telemetry["mission_id"].notna().sum()),
            "invalid_mission_id_rows": int(len(bad)),
        }

    # GPS / 轨迹：按 fly_record_id 分组统计可画线轨迹数（≥2 个有效点才成线）
    tel = telemetry.copy()
    tel["longitude"] = pd.to_numeric(tel["longitude"], errors="coerce")
    tel["latitude"] = pd.to_numeric(tel["latitude"], errors="coerce")
    has_gps = tel.dropna(subset=["longitude", "latitude"])
    zero_zero = has_gps[(has_gps["longitude"] == 0) & (has_gps["latitude"] == 0)]
    valid_gps = has_gps[~((has_gps["longitude"] == 0) & (has_gps["latitude"] == 0))]
    track_sizes = valid_gps.groupby("fly_record_id").size()
    report["gps"] = {
        "telemetry_rows_total": int(len(telemetry)),
        "rows_missing_gps": int(len(telemetry) - len(has_gps)),
        "zero_zero_points": int(len(zero_zero)),
        "valid_gps_points": int(len(valid_gps)),
        "distinct_fly_record_ids_valid_gps": int(valid_gps["fly_record_id"].nunique()),
        "tracks_with_ge_2_points": int((track_sizes >= 2).sum()),
        "tracks_with_1_point": int((track_sizes == 1).sum()),
        "note": (
            "有效 GPS = 经纬度非空且非 (0,0)。"
            "看板「轨迹」数 = tracks_with_ge_2_points（至少 2 点方可画线）。"
            "行政区内/外见 admin。"
        ),
    }

    try:
        from geo_utils import compute_admin_gps_stats

        report["admin"] = compute_admin_gps_stats(
            valid_gps["longitude"].astype(float),
            valid_gps["latitude"].astype(float),
        )
    except FileNotFoundError as exc:
        report["admin"] = {"error": str(exc)}

    for table_name, df in (("fly_record", record), ("airport", airport), ("route", route)):
        if "factory_code" not in df.columns:
            continue
        vc = df["factory_code"].fillna("NULL").astype(str).value_counts()
        report["fields"][f"{table_name}.factory_code_values"] = {
            str(k): int(v) for k, v in vc.items()
        }

    return report


def append_quality_report_txt(log_path: Path, quality: dict[str, Any]) -> None:
    """在 clean_log.txt 末尾追加可读的质量统计。"""
    lines = [
        "",
        "=" * 60,
        "字段 / 行级质量统计（quality）",
        "=" * 60,
        "",
        "【策略】",
        f"- fly_record_id: {quality['policy']['fly_record_id']}",
        f"- endurance 等: {quality['policy']['endurance_mission_fields']}",
        "",
    ]

    if quality.get("rows"):
        lines.append("【行级】")
        for key, val in quality["rows"].items():
            lines.append(f"- {key}:")
            for k, v in val.items():
                lines.append(f"    {k}: {v}")
        lines.append("")

    if quality.get("gps"):
        lines.append("【GPS / 轨迹】")
        for k, v in quality["gps"].items():
            lines.append(f"- {k}: {v}")
        lines.append("")

    if quality.get("admin"):
        lines.append("【行政区内 / 外（越城区 330602）】")
        for k, v in quality["admin"].items():
            lines.append(f"- {k}: {v}")
        lines.append("")

    if quality.get("fields"):
        lines.append("【字段空值 / 厂商分布】（节选）")
        for key in sorted(quality["fields"]):
            if key.endswith("_values"):
                lines.append(f"- {key}: {quality['fields'][key]}")
            elif any(x in key for x in ("endurance", "mission_version", "mission_application", "factory_code")):
                lines.append(f"- {key}: {quality['fields'][key]}")
        lines.append("")

    with log_path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# 保存与文档
# ---------------------------------------------------------------------------

def save_table(df: pd.DataFrame, path: Path) -> None:
    """将 DataFrame 保存为 UTF-8 BOM 编码的 CSV 文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    logger.info("  已保存: %s (%d 行)", path.name, len(df))


def write_database_readme(
    output_dir: Path,
    summary: dict[str, Any],
    clean_log: dict[str, Any],
) -> None:
    """根据 summary 与 clean_log 自动生成 clean_data/README.md（表概览、ER、清洗摘要）。"""
    tables = {t["name"]: t for t in summary["tables"]}
    meta = clean_log["meta"]
    pipeline = clean_log["pipeline"]
    integrity = clean_log.get("integrity", {})

    def row_delta(table: str) -> str:
        p = pipeline.get(table, {})
        return f"{p.get('rows_in', '?')} → {p.get('rows_out', '?')}"

    lines = [
        "# 清洗后数据库说明",
        "",
        "> 由 `clean_data.py` 自动生成，请勿手工编辑。",
        "",
        "## 概述",
        "",
        f"- **生成时间：** {meta['generated_at']}",
        f"- **原始数据目录：** `{meta['input_dir']}`",
        "- **编码格式：** UTF-8 with BOM（`utf-8-sig`），可用 Excel 直接打开",
        f"- **总数据量：** {sum(t['rows'] for t in summary['tables']):,} 行（4 张表）",
        "",
        "## 目录文件",
        "",
        "| 文件 | 说明 |",
        "|------|------|",
        "| `fly_record.csv` | 飞行记录 |",
        "| `fly_telemetry.csv` | 合并遥测（电池字段已展开） |",
        "| `airport.csv` | 机场点位 |",
        "| `route.csv` | 预定义航线 |",
        "| `clean_summary.json` | 各表 Schema、类型、空值统计 |",
        "| `clean_log.json` | 结构化清洗日志 |",
        "| `clean_log.txt` | 文本运行日志 |",
        "",
        "## 数据表一览",
        "",
        "| 表名 | 清洗前行数 | 清洗后行数 | 列数 | 说明 |",
        "|------|-----------|-----------|------|------|",
    ]

    desc = {
        "fly_record": "单次飞行任务记录，含任务名、时长、距离、设备关联",
        "fly_telemetry": "飞行过程 GPS/姿态/电量时序点",
        "airport": "机库/机场静态信息与坐标",
        "route": "预定义航线元数据（无航点坐标）",
    }
    for name in ("fly_record", "fly_telemetry", "airport", "route"):
        t = tables[name]
        p = pipeline.get(name, {})
        lines.append(
            f"| `{name}` | {p.get('rows_in', '—')} | {t['rows']:,} | {t['column_count']} | {desc[name]} |"
        )

    lines.extend([
        "",
        "## 实体关系",
        "",
        "```",
        "airport.device_sn  ←──  fly_record.dock_sn",
        "route.id           ←──  fly_record.mission_id",
        "fly_record.id      ←──  fly_telemetry.fly_record_id",
        "route.id           ←──  fly_telemetry.mission_id",
        "```",
        "",
        "## 关联有效性标记",
        "",
        "清洗脚本添加了布尔标记列（不删行，便于后续过滤）：",
        "",
    ])

    flag_desc = {
        "fly_record.mission_id_valid": "`mission_id` 能否关联到 `route.id`",
        "fly_record.dock_sn_valid": "`dock_sn` 能否关联到 `airport.device_sn`",
        "fly_telemetry.fly_record_id_valid": "`fly_record_id` 能否关联到 `fly_record.id`",
        "fly_telemetry.mission_id_valid": "`mission_id` 能否关联到 `route.id`",
    }
    for key, counts in integrity.items():
        if key.startswith("fly_") and isinstance(counts, dict):
            parts = ", ".join(f"{k}: {v:,}" for k, v in counts.items())
            lines.append(f"- **{key}**（{flag_desc.get(key, '')}）：{parts}")
    if "fly_telemetry.valid_gps_points" in integrity:
        lines.append(f"- **有效 GPS 遥测点**（非空且非 0,0）：{integrity['fly_telemetry.valid_gps_points']:,}")
    if "fly_telemetry.distinct_fly_record_ids" in integrity:
        lines.append(f"- **涉及飞行记录数**：{integrity['fly_telemetry.distinct_fly_record_ids']}")

    lines.extend([
        "",
        "## 清洗摘要",
        "",
    ])
    for name in ("fly_record", "fly_telemetry", "airport", "route"):
        p = pipeline.get(name, {})
        removed = p.get("rows_in", 0) - p.get("rows_out", 0)
        lines.append(f"### `{name}`（{row_delta(name)}，删除 {removed} 行）")
        for step in p.get("steps", []):
            if step.get("removed", 0) > 0 or step["step"] in ("drop_columns", "expand_battery_info", "input"):
                detail = ""
                if "columns" in step:
                    detail = f"，列：{step['columns']}"
                if "reason" in step:
                    detail = f"，{step['reason']}"
                lines.append(
                    f"- `{step['step']}`：{step['rows_before']} → {step['rows_after']}"
                    f"（-{step['removed']}）{detail}"
                )
        lines.append("")

    tel_source = clean_log.get("sources", {}).get("fly_telemetry", {})
    if tel_source.get("files"):
        lines.append("### 遥测分片合并")
        lines.append("")
        lines.append("| 分片文件 | 行数 |")
        lines.append("|----------|------|")
        for f in tel_source["files"]:
            lines.append(f"| `{f['file']}` | {f['rows']:,} |")
        lines.append(f"| **合计** | **{tel_source.get('rows_merged', 0):,}** |")
        lines.append("")

    lines.extend([
        "## 重新生成",
        "",
        "```bash",
        "python clean_data.py",
        "```",
        "",
        "完整字段定义见项目根目录 `数据说明.md`。",
        "",
    ])

    readme_path = output_dir / "README.md"
    readme_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("  已保存: %s", readme_path.name)


def save_outputs(
    tables: dict[str, pd.DataFrame],
    output_dir: Path,
    clean_log: dict[str, Any],
) -> dict[str, Any]:
    """写出 4 张 CSV、clean_summary.json、clean_log.json 及 README.md。"""
    names = {
        "fly_record": "fly_record",
        "fly_telemetry": "fly_telemetry",
        "airport": "airport",
        "route": "route",
    }
    summaries = []
    for key, filename in names.items():
        save_table(tables[key], output_dir / f"{filename}.csv")
        summaries.append(table_summary(tables[key], filename))

    generated_at = clean_log["meta"]["generated_at"]
    summary = {
        "meta": {
            "generated_at": generated_at,
            "input_dir": clean_log["meta"]["input_dir"],
            "output_dir": str(output_dir),
            "options": clean_log["meta"]["options"],
        },
        "row_counts": {t["name"]: t["rows"] for t in summaries},
        "tables": summaries,
    }
    summary_path = output_dir / "clean_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("  已保存: %s", summary_path.name)

    log_path = output_dir / "clean_log.json"
    log_path.write_text(json.dumps(clean_log, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("  已保存: %s", log_path.name)

    write_database_readme(output_dir, summary, clean_log)
    return summary


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run(
    input_dir: Path,
    output_dir: Path,
    drop_no_message_time: bool,
    add_flags: bool,
) -> dict[str, Any]:
    """执行完整清洗流程：读源 → 分表清洗 → 关联标注 → 写输出，返回 summary 字典。"""
    generated_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")

    log_txt_path = output_dir / "clean_log.txt"
    setup_file_logging(log_txt_path)

    logger.info("输入目录: %s", input_dir)
    logger.info("输出目录: %s", output_dir)
    logger.info(
        "选项: drop_no_message_time=%s, add_integrity_flags=%s",
        drop_no_message_time,
        add_flags,
    )

    clean_log: dict[str, Any] = {
        "meta": {
            "generated_at": generated_at,
            "script": "clean_data.py",
            "input_dir": str(input_dir),
            "output_dir": str(output_dir),
            "options": {
                "drop_no_message_time": drop_no_message_time,
                "add_integrity_flags": add_flags,
            },
        },
        "sources": {},
        "pipeline": {},
        "integrity": {},
    }

    # --- 阶段 1：读取原始 CSV ---
    raw_record = read_csv(input_dir / FILE_RECORD)
    raw_airport = read_csv(input_dir / FILE_AIRPORT)
    raw_route = read_csv(input_dir / FILE_ROUTE)
    raw_telemetry, tel_file_stats = load_telemetry(input_dir)

    clean_log["sources"] = {
        "fly_record": {"file": FILE_RECORD, "rows": len(raw_record)},
        "fly_telemetry": {
            "directory": DIR_TELEMETRY,
            "file_count": len(tel_file_stats),
            "files": tel_file_stats,
            "rows_merged": len(raw_telemetry),
        },
        "airport": {"file": FILE_AIRPORT, "rows": len(raw_airport)},
        "route": {"file": FILE_ROUTE, "rows": len(raw_route)},
    }

    record_log: list[dict[str, Any]] = []
    tel_log: list[dict[str, Any]] = []
    airport_log: list[dict[str, Any]] = []
    route_log: list[dict[str, Any]] = []

    # --- 阶段 2：分表清洗 ---
    fly_record = clean_fly_record(raw_record, record_log)
    fly_telemetry = clean_fly_telemetry(
        raw_telemetry,
        drop_no_message_time=drop_no_message_time,
        log=tel_log,
    )
    airport = clean_airport(raw_airport, airport_log)
    route = clean_route(raw_route, route_log)

    # --- 阶段 3：跨表关联标注与质量报告（可选） ---
    if add_flags:
        logger.info("添加跨表关联有效性标记 …")
        fly_record, fly_telemetry = add_integrity_flags(
            fly_record, fly_telemetry, airport, route,
        )
        clean_log["integrity"] = compute_integrity_stats(fly_record, fly_telemetry)
        clean_log["quality"] = compute_quality_report(
            fly_record, fly_telemetry, airport, route,
        )

    # 汇总各表清洗前后行数及逐步明细，写入 clean_log["pipeline"]
    for name, steps, rows_in, df in [
        ("fly_record", record_log, len(raw_record), fly_record),
        ("fly_telemetry", tel_log, len(raw_telemetry), fly_telemetry),
        ("airport", airport_log, len(raw_airport), airport),
        ("route", route_log, len(raw_route), route),
    ]:
        clean_log["pipeline"][name] = {
            "rows_in": rows_in,
            "rows_out": len(df),
            "rows_removed": rows_in - len(df),
            "steps": steps,
        }

    # --- 阶段 4：汇总 pipeline 日志并写出文件 ---
    tables = {
        "fly_record": fly_record,
        "fly_telemetry": fly_telemetry,
        "airport": airport,
        "route": route,
    }
    summary = save_outputs(tables, output_dir, clean_log)
    if clean_log.get("quality"):
        append_quality_report_txt(log_txt_path, clean_log["quality"])
    logger.info("清洗完成。")
    return summary


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="越城区无人机飞行服务数据清洗")
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help=f"原始数据目录（默认: {DEFAULT_INPUT_DIR.name}）",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"清洗结果输出目录（默认: {DEFAULT_OUTPUT_DIR.name}）",
    )
    parser.add_argument(
        "--keep-no-message-time",
        action="store_true",
        help="保留无 message_time 的遥测行（默认会丢弃）",
    )
    parser.add_argument(
        "--no-integrity-flags",
        action="store_true",
        help="不添加 mission_id_valid / dock_sn_valid 等关联标记列",
    )
    return parser.parse_args()


def main() -> None:
    """脚本入口：解析参数并运行清洗流程。"""
    args = parse_args()
    if not args.input_dir.exists():
        raise FileNotFoundError(f"输入目录不存在: {args.input_dir}")

    run(
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        drop_no_message_time=not args.keep_no_message_time,
        add_flags=not args.no_integrity_flags,
    )


if __name__ == "__main__":
    main()
