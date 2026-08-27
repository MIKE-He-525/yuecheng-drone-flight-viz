# Yuecheng Drone Flight Viz

越城区无人机飞行数据清洗、静态图与交互空间看板。

## 这是什么

本项目对越城区无人机飞行服务平台数据（2024-09～2026-05）进行清洗、可视化与交互分析，包含：

- **3,298** 条飞行记录
- **36** 个机场点位
- **92,795** 个有效 GPS 遥测点
- **786** 条预定义航线
- **563** 条可绘制 GPS 轨迹

数据源：`越城无人机数据+字段描述/`（原始 CSV + 字段描述）。字段定义、清洗规则与分析口径详见 [`数据说明.md`](数据说明.md)。

## 特性

- **数据清洗**：合并 10 个遥测分片，过滤软删除、哨兵值转 NULL、外键关联标记、行政区划判断
- **静态图表**：月度飞行趋势、厂商分布、时长距离分布、地图底图叠加轨迹（OSM 瓦片，300 DPI PNG）
- **交互看板**：单文件 HTML 看板（总览 KPI + 空间地图同页），支持多维筛选、时序曲线、2D/3D 视图、双轨对比
- **Excel 交付物**：字段说明 + 清洗需求 + 清洗后 Schema + 质量统计

## 快速开始

**环境要求**：Python 3.10+

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 清洗数据（原始数据在 越城无人机数据+字段描述/）
python clean_data.py
# → 输出: clean_data/ 下 CSV、日志与 README

# 3. 可选：生成静态图表
python visualize.py
# → 输出: figures/ 下 9 张 PNG（需联网拉取 OSM 瓦片）

# 4. 可选：导出 Excel 交付物
python export_data_workbook.py
# → 输出: clean_data/数据说明与清洗交付.xlsx

# 5. 构建交互看板
python dashboard/build_dashboard.py
# → 输出: dashboard/dashboard.html（约 3.5 MB，数据内嵌）

# 6. 打开看板
# 双击 dashboard/dashboard.html（需现代浏览器 + 联网加载 CDN）
```

## 看板

**文件**：`dashboard/dashboard.html`（单文件交付物）

**内容**：总览（KPI 卡片 + 统计图表 + 任务 Top 12 + 数据解读与法律备注）+ 空间地图（筛选 + 列表 + 轨迹图层 + 时序曲线 + 3D 视图）

**依赖**：需联网加载 Google Fonts、Chart.js、Leaflet、MapLibre GL、CARTO 瓦片、OpenFreeMap 立体建筑、AWS Terrarium DEM 地形

### 基本操作

- **打开**：双击 `dashboard/dashboard.html` 或拖入浏览器；首次打开需联网
- **筛选**：地图区左栏可按厂商、任务类型、起飞时间、时长、距离、高度、速度筛选；列表支持搜索与排序
- **选中轨迹**：点击列表或地图轨迹 → 右上角详情卡 + 底部时序抽屉（高度/速度/距离曲线）；选中后其余轨迹置灰
- **时序播放**：展开时序抽屉后，点击播放按钮（1×/3×/10×/30× 倍速），地图与曲线同步位置标注
- **3D 视图**：勾选图层「3D 视图」后进入 MapLibre 倾斜场景，显示立体建筑、地形、WebGL 细管航迹
- **双轨对比**：选中一条 →「加入对比」→ 再选另一条；对比模式下地图双轨高亮、时序曲线叠加、摘要表并列

### 更多功能

- 图层控制：机场点位/覆盖半径、飞行轨迹、行政区划、仅行政内轨迹、立体建筑、地形
- 时序抽屉箭头开合（收起仅留箭头条，展开时地图同步缩小）
- 主题切换（Claude / Material / Vercel / Linear / SaaS），同步更新图表配色与地图底图
- 列表可按点数/时长/距离排序，支持 ID、任务名、机场、机库 SN、任务 ID、无人机 SN 搜索

**完整看板使用指南**见 [`数据说明.md`](数据说明.md) §8.3～§8.4。

### 开发调试

修改 `index.html` / `map.html` / `map_section.css` 后：

```bash
python dashboard/build_dashboard.py
cd dashboard
python -m http.server 8080
# 访问 http://localhost:8080/dashboard.html
```

## 项目结构

```
├── clean_data.py                  # 清洗脚本
├── visualize.py                   # 静态图表生成
├── export_data_workbook.py        # Excel 交付物导出
├── geo_utils.py                   # 行政区划面判断
├── requirements.txt               # Python 依赖
├── README.md                      # 本说明
├── 数据说明.md                     # 进度、字段、清洗规则、看板完整指南
├── geo/                           # 越城区行政区划 GeoJSON（330602）
├── 越城无人机数据+字段描述/         # 原始 CSV + 字段描述（已入库）
├── clean_data/                    # 清洗后 CSV、日志、Excel 交付物
│   ├── fly_record.csv             # 飞行记录（3,298 条）
│   ├── fly_telemetry.csv          # 合并遥测（92,815 条，电池字段已展开）
│   ├── airport.csv                # 机场点位（36 个）
│   ├── route.csv                  # 预定义航线（786 条）
│   ├── clean_log.json / clean_log.txt
│   ├── clean_summary.json
│   ├── README.md                  # 清洗后数据库说明
│   └── 数据说明与清洗交付.xlsx
├── figures/                       # 静态图表 PNG（可选）
│   ├── 01_monthly_flights.png
│   ├── 02_factory_distribution.png
│   ├── 03_fly_type.png
│   ├── 04_duration_distance.png
│   ├── 05_top_tasks.png
│   ├── 06_airport_map.png
│   ├── 07_route_distance.png
│   ├── 08_battery_sample.png
│   └── 09_flight_tracks.png
└── dashboard/                     # 交互看板
    ├── build_dashboard.py         # 构建脚本
    ├── index.html                 # 总览页模板
    ├── map.html                   # 地图页模板
    ├── map_section.css            # 地图区样式唯一来源
    ├── data.json / map_data.json  # 看板数据
    └── dashboard.html             # 单文件交付物（约 3.5 MB）
```

## 依赖

**Python 包**（详见 `requirements.txt`）：

- `pandas` - 数据清洗与分析
- `numpy` - 数值计算
- `geopandas` - 地理空间处理（行政区划判断、轨迹空间分析）
- `shapely` - 几何对象
- `matplotlib` + `seaborn` - 静态图表
- `contextily` - OSM 底图瓦片
- `openpyxl` - Excel 交付物导出

**浏览器端**（看板自动从 CDN 加载）：

- Google Fonts - 界面字体
- Chart.js - 统计图表
- Leaflet - 2D 地图交互
- MapLibre GL JS - 3D 地图引擎
- CARTO 瓦片 - 底图
- OpenFreeMap - 立体建筑矢量瓦片
- AWS Terrarium DEM - 地形高程瓦片

---

**仓库**：[github.com/MIKE-He-525/yuecheng-drone-flight-viz](https://github.com/MIKE-He-525/Yuecheng-District-Drone-Flight-Service-Data-Visualization) (private)

**清洗与分析口径**：见 [`数据说明.md`](数据说明.md)（§1～§7 数据结构与清洗规则；§8 可视化与看板完整指南）
