# 内容管理后台系统

一个开箱即用的轻量级后台管理系统（CMS），用于管理网站内容。单端口 HTTP 服务，零第三方依赖，可直接部署到任何支持 Python 的环境。

## 功能

| 模块 | 说明 |
| --- | --- |
| 登录认证 | 账号密码登录，PBKDF2 密码散列，Token 会话（7 天有效），支持记住登录、修改密码、禁用账号 |
| 数据看板 | 内容规模 / 发布率 / 浏览量 / 媒体占用等 KPI，近 14 天新增与发布趋势图、分类分布环形图、热门内容 Top 6、最近操作记录 |
| 列表管理 | 关键词检索、分类 / 状态筛选、多字段排序、分页、多选批量（发布 / 草稿 / 归档 / 删除）、详情预览弹窗 |
| 表单编辑 | 标题必填校验、摘要字数统计、Markdown 编辑与实时预览、标签管理、封面上传（点击 / 拖拽）、草稿与发布流转 |
| 分类管理 | 分类的增删改、标识色、排序、内容计数 |
| 媒体库 | 多文件拖拽上传、图片缩略预览、复制链接、删除，文件类型与大小白名单校验 |
| 用户管理 | 多账号、角色（管理员 / 编辑）、启用禁用、重置密码 |
| 操作日志 | 记录登录、内容增删改、文件上传等关键动作，支持检索与分页 |

## 技术栈

- **后端**：Python 3 标准库（`http.server` / `sqlite3` / `hashlib` / `json`），无任何第三方依赖
- **数据持久化**：SQLite（`data/app.db`），服务端持久保存
- **文件存储**：服务器 `uploads/` 目录，通过 `/uploads/<name>` 提供访问
- **前端**：原生 HTML / CSS / JavaScript，Chart.js 4（已随项目内置，无需外网 CDN）

## 运行

```bash
python app.py            # 默认 8000 端口
PORT=9000 python app.py  # 自定义端口
```

打开 `http://127.0.0.1:8000` 进入登录页，登录后进入 `/admin` 后台。

## 演示账号

| 账号 | 密码 | 角色 |
| --- | --- | --- |
| `admin` | `admin123` | 管理员 |
| `editor` | `editor123` | 编辑 |
| `lisi` | `lisi123` | 编辑 |

> 上线前请务必在「个人设置 → 修改密码」中修改默认密码，并在「用户管理」中移除不需要的演示账号。

## 目录结构

```
├── app.py                 # 服务端入口（路由 / API / 认证 / 数据库）
├── server.py / main.py    # 等价启动入口（兼容不同环境的启动文件探测）
├── static/
│   ├── index.html         # 登录页
│   ├── admin.html         # 后台主界面
│   ├── css/style.css      # 样式系统（响应式）
│   ├── js/api.js          # API 客户端与通用工具
│   ├── js/app.js          # 前端应用（路由 + 各功能视图）
│   ├── vendor/chart.umd.min.js
│   └── assets/covers/     # 演示封面
├── data/                  # SQLite 数据库（运行时生成）
└── uploads/               # 上传文件（运行时生成）
```

## 响应式布局

- **≥ 1100px**：侧边栏常驻，多列网格
- **≤ 980px**：侧边栏收起为抽屉（汉堡按钮唤出），图表改为单列
- **≤ 720px**：数据表格自动切换为卡片式列表，表单单列，KPI 单列

## 主要接口

```
POST   /api/auth/login | logout | profile | password
GET    /api/auth/me
GET    /api/dashboard/overview
GET    /api/contents?page&size&keyword&category_id&status&sort&order
POST   /api/contents          PUT /api/contents/:id   DELETE /api/contents/:id
POST   /api/contents/batch    { ids:[], action: delete|published|draft|archived }
GET    /api/categories        POST/PUT/DELETE /api/categories/:id
GET    /api/media             DELETE /api/media/:id
POST   /api/upload            multipart/form-data
GET    /api/users             POST/PUT/DELETE /api/users/:id
GET    /api/logs?page&size&keyword
```

所有 `/api/*` 接口（除登录外）均需携带 `Authorization: Bearer <token>`。
