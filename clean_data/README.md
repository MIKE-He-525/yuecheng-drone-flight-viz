# 清洗后数据库说明

> 由 `clean_data.py` 自动生成，请勿手工编辑。

## 概述

- **生成时间：** 2026-07-13 13:38:56 +0800
- **原始数据目录：** `D:\coding\DataPreprocessing\越城无人机数据+字段描述`
- **编码格式：** UTF-8 with BOM（`utf-8-sig`），可用 Excel 直接打开
- **总数据量：** 96,935 行（4 张表）

## 目录文件

| 文件 | 说明 |
|------|------|
| `fly_record.csv` | 飞行记录 |
| `fly_telemetry.csv` | 合并遥测（电池字段已展开） |
| `airport.csv` | 机场点位 |
| `route.csv` | 预定义航线 |
| `clean_summary.json` | 各表 Schema、类型、空值统计 |
| `clean_log.json` | 结构化清洗日志 |
| `clean_log.txt` | 文本运行日志 |

## 数据表一览

| 表名 | 清洗前行数 | 清洗后行数 | 列数 | 说明 |
|------|-----------|-----------|------|------|
| `fly_record` | 3298 | 3,298 | 23 | 单次飞行任务记录，含任务名、时长、距离、设备关联 |
| `fly_telemetry` | 100000 | 92,815 | 32 | 飞行过程 GPS/姿态/电量时序点 |
| `airport` | 36 | 36 | 25 | 机库/机场静态信息与坐标 |
| `route` | 786 | 786 | 21 | 预定义航线元数据（无航点坐标） |

## 实体关系

```
airport.device_sn  ←──  fly_record.dock_sn
route.id           ←──  fly_record.mission_id
fly_record.id      ←──  fly_telemetry.fly_record_id
route.id           ←──  fly_telemetry.mission_id
```

## 关联有效性标记

清洗脚本添加了布尔标记列（不删行，便于后续过滤）：

- **fly_record.mission_id_valid**（`mission_id` 能否关联到 `route.id`）：False: 2,339, True: 959
- **fly_record.dock_sn_valid**（`dock_sn` 能否关联到 `airport.device_sn`）：True: 3,081, False: 217
- **fly_telemetry.fly_record_id_valid**（`fly_record_id` 能否关联到 `fly_record.id`）：True: 68,860, False: 23,955
- **fly_telemetry.mission_id_valid**（`mission_id` 能否关联到 `route.id`）：False: 59,706, True: 33,109
- **有效 GPS 遥测点**（非空且非 0,0）：92,795
- **涉及飞行记录数**：565

## 清洗摘要

### `fly_record`（3298 → 3298，删除 0 行）
- `input`：3298 → 3298（-0）
- `drop_columns`：3298 → 3298（-0），列：['pilot_id']

### `fly_telemetry`（100000 → 92815，删除 7185 行）
- `input`：100000 → 100000（-0）
- `drop_columns`：100000 → 100000（-0），列：['ext_info']
- `drop_no_message_time`：100000 → 92815（-7185），message_time 为空或无法解析
- `expand_battery_info`：92815 → 92815（-0）

### `airport`（36 → 36，删除 0 行）
- `input`：36 → 36（-0）
- `drop_columns`：36 → 36（-0），列：['update_time', 'area_name', 'area_code', 'model_id']

### `route`（786 → 786，删除 0 行）
- `input`：786 → 786（-0）
- `drop_columns`：786 → 786（-0），列：['drone_sub_type', 'heading_angle']

### 遥测分片合并

| 分片文件 | 行数 |
|----------|------|
| `越城区无人机飞行服务飞行数据上报信息1.csv` | 10,000 |
| `越城区无人机飞行服务飞行数据上报信息10.csv` | 10,000 |
| `越城区无人机飞行服务飞行数据上报信息2.csv` | 10,000 |
| `越城区无人机飞行服务飞行数据上报信息3.csv` | 10,000 |
| `越城区无人机飞行服务飞行数据上报信息4.csv` | 10,000 |
| `越城区无人机飞行服务飞行数据上报信息5.csv` | 10,000 |
| `越城区无人机飞行服务飞行数据上报信息6.csv` | 10,000 |
| `越城区无人机飞行服务飞行数据上报信息7.csv` | 10,000 |
| `越城区无人机飞行服务飞行数据上报信息8.csv` | 10,000 |
| `越城区无人机飞行服务飞行数据上报信息9.csv` | 10,000 |
| **合计** | **100,000** |

## 重新生成

```bash
python clean_data.py
```

完整字段定义见项目根目录 `数据说明.md`。
