#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""导出「数据说明 + 清洗需求 + 清洗后库 + 清洗日志」Excel 交付物。"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_MD = PROJECT_ROOT / "数据说明.md"
DEFAULT_CLEAN_DIR = PROJECT_ROOT / "clean_data"
DEFAULT_OUTPUT = DEFAULT_CLEAN_DIR / "数据说明与清洗交付.xlsx"

SAMPLE_ROWS = 50


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def section_slice(text: str, start: str, end: str | None = None, *, heading_level: int = 2) -> str:
    """提取 markdown 章节正文；heading_level=2 为 ##，3 为 ###。"""
    hashes = "#" * heading_level
    m = re.search(rf"^{hashes}\s+{re.escape(start)}", text, flags=re.MULTILINE)
    if not m:
        return ""
    start_idx = m.end()
    if end:
        m2 = re.search(rf"^#{{2,{heading_level}}}\s+{re.escape(end)}", text[start_idx:], flags=re.MULTILINE)
        end_idx = start_idx + m2.start() if m2 else len(text)
    else:
        end_idx = len(text)
    return text[start_idx:end_idx]


def parse_md_tables(block: str) -> list[pd.DataFrame]:
    """解析 markdown  pipe 表格为 DataFrame 列表。"""
    lines = block.splitlines()
    tables: list[pd.DataFrame] = []
    buf: list[str] = []

    def flush() -> None:
        nonlocal buf
        if len(buf) < 2:
            buf = []
            return
        rows = []
        for line in buf:
            if not line.strip().startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            rows.append(cells)
        buf = []
        if len(rows) < 2:
            return
        header = rows[0]
        body = [r for r in rows[2:] if not all(re.match(r"^:?-+:?$", c) for c in r)]
        if not body:
            return
        width = len(header)
        norm = [r + [""] * (width - len(r)) if len(r) < width else r[:width] for r in body]
        tables.append(pd.DataFrame(norm, columns=header))

    for line in lines:
        if line.strip().startswith("|"):
            buf.append(line)
        elif buf:
            flush()
    flush()
    return tables


def pick_table(tables: list[pd.DataFrame], header_hint: str) -> pd.DataFrame:
    for t in tables:
        cols = " ".join(str(c) for c in t.columns)
        if header_hint in cols:
            return t
    return pd.DataFrame()


def flatten_dict(d: dict[str, Any], prefix: str = "") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            rows.extend(flatten_dict(v, key))
        else:
            rows.append({"键": key, "值": v})
    return rows


def pipeline_steps_df(pipeline: dict[str, Any], quality: dict[str, Any] | None = None) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for table, info in pipeline.items():
        for step in info.get("steps", []):
            row = {
                "表": table,
                "步骤": step.get("step"),
                "清洗前": step.get("rows_before"),
                "清洗后": step.get("rows_after"),
                "删除行数": step.get("removed"),
            }
            if "columns" in step:
                cols = step["columns"]
                row["涉及列"] = ", ".join(cols) if isinstance(cols, list) else json.dumps(cols, ensure_ascii=False)
            if "reason" in step:
                row["说明"] = step["reason"]
            elif step.get("zero_zero_points") is not None:
                row["说明"] = (
                    f"(0,0) 无效 GPS {step['zero_zero_points']} 条；"
                    f"有效 GPS {step.get('valid_gps_points', '—')} 条（不删行）"
                )
            rows.append(row)

    df = pd.DataFrame(rows)
    gps = (quality or {}).get("gps", {})
    tel_steps = pipeline.get("fly_telemetry", {}).get("steps", [])
    if gps.get("zero_zero_points") is not None and not any(
        s.get("step") == "audit_zero_zero_gps" for s in tel_steps
    ):
        total = int(gps.get("telemetry_rows_total", 0))
        zero = int(gps["zero_zero_points"])
        valid = int(gps.get("valid_gps_points", total - zero))
        extra = pd.DataFrame(
            [
                {
                    "表": "fly_telemetry",
                    "步骤": "audit_zero_zero_gps",
                    "清洗前": total,
                    "清洗后": total,
                    "删除行数": 0,
                    "涉及列": "longitude, latitude",
                    "说明": (
                        f"识别经纬度 (0,0) 无效 GPS 共 {zero} 条，"
                        f"从有效 GPS 统计中排除（有效 {valid} 条）；"
                        f"行仍保留在 CSV，共 {total} 行"
                    ),
                }
            ]
        )
        df = pd.concat([df, extra], ignore_index=True)
    return df


