# dronedatadashboard

越城区无人机飞行服务数据的清洗、可视化与交互看板项目。

## 功能

- **数据清洗**：`clean_data.py` 将原始 CSV 清洗并输出到 `clean_data/`
- **静态图表**：`visualize.py` 生成统计 PNG（需联网加载 OSM 底图）
- **交互看板**：`dashboard/` 含单文件 `dashboard.html`（总览 + 地图，数据内嵌）

## 环境

Python 3.10+ 推荐。

```bash
pip install -r requirements.txt
```

## 快速开始

```bash
# 1. 清洗数据（需本地放置原始 CSV，见 数据说明.md）
python clean_data.py

# 2. 生成静态图（可选）
python visualize.py

# 3. 构建看板
python dashboard/build_dashboard.py
```

然后双击打开 `dashboard/dashboard.html`（需联网加载 Chart.js、Leaflet 与地图瓦片）。

## 目录结构

```
├── clean_data.py          # 清洗脚本
├── visualize.py           # 静态可视化
├── dashboard/             # HTML 看板
│   ├── build_dashboard.py
│   └── dashboard.html     # 单文件交付物（推荐）
├── clean_data/            # 清洗后 CSV 与日志
└── 数据说明.md             # 字段说明、清洗规则与进度记录
```

## 数据说明

- 本仓库**不包含**原始数据目录 `越城无人机数据+字段描述/`，需自行准备。
- 字段定义、实体关系、清洗规则详见 [`数据说明.md`](数据说明.md)。
- 清洗后数据库说明见 [`clean_data/README.md`](clean_data/README.md)。

## 许可与隐私

数据涉及无人机飞行与地理信息，请勿公开传播敏感原始数据；本仓库建议使用 **Private** 可见性。
