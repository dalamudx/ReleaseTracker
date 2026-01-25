.PHONY: help install run-backend run-frontend lint format clean build

# 默认目标
.DEFAULT_GOAL := help

# 变量定义
PYTHON = python3
PIP = pip
NPM = npm

help: ## 显示帮助信息
	@echo "使用方法: make [target]"
	@echo ""
	@echo "目标列表:"
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## 安装所有依赖 (后端和前端)
	@echo "📦 安装后端依赖..."
	cd backend && $(PIP) install -e ".[dev]"
	@echo "📦 安装前端依赖..."
	cd frontend && $(NPM) install

run-backend: ## 运行后端服务
	@echo "🚀 启动后端服务..."
	cd backend && uvicorn releasetracker.main:app --host 0.0.0.0 --port 8000 --reload

run-frontend: ## 运行前端服务
	@echo "🚀 启动前端服务..."
	cd frontend && $(NPM) run dev

dev: ## 同时运行后端和前端 (需要 make -j2)
	@echo "🚀 启动开发环境..."
	@$(MAKE) -j2 run-backend run-frontend

lint: ## 代码检查 (后端 ruff/black, 前端 eslint)
	@echo "🔍 检查后端代码..."
	cd backend && ruff check . && black --check .
	@echo "� 检查前端代码..."
	cd frontend && $(NPM) run lint

format: ## 代码格式化 (后端 black/ruff)
	@echo "✨ 格式化后端代码..."
	cd backend && black . && ruff check . --fix

build: ## 构建前端生产代码
	@echo "🏗️ 构建前端..."
	cd frontend && $(NPM) run build

clean: ## 清理构建产物和缓存
	@echo "🧹 清理垃圾文件..."
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf frontend/dist