# 从 GitHub 获取并运行

本文说明如何在一台新电脑上从 GitHub 拉取项目，并启动采购文件智能审查系统。

## 一、运行前准备

需要准备以下软件和服务：

- Git
- Python 3.12 或更高版本
- [uv](https://docs.astral.sh/uv/)
- Node.js 20 或更高版本，以及 npm
- PostgreSQL 14 或更高版本
- Redis 6 或更高版本
- 一个可访问的 MinerU 服务，默认地址为 `http://127.0.0.1:8001`
- 一个 OpenAI 兼容的大模型接口及 API Key

PostgreSQL 和 Redis 可以安装在本机，也可以使用 Docker 或公司内部服务。MinerU 和大模型接口必须能被后端访问。

## 二、拉取项目

```bash
git clone <GitHub仓库地址>
cd agent-review-system
```

不要把 `.env`、`backend/review_config.json` 或生产数据提交到 GitHub。这些文件需要在新电脑上根据示例文件重新创建。

## 三、配置后端

进入后端目录并安装依赖：

```bash
cd backend
uv sync
```

复制环境变量示例文件：

```bash
cp .env.example .env
```

Windows PowerShell 使用：

```powershell
Copy-Item .env.example .env
```

编辑 `backend/.env`，至少确认以下配置：

```env
APP_ENV=development
STORAGE_BACKEND=postgres
DATABASE_URL=postgresql://用户名:密码@127.0.0.1:5432/xiamen_tobacco
REDIS_URL=redis://127.0.0.1:6379/0
MINERU_API_URL=http://127.0.0.1:8001
JWT_SECRET=请替换成随机的长字符串
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

`JWT_SECRET` 不要使用示例值。生产环境应使用随机生成的长字符串，且不要提交到 GitHub。

## 四、配置审查模型

在 `backend` 目录下复制模型配置：

```bash
cp review_config.example.json review_config.json
```

Windows PowerShell：

```powershell
Copy-Item review_config.example.json review_config.json
```

编辑 `backend/review_config.json` 中的 `llm` 配置：

```json
{
  "llm": {
    "api_url": "https://你的OpenAI兼容接口/v1",
    "api_key": "你的API Key",
    "model": "你的模型名"
  }
}
```

`api_key` 只保存在本机配置中，不要提交到 GitHub。若使用 OCR 服务，也需要填写 `ocr` 部分。

## 五、初始化数据库和账号

先创建 PostgreSQL 数据库，例如 `xiamen_tobacco`，然后在 `backend` 目录执行：

```bash
uv run alembic upgrade head
uv run python -m scripts.init_auth
```

初始化脚本会在 PostgreSQL 中创建角色和演示账号。初始账号为：

| 用户名 | 初始密码 | 角色 |
|---|---|---|
| `operator` | `ChangeMe123!` | 业务经办 |
| `supervisor` | `ChangeMe123!` | 专业监督 |
| `admin` | `ChangeMe123!` | 系统管理员 |

首次登录后应立即修改或替换这些初始账号和密码。不要在公开文档中继续使用生产密码。

本仓库不包含原有业务数据。新环境需要通过系统页面重新上传知识库和采购文件。

## 六、启动后端

在 `backend` 目录执行：

```bash
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

验证后端：

```text
http://127.0.0.1:8000/health
```

看到 `{"status":"ok"}` 即表示 API 已启动。若提示 LLM 配置缺失，请检查 `review_config.json`。

## 七、启动 Celery worker

Celery worker 用于执行耗时的审查任务。Redis、PostgreSQL 和 MinerU 服务都准备好后，在第二个终端执行：

```bash
cd backend
uv run celery -A app.workers.celery_app:celery_app worker -Q review --loglevel=info
```

也可以使用 Docker：

```bash
cd backend
docker compose -f docker-compose.worker.yml up -d --build
```

查看 worker 日志：

```bash
docker logs -f xiamen-celery-worker
```

Docker 配置默认通过 `host.docker.internal` 访问宿主机上的 PostgreSQL、Redis 和 MinerU。若这些服务不在宿主机上，需要修改 `docker-compose.worker.yml` 中的地址。

## 八、启动前端

打开第三个终端：

```bash
cd frontend
npm ci
```

复制前端配置：

```bash
cp .env.example .env
npm run dev
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
npm run dev
```

浏览器打开：

```text
http://localhost:5173
```

前端默认把 `/api` 请求代理到 `http://127.0.0.1:8000`。如果后端运行在其他地址，修改 `frontend/.env` 中的 `VITE_API_PROXY_TARGET`。

## 九、启动顺序

推荐顺序如下：

1. PostgreSQL
2. Redis
3. MinerU 服务
4. 初始化数据库和账号
5. 后端 API
6. Celery worker
7. 前端

## 十、常见问题

### 后端启动时报 LLM 配置缺失

检查 `backend/review_config.json` 是否存在，并确认 `llm.api_url`、`llm.api_key`、`llm.model` 都已填写。

### 登录失败或数据库连接失败

检查 `DATABASE_URL`、数据库名称、用户名和密码，并确认已经执行：

```bash
uv run alembic upgrade head
uv run python -m scripts.init_auth
```

### 页面能打开，但提交审查后一直等待

通常是 Celery worker、Redis 或 MinerU 未启动。依次检查 worker 日志、Redis 连接和 `MINERU_API_URL`。

### 前端提示无法连接服务

确认后端运行在 `8000` 端口，并检查 `frontend/.env` 中的 `VITE_API_PROXY_TARGET`。

### 上传或运行时报目录不存在

后端会按配置创建运行目录。检查 `DATA_DIR`、`UPLOADS_DIR` 和 `REVIEW_RUNS_ROOT` 是否有写权限。

## 十一、开发验证

后端测试：

```bash
cd backend
uv run --no-sync pytest -q
```

前端构建：

```bash
cd frontend
npm run build
```
