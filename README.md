# ReleaseTracker

🚀 一款轻量级、可配置的版本追踪软件，支持追踪 GitHub、GitLab、Helm Chart 等仓库的 release 版本。

![Python](https://img.shields.io/badge/Python-3.10+-blue)
![Vue](https://img.shields.io/badge/Vue-3.x-green)
![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-teal)
![License](https://img.shields.io/badge/License-GPL%20v3-blue)

## ✨ 特性

- 📦 **多源支持**：GitHub、GitLab（含自托管）、Helm Chart
- 🔐 **安全认证**：
    - JWT 用户认证（登录/注册/修改密码）
    - 🔒 **凭证加密**：Token 等敏感信息使用 AES/Fernet 透明加密存储
- 🌍 **国际化**：完整支持中英文切换
- 🎨 **现代化 UI**：
    - Vue 3 + TailwindCSS
    - 🌓 **个性化主题**：支持深色模式、多种主题色配置
    - 📱 **响应式设计**：完美适配移动端
- ⚙️  **灵活配置**：Web UI 可视化管理，支持正则过滤规则
- 🔔 **通知推送**：Webhook 通知（支持扩展更多渠道）
- 🎯 **定时追踪**：自动定期检查版本更新
- 💾 **本地存储**：SQLite 数据库，轻量无依赖

## 🏗️ 架构

```
┌─────────────┐
│  Vue 3 前端  │
│  (端口 5173) │
└──────┬──────┘
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

# 设置加密密钥（可选，生产环境推荐设置）
# 生成密钥: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
export ENCRYPTION_KEY="your-generated-key"
export JWT_SECRET="your-generated-key"

# 启动服务
uvicorn releasetracker.main:app --reload --host 0.0.0.0 --port 8000
```

> ⚠️ **注意**：首次启动会自动创建默认管理员账户：
> - 用户名：`admin`
> - 密码：`admin`
> 
> 请登录后立即修改密码！

### 3. 启动前端

```bash
cd frontend

# 安装依赖
npm install

# 启动开发服务器
npm run dev
```

访问 http://localhost:5173 即可使用！

## 📝 配置说明

| 环境变量 | 描述 | 默认值 |
|----------|------|--------|
| `ENCRYPTION_KEY` | 用于加密敏感凭证的密钥 (AES) | 自动生成的开发密钥 |
| `JWT_SECRET` | 用于签名认证令牌的密钥 (JWT) | 自动生成的开发密钥 |
| `TZ` | 系统时区设置 | `UTC` |

## 🔐 安全说明

### 凭证加密
所有敏感凭证（如 GitHub Token、GitLab Token）在写入数据库前都会使用 Fernet 算法进行加密。
- 密钥通过环境变量 `ENCRYPTION_KEY` 配置。
- 如果未配置密钥，将使用默认开发密钥（并在日志中输出警告）。

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

## 📦 部署

### 生产部署

后端：
```bash
export ENCRYPTION_KEY="<production-key>"
uvicorn releasetracker.main:app --host 0.0.0.0 --port 8000 --workers 4
```

前端：
```bash
npm run build
# 将 dist/ 目录部署到 Nginx 或其他静态服务器
```

## 🗺️ 路线图

- [ ] 更多版本源（npm、PyPI、Docker Hub）
- [ ] 版本更新管理功能

## 📄 许可证

GPL-3.0 License

## 🙏 致谢

- [FastAPI](https://fastapi.tiangolo.com/)
- [Vue 3](https://vuejs.org/)
- [Tailwind CSS](https://tailwindcss.com/)
- [shadcn/ui](https://ui.shadcn.com/)
