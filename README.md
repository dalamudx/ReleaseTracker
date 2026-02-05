<div align="center">
  <img src="frontend/public/logo.svg" width="120" alt="ReleaseTracker Logo" />
</div>

# ReleaseTracker

🚀 一款轻量级、可配置的版本追踪软件，支持追踪 GitHub、GitLab、Helm Chart 等仓库的 release 版本。

![Python](https://img.shields.io/badge/Python-3.12+-blue)
![React](https://img.shields.io/badge/React-19-61dafb)
![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-teal)
![License](https://img.shields.io/badge/License-GPL%20v3-blue)

## ✨ 特性

- 📦 **多源支持**：GitHub、GitLab（含自托管）、Helm Chart
- 🔐 **安全认证**：
    - JWT 用户认证（登录/注册/修改密码）
    - 🔒 **凭证加密**：Token 等敏感信息使用 **Fernet** (基于 AES-128) 对称加密存储
- 🌍 **国际化**：完整支持中英文切换
- 🎨 **现代化 UI**：
    - React 19 + TypeScript + TailwindCSS
    - 🌓 **个性化主题**：支持深色模式、多种主题色配置
    - 📱 **响应式设计**：完美适配移动端
- ⚙️  **灵活配置**：Web UI 可视化管理，支持正则过滤规则
- 🔔 **通知推送**：Webhook 通知（支持扩展更多渠道）
- 🎯 **定时追踪**：自动定期检查版本更新
- 💾 **本地存储**：SQLite 数据库，轻量无依赖

## 🏗️ 架构

```
┌─────────────────┐
│  React 19 前端   │
│  (端口 5173)    │
└──────┬──────────┘
       │ REST API (JWT Auth)
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

### 开发环境

#### 前置要求

- Python 3.12+
- Node.js 20+
- npm

#### 1. 克隆并安装

```bash
git clone <repository-url>
cd ReleaseTracker

# 安装所有依赖（后端 + 前端）
make install
```

#### 2. 启动开发服务器

```bash
# 同时启动前后端开发服务器
make dev
```

访问 http://localhost:5173 即可使用！

### 生产环境（Docker）

#### 使用 Docker 部署

```bash
# 构建镜像
docker build -t releasetracker:latest .

# 运行容器
docker run -d \
  --name releasetracker \
  -p 8000:8000 \
  -v $(pwd)/data:/app/backend/data \
  -e ENCRYPTION_KEY="your-production-key" \
  -e JWT_SECRET="your-jwt-secret" \
  -e TZ="Asia/Shanghai" \
  ghcr.io/dalamudx/releasetracker:latest
```

#### 使用 Docker Compose（推荐）

创建 `docker-compose.yml`：

```yaml
version: '3.8'

services:
  releasetracker:
    image: ghcr.io/dalamudx/releasetracker:latest
    container_name: releasetracker
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/backend/data
    environment:
      - ENCRYPTION_KEY=your-production-key
      - JWT_SECRET=your-jwt-secret
      - TZ=Asia/Shanghai
    restart: unless-stopped
```

启动：

```bash
docker-compose up -d
```

访问 http://localhost:8000 即可使用！

> [!WARNING]
> 首次启动会自动创建默认管理员账户：
> - 用户名：`admin`
> - 密码：`admin`
> 
> 请登录后立即修改密码！

## 📝 配置说明

| 环境变量 | 描述 | 默认值 |
|----------|------|--------|
| `ENCRYPTION_KEY` | 用于加密敏感凭证的密钥 (AES) | 自动生成的开发密钥 |
| `JWT_SECRET` | 用于签名认证令牌的密钥 (JWT) | 自动生成的开发密钥 |
| `TZ` | 系统时区设置 | `UTC` |

### 生成密钥

```bash
# 生成 AES 加密密钥 (Fernet)
# 方式 1: 使用 Python (推荐)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# 方式 2: 使用 OpenSSL
openssl rand -base64 32 | tr '+/' '-_'

# 生成 JWT 密钥（任意强随机字符串）
openssl rand -hex 32
```

## 🔐 安全说明

### 凭证加密
所有敏感凭证（如 GitHub Token、GitLab Token）在写入数据库前都会使用 Fernet 算法进行加密。
- 密钥通过环境变量 `ENCRYPTION_KEY` 配置。
- 如果未配置密钥，将使用默认开发密钥（并在日志中输出警告）。

## 🛠️ 开发命令

| 命令 | 说明 |
|------|------|
| `make install` | 安装所有依赖（后端 + 前端） |
| `make dev` | 同时启动前后端开发服务器 |
| `make run-backend` | 仅启动后端 |
| `make run-frontend` | 仅启动前端 |
| `make lint` | 代码检查（Python + TypeScript） |
| `make format` | 代码格式化 |
| `make build` | 构建前端生产代码 |
| `make clean` | 清理构建产物 |

## 📚 API 文档

启动后端后访问：

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

### 主要端点

| 方法 | 路径 | 描述 |
|------|------|------|
| POST | `/api/auth/login` | 用户登录 |
| GET | `/api/auth/me` | 获取当前用户信息 |
| GET | `/api/stats` | 获取统计信息 |
| GET | `/api/trackers` | 获取所有追踪器 |
| GET | `/api/credentials` | 获取所有凭证 |
| GET | `/api/releases` | 获取版本列表 |

## 🗺️ 路线图

- [ ] 更多版本源（npm、PyPI、Docker Hub）
- [ ] OAuth 认证支持
- [ ] 版本更新管理功能

## 📄 许可证

GPL-3.0 License

## 🙏 致谢

- [FastAPI](https://fastapi.tiangolo.com/)
- [React](https://react.dev/)
- [Tailwind CSS](https://tailwindcss.com/)
- [shadcn/ui](https://ui.shadcn.com/)
