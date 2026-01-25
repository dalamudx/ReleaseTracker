# ReleaseTracker

🚀 一款轻量级、可配置的版本追踪软件，支持追踪 GitHub、GitLab、Helm Chart 等仓库的 release 版本。

![Python](https://img.shields.io/badge/Python-3.10+-blue)
![Vue](https://img.shields.io/badge/Vue-3.x-green)
![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-teal)
![License](https://img.shields.io/badge/License-MIT-yellow)

## ✨ 特性

- 📦 **多源支持**：GitHub、GitLab（含自托管）、Helm Chart
- ⚙️  **灵活配置**：YAML 配置文件，支持过滤规则
- 🔔 **通知推送**：Webhook 通知（支持扩展更多渠道）
- 🎯 **定时追踪**：自动定期检查版本更新
- 💾 **本地存储**：SQLite 数据库，轻量无依赖
- 🌐 **Web 界面**：Vue 3 现代化前端
- 🌓 **深色模式**：支持亮色/暗色主题

## 🏗️ 架构

```
┌─────────────┐
│  Vue 3 前端  │
│  (端口 5173) │
└──────┬──────┘
       │ REST API
       ▼
┌─────────────────┐
│  FastAPI 后端    │
│  (端口 8000)    │
└────────┬────────┘
         │
    ┌────┴────┬────────┬─────────┐
    ▼         ▼        ▼         ▼
 GitHub    GitLab    Helm    Notifiers
```

## 🚀 快速开始

### 前置要求

- Python 3.10+
- Node.js 18+
- npm 或 yarn

### 1. 克隆项目

```bash
git clone <repository-url>
cd ReleaseTracker
```

### 2. 启动后端

```bash
cd backend

# 安装依赖
pip install -e .

# 复制配置文件
cp config.example.yaml config.yaml
# 编辑 config.yaml 添加你要追踪的仓库

# 启动服务
uvicorn releasetracker.main:app --reload --host 0.0.0.0 --port 8000
```

### 3. 启动前端

```bash
cd frontend

# 安装依赖
npm install

# 启动开发服务器
npm run dev
```

访问 http://localhost:5173 即可使用！

## 📝 配置示例

```yaml
# config.yaml
storage:
  type: sqlite
  path: ./data/releases.db

trackers:
  # GitHub 仓库
  - name: kubernetes
    type: github
    repo: kubernetes/kubernetes
    interval: 1h
    filter:
      include_prerelease: false
      pattern: "^v1\\."

  # GitLab 仓库
  - name: gitlab-runner
    type: gitlab
    instance: https://gitlab.com
    project: gitlab-org/gitlab-runner
    interval: 2h

  # Helm Chart
  - name: nginx-ingress
    type: helm
    repo: https://kubernetes.github.io/ingress-nginx
    chart: ingress-nginx
    interval: 4h

notifiers:
  - name: webhook
    type: webhook
    url: https://example.com/webhook
    events: [new_release]
```

## 📚 API 文档

启动后端后访问：

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

### 主要端点

| 方法 | 路径 | 描述 |
|------|------|------|
| GET | `/api/stats` | 获取统计信息 |
| GET | `/api/trackers` | 获取所有追踪器 |
| POST | `/api/trackers/{name}/check` | 手动触发检查 |
| GET | `/api/releases` | 获取版本列表 |
| GET | `/api/releases/latest` | 获取最新版本 |

## 🎨 界面预览

- **仪表盘**：统计卡片 + 最新版本列表
- **追踪器管理**：查看所有追踪器状态、手动触发检查
- **版本历史**：完整的版本更新记录

## 🔧 开发

### 后端测试

```bash
cd backend
pytest tests/ -v
```

### 前端构建

```bash
cd frontend
npm run build
```

## 📦 部署

### Docker 部署（推荐）

```bash
# 构建镜像
docker-compose build

# 启动服务
docker-compose up -d
```

### 生产部署

后端：
```bash
uvicorn releasetracker.main:app --host 0.0.0.0 --port 8000 --workers 4
```

前端：
```bash
npm run build
# 将 dist/ 目录部署到 Nginx 或其他静态服务器
```

## 🗺️ 路线图

- [ ] Docker 镜像和 docker-compose
- [ ] 更多通知渠道（邮件、钉钉、飞书、Slack）
- [ ] 版本更新对比功能
- [ ] Prometheus metrics
- [ ] 更多版本源（npm、PyPI、Docker Hub）

## 📄 许可证

MIT License

## 🙏 致谢

- [FastAPI](https://fastapi.tiangolo.com/)
- [Vue 3](https://vuejs.org/)
- [Tailwind CSS](https://tailwindcss.com/)
