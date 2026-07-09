# Netlify 部署目录

本目录用于部署到 [Netlify](https://www.netlify.com/)。**业务数据内嵌在 `index.html` 中**，请勿提交到公开 Git 仓库。

## 生成

在项目根目录执行：

```bash
python clean_data.py
python dashboard/build_dashboard.py
```

成功后本目录会有：

| 文件 | 说明 |
|------|------|
| `index.html` | 单页看板（总览 + 地图，约 1.3 MB，**本地生成**） |
| `netlify.toml` | Netlify 配置（可提交到 Git） |
| `README.md` | 本说明 |

## 部署方式

### 方式 A：拖拽部署（最简单）

1. 打开 Netlify → **Add new site** → **Deploy manually**
2. 将 **`dashboard/netlify/` 整个文件夹**（须含 `index.html`）拖入上传区
3. 部署完成后访问分配的 `*.netlify.app` 域名

### 方式 B：连接 GitHub 仓库

1. Netlify → **Import from Git** → 选择本仓库
2. 设置：
   - **Base directory**：留空
   - **Build command**：`python clean_data.py && python dashboard/build_dashboard.py`  
     （CI 环境需有原始/清洗数据；若仅部署静态包，见方式 A）
   - **Publish directory**：`dashboard/netlify`
3. 若仓库无数据文件，建议在本地构建后使用方式 A，或用 Netlify CLI 上传本目录。

### 方式 C：Netlify CLI

```bash
npm install -g netlify-cli
cd dashboard/netlify
netlify deploy --prod
```

## 依赖说明

站点需 **联网** 加载：

- Google Fonts
- Chart.js、Leaflet（CDN）
- CARTO 地图瓦片

## 隐私

部署站点含飞行与地理信息，请使用 Netlify **Password protection** 或仅在内网/受控环境分享链接。
