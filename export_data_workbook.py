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


def pipeline_steps_df(pipeline: dict[str, Any]) -> pd.DataFrame:
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
            rows.append(row)
    return pd.DataFrame(rows)


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
                    "清洗后四表行数与 CSV 文件名",
                    "清洗后每字段类型与空值统计",
                    "各表前 50 行样例（全量在 CSV）",
                    "原始文件行数、遥测分片清单",
                    "逐步骤行数变化与涉及列",
                    "mission_id_valid 等标记统计",
                    "fly_record_id 等清洗策略文字说明",
                    "字段空值、厂商分布等",
                    "无效 fly_record_id 等行级统计",
                    "有效 GPS、轨迹数等",
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
        tbl_overview = pd.DataFrame(
            [
                {"表": k, "清洗后行数": v, "CSV文件": f"{k}.csv"}
                for k, v in counts.items()
            ]
        )
        write_sheet(writer, "10_清洗后_表一览", tbl_overview)
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

        write_sheet(writer, "13_日志_清洗步骤", pipeline_steps_df(clean_log.get("pipeline", {})))
        write_sheet(writer, "14_日志_关联完整性", integrity_df(clean_log.get("integrity", {})))

        if quality.get("policy"):
            write_sheet(writer, "15_质量_策略", pd.DataFrame([{"策略": k, "说明": v} for k, v in quality["policy"].items()]))
        write_sheet(writer, "16_质量_字段", quality_fields_df(quality.get("fields", {})))
        write_sheet(writer, "17_质量_行级", quality_rows_df(quality.get("rows", {})))

        gps = quality.get("gps", {})
        admin = quality.get("admin", {})
        gps_df = pd.DataFrame([{"指标": k, "值": v} for k, v in gps.items()])
        admin_df = pd.DataFrame([{"指标": k, "值": v} for k, v in admin.items()])
        write_sheet(writer, "18_质量_GPS轨迹", gps_df)
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
