# dronedatadashboard

越城区无人机飞行服务数据的清洗、可视化与交互看板项目。

## 功能

- **数据清洗**：`clean_data.py` 将原始 CSV 清洗并输出到 `clean_data/`
- **静态图表**：`visualize.py` 生成统计 PNG（需联网加载 OSM 底图）
- **交互看板**：`dashboard/` 含 HTML 模板与构建脚本；`dashboard.html` 为单文件交付物（本地生成）

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

# 3. 构建看板 JSON 与单文件 HTML
python dashboard/build_dashboard.py
```

**打开看板：**

- **推荐**：双击 `dashboard/dashboard.html`（单文件，数据内嵌，需联网加载 Chart.js / Leaflet / 地图瓦片）
- **开发调试**：`index.html` / `map.html` 为纯模板，通过 `fetch` 读取同目录下的 `data.json`、`map_data.json`，需本地 HTTP 服务：

```bash
cd dashboard
python -m http.server 8080
# 访问 http://localhost:8080/index.html 或 dashboard.html
```

## 目录结构

```
├── clean_data.py          # 清洗脚本
├── visualize.py           # 静态可视化
├── dashboard/
│   ├── build_dashboard.py # 导出 JSON + 生成 dashboard.html
│   ├── index.html         # 总览模板（仓库内无业务数据）
│   └── map.html           # 地图模板（仓库内无业务数据）
├── clean_data/            # 清洗日志与说明（CSV 本地生成）
└── 数据说明.md
```

## 数据与隐私

**仓库内不含业务数据**，仅含脚本与 HTML 模板。以下文件在 `.gitignore` 中，需本地生成：

| 路径 | 生成方式 |
|------|----------|
| `clean_data/*.csv` | `python clean_data.py` |
| `dashboard/data.json` | `python dashboard/build_dashboard.py` |
| `dashboard/map_data.json` | 同上 |
| `dashboard/dashboard.html` | 同上 |

克隆后请先准备原始数据并依次运行清洗与构建脚本。

- 原始数据目录 `越城无人机数据+字段描述/` 需自行准备，不上传 GitHub。
- 字段与清洗规则见 [`数据说明.md`](数据说明.md)。
- **注意**：若仓库曾推送过含数据的旧版本，Git 历史中可能仍保留；新 clone 的默认分支不会包含上述忽略文件。建议保持 **Private** 可见性。

## 许可与隐私

数据涉及无人机飞行与地理信息，请勿公开传播敏感数据；本仓库建议使用 **Private** 可见性。
