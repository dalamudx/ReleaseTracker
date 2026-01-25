# ReleaseTracker 后端

## 安装依赖

```bash
cd backend
pip install -e .
```

## 配置

复制示例配置文件并修改：

```bash
cp config.example.yaml config.yaml
# 编辑 config.yaml 添加你要追踪的仓库
```

## 运行

```bash
# 开发模式
uvicorn releasetracker.main:app --reload --host 0.0.0.0 --port 8000

# 生产模式
uvicorn releasetracker.main:app --host 0.0.0.0 --port 8000
```

## API 文档

启动后访问：
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## API 端点

| 方法 | 路径 | 描述 |
|------|------|------|
| GET | `/api/stats` | 获取统计信息 |
| GET | `/api/trackers` | 获取所有追踪器 |
| GET | `/api/trackers/{name}` | 获取单个追踪器 |
| POST | `/api/trackers/{name}/check` | 手动触发检查 |
| GET | `/api/releases` | 获取版本列表 |
| GET | `/api/releases/latest` | 获取最新版本 |

## 开发

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 代码格式化
black src/

# 代码检查
ruff check src/

# 运行测试
pytest tests/
```