def integrity_df(integrity: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for key, counts in integrity.items():
        if isinstance(counts, dict):
            for flag, n in counts.items():
                rows.append({"标记列": key, "取值": flag, "行数": n})
        else:
            rows.append({"标记列": key, "取值": "—", "行数": counts})
    return pd.DataFrame(rows)


def quality_fields_df(fields: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for key, val in fields.items():
        if key.endswith("_values") and isinstance(val, dict):
            for code, n in val.items():
                rows.append({"字段": key, "编码/取值": code, "数量": n})
        elif isinstance(val, dict):
            rows.append({"字段": key, **val})
        else:
            rows.append({"字段": key, "值": val})
    return pd.DataFrame(rows)


def quality_rows_df(rows_section: dict[str, Any]) -> pd.DataFrame:
    out: list[dict[str, Any]] = []
    for name, val in rows_section.items():
        if not isinstance(val, dict):
            continue
        row = {"类别": name}
        for k, v in val.items():
            row[k] = json.dumps(v, ensure_ascii=False) if isinstance(v, list) else v
        out.append(row)
    return pd.DataFrame(out)


def schema_df(summary: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for table in summary.get("tables", []):
        name = table["name"]
        dtypes = table.get("dtypes", {})
        nulls = table.get("null_counts", {})
        for col in table.get("columns", []):
            rows.append(
                {
                    "表": name,
                    "字段名": col,
                    "清洗后类型": dtypes.get(col, ""),
                    "空值数": nulls.get(col, ""),
                    "非空数": table["rows"] - nulls.get(col, 0) if col in nulls else "",
                    "表总行数": table["rows"],
                }
            )
    return pd.DataFrame(rows)


def all_fields_sheet(md_text: str) -> pd.DataFrame:
    specs = [
        ("3. 飞行数据记录信息", "fly_record", "4. 飞行数据上报信息"),
        ("4. 飞行数据上报信息", "fly_telemetry", "5. 机场点位信息"),
        ("5. 机场点位信息", "airport", "6. 航线信息"),
        ("6. 航线信息", "route", "7. 数据清洗方案"),
    ]
    parts: list[pd.DataFrame] = []
    for start, table_key, end in specs:
        block = section_slice(md_text, start, end)
        t = pick_table(parse_md_tables(block), "字段名")
        if not t.empty:
            t = t.copy()
            t.insert(0, "表", table_key)
            parts.append(t)
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


def cleaning_req_sheet(md_text: str, section: str, next_section: str, table_key: str) -> pd.DataFrame:
    block = section_slice(md_text, section, next_section, heading_level=3)
    tables = parse_md_tables(block)
    type_tbl = pick_table(tables, "目标类型")
    dirty_tbl = pick_table(tables, "处理方式")
    if type_tbl.empty and dirty_tbl.empty:
        return pd.DataFrame()
    type_tbl = type_tbl.copy()
    type_tbl.insert(0, "类别", "目标类型")
    type_tbl.insert(0, "表", table_key)
    dirty_tbl = dirty_tbl.copy()
    dirty_tbl.insert(0, "类别", "脏数据")
    dirty_tbl.insert(0, "表", table_key)
    return pd.concat([type_tbl, dirty_tbl], ignore_index=True)


def overview_rows(md_text: str) -> pd.DataFrame:
    block = section_slice(md_text, "1. 数据概览", "2. 数据实体关系")
    return pick_table(parse_md_tables(block), "数据表")


def relations_rows(md_text: str) -> pd.DataFrame:
    block = section_slice(md_text, "2. 数据实体关系", "3. 飞行数据记录信息")
    return pick_table(parse_md_tables(block), "关联")


def implemented_rules(md_text: str) -> pd.DataFrame:
    block = section_slice(md_text, "7.6 跨表一致性清洗", "7.7", heading_level=3)
    tables = parse_md_tables(block)
    for t in tables:
        cols = " ".join(str(c) for c in t.columns)
        if "清洗规则" in cols or ("字段" in cols and "原始情况" in cols):
            return t
    return pd.DataFrame()


def gap_rules(md_text: str) -> pd.DataFrame:
    block = section_slice(md_text, "7.6 跨表一致性清洗", "7.7", heading_level=3)
    tables = parse_md_tables(block)
    for t in tables:
        if "建议" in " ".join(str(c) for c in t.columns):
            return t
    return pd.DataFrame()


def table_overview_df(counts: dict[str, int], quality: dict[str, Any]) -> pd.DataFrame:
    """清洗后表行数；fly_telemetry 单独标注 CSV 行数 vs 有效 GPS 口径。"""
    gps = quality.get("gps", {})
    rows: list[dict[str, Any]] = []
    for name, row_count in counts.items():
        row: dict[str, Any] = {
            "表": name,
            "清洗后行数(CSV)": row_count,
            "CSV文件": f"{name}.csv",
            "有效GPS行数": "",
            "其中(0,0)无效点": "",
            "说明": "",
        }
        if name == "fly_telemetry":
            total = int(gps.get("telemetry_rows_total", row_count))
            valid = gps.get("valid_gps_points")
            zero = gps.get("zero_zero_points")
            missing = gps.get("rows_missing_gps", 0)
            row["有效GPS行数"] = valid
            row["其中(0,0)无效点"] = zero
            row["说明"] = (
                f"CSV 共 {total} 行；看板「有效遥测点」= {valid} "
                f"（= {total} - 缺失经纬度 {missing} - (0,0) 无效 {zero}；"
                f"无效 GPS 行仍保留在 CSV，见工作表「18b_(0,0)GPS明细」）"
            )
        rows.append(row)
    return pd.DataFrame(rows)


def gps_quality_summary_df(gps: dict[str, Any]) -> pd.DataFrame:
    """GPS/轨迹质量指标（含 92815 与 92795 的验算说明）。"""
    total = int(gps.get("telemetry_rows_total", 0))
    missing = int(gps.get("rows_missing_gps", 0))
    zero = int(gps.get("zero_zero_points", 0))
    valid = int(gps.get("valid_gps_points", 0))
    rows = [
        {
            "指标": "遥测 CSV 总行 (telemetry_rows_total)",
            "值": total,
            "说明": "clean_data/fly_telemetry.csv 清洗后行数；对应 KPI 字段 telemetry_total_rows",
        },
        {
            "指标": "缺失经纬度 (rows_missing_gps)",
            "值": missing,
            "说明": "longitude 或 latitude 为空",
        },
        {
            "指标": "(0,0) 无效 GPS (zero_zero_points)",
            "值": zero,
            "说明": "经纬度均为 0，视为无有效定位；不参与看板统计与轨迹绘制，行仍保留在 CSV",
        },
        {
            "指标": "有效 GPS 点 (valid_gps_points)",
            "值": valid,
            "说明": "看板 KPI「有效遥测点」；经纬度非空且非 (0,0)",
        },
        {
            "指标": "验算：CSV 总行 - 缺失 - (0,0)",
            "值": f"{total} - {missing} - {zero} = {valid}",
            "说明": "与有效 GPS 点一致" if total - missing - zero == valid else "请核对 clean_log",
        },
        {
            "指标": "有效 GPS 对应 fly_record_id 数",
            "值": gps.get("distinct_fly_record_ids_valid_gps"),
            "说明": "至少有一条有效 GPS 的飞行 id 去重数",
        },
        {
            "指标": "可绘制轨迹数 (tracks_with_ge_2_points)",
            "值": gps.get("tracks_with_ge_2_points"),
            "说明": "至少 2 个有效 GPS 点，看板地图「轨迹」KPI",
        },
        {
            "指标": "仅 1 个有效 GPS 的 fly_record_id",
            "值": gps.get("tracks_with_1_point"),
            "说明": "有 GPS 但不足以画线",
        },
    ]
    note = gps.get("note")
    if note:
        rows.append({"指标": "备注", "值": note, "说明": ""})
    return pd.DataFrame(rows)


def zero_gps_detail_df(clean_dir: Path) -> pd.DataFrame:
    """(0,0) 无效 GPS 逐条明细及按 fly_record_id 汇总。"""
    csv = clean_dir / "fly_telemetry.csv"
    if not csv.exists():
        return pd.DataFrame()
    tel = pd.read_csv(csv, encoding="utf-8-sig")
    zero = tel[(tel["longitude"] == 0) & (tel["latitude"] == 0)].copy()
    if zero.empty:
        return pd.DataFrame({"说明": ["（无 (0,0) 无效 GPS 行）"]})

    prefer = [
        "fly_record_id",
        "fly_record_id_valid",
        "message_time",
        "longitude",
        "latitude",
        "height",
        "speed",
        "mission_id",
    ]
    cols = [c for c in prefer if c in zero.columns]
    detail = zero[cols].sort_values(["fly_record_id", "message_time"], na_position="last")
    detail.insert(0, "序号", range(1, len(detail) + 1))

    summary = (
        zero.groupby("fly_record_id", as_index=False)
        .agg(无效GPS条数=("fly_record_id", "size"), 最早时间=("message_time", "min"), 最晚时间=("message_time", "max"))
        .sort_values("fly_record_id")
    )
    summary.insert(0, "类别", "按 fly_record_id 汇总")
    detail.insert(0, "类别", "逐条明细")
    summary_cols = ["类别", "fly_record_id", "无效GPS条数", "最早时间", "最晚时间"]
    detail_cols = ["类别", "序号"] + [c for c in detail.columns if c not in ("类别", "序号")]
    combined = pd.concat(
        [
            summary.reindex(columns=summary_cols),
            detail.reindex(columns=detail_cols),
        ],
        ignore_index=True,
    )
    return combined


def write_sheet(
    writer: pd.ExcelWriter,
    name: str,
    df: pd.DataFrame,
    *,
    index: bool = False,
) -> None:
    safe = name[:31]
    if df is None or df.empty:
        pd.DataFrame({"说明": ["（暂无数据）"]}).to_excel(writer, sheet_name=safe, index=False)
    else:
        df.to_excel(writer, sheet_name=safe, index=index)


def build_workbook(
    md_path: Path,
    clean_dir: Path,
    output_path: Path,
    *,
    include_samples: bool = True,
) -> Path:
    md_text = read_text(md_path)
    summary = json.loads((clean_dir / "clean_summary.json").read_text(encoding="utf-8"))
    clean_log = json.loads((clean_dir / "clean_log.json").read_text(encoding="utf-8"))
    quality = clean_log.get("quality", {})

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        # 目录（最先写入，便于 Excel 中排在前列）
        toc = pd.DataFrame(
            {
                "工作表": [
                    "00_目录",
                    "01_数据概览",
                    "02_实体关系",
                    "03_全部字段说明",
                    "04_厂商编码",
                    "05_清洗需求_共通",
                    "06_清洗需求_明细",
                    "07_清洗规则_已实现",
                    "08_关联缺口",
                    "09_清洗后_元信息",
                    "10_清洗后_表一览",
                    "11_清洗后_字段Schema",
                    "样例_fly_record / airport / route / fly_telemetry",
                    "12_日志_源数据",
                    "13_日志_清洗步骤",
                    "14_日志_关联完整性",
                    "15_质量_策略",
                    "16_质量_字段",
                    "17_质量_行级",
                    "18_质量_GPS轨迹",
                    "18b_(0,0)GPS明细",
                    "19_质量_行政区",
                ],
                "内容": [
                    "本工作簿索引",
                    "四表行数、粒度、时间范围",
                    "表间关联键说明",
                    "全部原始字段（中文名、类型、说明）",
                    "DJ/YD/ZH 厂商对照",
                    "共通结构性清洗项",
                    "分表类型转换与脏数据处理需求",
                    "clean_data.py 已实现的跨表规则",
                    "仍存在的关联缺口与建议",
                    "本次清洗运行元信息",
                    "清洗后四表行数；遥测 CSV 行数 vs 有效 GPS 口径",
                    "清洗后每字段类型与空值统计",
                    "各表前 50 行样例（全量在 CSV）",
                    "原始文件行数、遥测分片清单",
                    "逐步骤行数变化与涉及列",
                    "mission_id_valid 等标记统计",
                    "fly_record_id 等清洗策略文字说明",
                    "字段空值、厂商分布等",
                    "无效 fly_record_id 等行级统计",
                    "有效 GPS、轨迹数及 92815→92795 验算",
                    "20 条 (0,0) 无效 GPS 逐条与按飞行汇总",
                    "行政区内/外点数与占比",
                ],
            }
        )
        write_sheet(writer, "00_目录", toc)

        # --- 数据解读 ---
        write_sheet(writer, "01_数据概览", overview_rows(md_text))
        write_sheet(writer, "02_实体关系", relations_rows(md_text))
        write_sheet(writer, "03_全部字段说明", all_fields_sheet(md_text))
        write_sheet(writer, "04_厂商编码", pick_table(parse_md_tables(section_slice(md_text, "7.9 厂商编码对照", "7.10", heading_level=3)), "编码"))

        # --- 清洗需求 ---
        write_sheet(writer, "05_清洗需求_共通", pick_table(parse_md_tables(section_slice(md_text, "7.1 结构性清洗", "7.2", heading_level=3)), "清洗项"))
        reqs = [
            cleaning_req_sheet(md_text, "7.2 飞行数据记录信息", "7.3", "fly_record"),
            cleaning_req_sheet(md_text, "7.3 飞行数据上报信息", "7.4", "fly_telemetry"),
            cleaning_req_sheet(md_text, "7.4 机场点位信息", "7.5", "airport"),
            cleaning_req_sheet(md_text, "7.5 航线信息", "7.6", "route"),
        ]
        write_sheet(writer, "06_清洗需求_明细", pd.concat([r for r in reqs if not r.empty], ignore_index=True) if any(not r.empty for r in reqs) else pd.DataFrame())
        write_sheet(writer, "07_清洗规则_已实现", implemented_rules(md_text))
        write_sheet(writer, "08_关联缺口", gap_rules(md_text))

        # --- 清洗后数据库 ---
        meta = summary.get("meta", {})
        meta_df = pd.DataFrame(
            [
                {"项": "生成时间", "值": meta.get("generated_at", "")},
                {"项": "原始数据目录", "值": meta.get("input_dir", "")},
                {"项": "输出目录", "值": meta.get("output_dir", "")},
                {"项": "丢弃无 message_time", "值": meta.get("options", {}).get("drop_no_message_time")},
                {"项": "关联有效性标记", "值": meta.get("options", {}).get("add_integrity_flags")},
            ]
        )
        write_sheet(writer, "09_清洗后_元信息", meta_df)

        counts = summary.get("row_counts", {})
        write_sheet(writer, "10_清洗后_表一览", table_overview_df(counts, quality))
        write_sheet(writer, "11_清洗后_字段Schema", schema_df(summary))

        if include_samples:
            for name in ("fly_record", "airport", "route"):
                csv = clean_dir / f"{name}.csv"
                if csv.exists():
                    df = pd.read_csv(csv, encoding="utf-8-sig", nrows=SAMPLE_ROWS)
                    write_sheet(writer, f"样例_{name}", df)

            tel_csv = clean_dir / "fly_telemetry.csv"
            if tel_csv.exists():
                df = pd.read_csv(tel_csv, encoding="utf-8-sig", nrows=SAMPLE_ROWS)
                write_sheet(writer, "样例_fly_telemetry", df)

        # --- 清洗日志 ---
        sources = clean_log.get("sources", {})
        src_rows: list[dict[str, Any]] = []
        for table, info in sources.items():
            row = {"表": table, **{k: v for k, v in info.items() if k != "files"}}
            if "files" in info:
                row["分片文件数"] = len(info["files"])
            src_rows.append(row)
        write_sheet(writer, "12_日志_源数据", pd.DataFrame(src_rows))

        write_sheet(writer, "13_日志_清洗步骤", pipeline_steps_df(clean_log.get("pipeline", {}), quality))
        write_sheet(writer, "14_日志_关联完整性", integrity_df(clean_log.get("integrity", {})))

        if quality.get("policy"):
            write_sheet(writer, "15_质量_策略", pd.DataFrame([{"策略": k, "说明": v} for k, v in quality["policy"].items()]))
        write_sheet(writer, "16_质量_字段", quality_fields_df(quality.get("fields", {})))
        write_sheet(writer, "17_质量_行级", quality_rows_df(quality.get("rows", {})))

        gps = quality.get("gps", {})
        admin = quality.get("admin", {})
        write_sheet(writer, "18_质量_GPS轨迹", gps_quality_summary_df(gps))
        write_sheet(writer, "18b_(0,0)GPS明细", zero_gps_detail_df(clean_dir))
        admin_df = pd.DataFrame([{"指标": k, "值": v} for k, v in admin.items()])
        write_sheet(writer, "19_质量_行政区", admin_df)

    _autofit_columns(output_path)
    return output_path


def _autofit_columns(path: Path) -> None:
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter

    wb = load_workbook(path)
    for ws in wb.worksheets:
        for col_idx, column_cells in enumerate(ws.columns, 1):
            max_len = 0
            for cell in column_cells:
                val = "" if cell.value is None else str(cell.value)
                max_len = max(max_len, min(len(val), 60))
            ws.column_dimensions[get_column_letter(col_idx)].width = max(max_len + 2, 10)
        ws.freeze_panes = "A2"
    wb.save(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="导出数据说明与清洗交付 Excel")
    parser.add_argument("--md", type=Path, default=DEFAULT_MD)
    parser.add_argument("--clean-dir", type=Path, default=DEFAULT_CLEAN_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--no-samples", action="store_true", help="不包含各表样例数据")
    args = parser.parse_args()

    out = build_workbook(
        args.md,
        args.clean_dir,
        args.output,
        include_samples=not args.no_samples,
    )
    size_kb = out.stat().st_size // 1024
    print(f"已写入: {out} ({size_kb} KB)")


if __name__ == "__main__":
    main()
