# 快速开始（本地运行）

这份文档提供比根目录 `README.md` 更完整的本地运行步骤与常见配置说明。

---

## 前置要求

- Docker
- Docker Compose plugin
- `curl`
- 至少 1 个可用的 LLM API Key（OpenAI / DeepSeek / Claude 等）

可选（仅在“手动开发模式”下需要）：

- Python 3.11+
- Node.js 18+（推荐配合 `pnpm`）

> 仅本地自用：通常只需要把「模型 + 搜索」相关的 key 配好即可；鉴权/多用户隔离/限流属于可选加固项（不影响开发体验）。

> 推荐优先使用根目录 `./start_weaver.sh`。它会自动探测端口冲突、生成本地配置文件，并在 Docker 内启动前端、后端、PostgreSQL 和 Redis。

---

## 1) 克隆仓库

```bash
git clone https://github.com/LittleSongxx/Weaver_pro.git
cd Weaver_pro
```

---

## 2) 一键启动（推荐）

```bash
./start_weaver.sh
```

首次执行时，如果本地文件不存在，脚本会自动生成：

- `.env`
- `web/.env.local`
- `config/config.toml`

如果这些文件刚被创建，请先在根目录 `.env` 中补充 API Key，然后再次执行：

```bash
./start_weaver.sh
```

`.env` 最小可用配置示例：

```bash
# 任选其一：OpenAI / DeepSeek（OpenAI 兼容）/ Anthropic
OPENAI_API_KEY=sk-...

# 若使用 DeepSeek，建议同时填写
OPENAI_BASE_URL=https://api.deepseek.com

# 搜索服务（推荐）
BOCHA_API_KEY=
SEARCH_ENGINES=bocha,duckduckgo
```

常见可选项：

```bash
# 代码执行
E2B_API_KEY=e2b_...

# Qwen / 语音
DASHSCOPE_API_KEY=sk-...
```

> 端口说明：脚本默认尝试前端 `3100`、后端 `8001`、PostgreSQL `5432`、Redis `6379`；如端口冲突，会自动顺延并写入 `.run/compose.env`。

启动成功后会输出实际访问地址，通常包括：

- 前端界面：`http://127.0.0.1:3100`
- 后端 API：`http://127.0.0.1:8001`
- OpenAPI 文档：`http://127.0.0.1:8001/docs`
- Metrics：`http://127.0.0.1:8001/metrics`

---

## 3) 手动开发模式（可选）

### 后端（根目录 `.env`）

```bash
cp .env.example .env
cp config/config.example.toml config/config.toml
```

> 端口说明：后端默认监听 `8001`。如端口冲突，可在根目录 `.env` 中设置 `PORT=18080` / `PORT=28001` 之类的值（选一个空闲端口即可）。

**最小可用配置**（在 `.env` 里填写）：

```bash
# 任选其一：OpenAI / DeepSeek（OpenAI 兼容）/ Anthropic
OPENAI_API_KEY=sk-...
# 或（DeepSeek 兼容 OpenAI 协议）
# OPENAI_API_KEY=sk-...
# OPENAI_BASE_URL=https://api.deepseek.com
# 或（Claude）
# ANTHROPIC_API_KEY=sk-ant-...

# 搜索服务（Deep Research / Web 模式会用到）
BOCHA_API_KEY=sk-...
SEARCH_ENGINES=bocha,duckduckgo
```

**可选但推荐**：

```bash
# 代码执行（推荐）
E2B_API_KEY=e2b_...

# MCP 工具桥（更多示例见 docs/mcp.md）
ENABLE_MCP=true
MCP_SERVERS={"filesystem":{"type":"stdio","command":"npx","args":["-y","@modelcontextprotocol/server-filesystem","/ABS/PATH/TO/ALLOW"]},"memory":{"type":"stdio","command":"npx","args":["-y","@modelcontextprotocol/server-memory"]}}
```

> 安全提示：`server-filesystem` 一定要显式传入“允许访问的目录”，不要直接给根目录。

### 前端（`web/.env.local`）

```bash
cp web/.env.local.example web/.env.local
```

常用配置项：

```bash
# 后端 API 地址（浏览器可访问的地址；需要和后端 `PORT` 对齐）
NEXT_PUBLIC_API_URL=http://127.0.0.1:8001

# Chat / Research 流式协议（默认 sse；遇到代理/平台不兼容可切换 legacy）
NEXT_PUBLIC_CHAT_STREAM_PROTOCOL=sse
NEXT_PUBLIC_RESEARCH_STREAM_PROTOCOL=sse
```

更多流式协议说明见 `docs/chat-streaming.md`。

---

## 4) 安装依赖（手动开发模式）

### 后端

```bash
# 创建 .venv 并安装核心 + 开发依赖
make setup

#（可选）安装“重依赖”工具（桌面自动化 / Office 文档 / 爬虫等）
make setup-full
```

### 前端

```bash
pnpm -C web install --frozen-lockfile
```

### Playwright（可选）

如果需要浏览器自动化，安装 Chromium：

```bash
playwright install chromium
```

---

## 5) 启动服务（手动开发模式）

```bash
# 终端 0：启动 PostgreSQL / Redis
docker compose -f docker/docker-compose.yml up -d postgres redis

# 终端 1：启动后端
.venv/bin/python main.py

# 终端 2：启动前端（默认端口 3100）
pnpm -C web dev
```

访问入口：

- 前端界面：http://localhost:3100
- 后端 API：http://localhost:8001（默认；以 `.env` 中 `PORT` 为准）
- OpenAPI 文档：http://localhost:8001/docs（默认；以 `.env` 中 `PORT` 为准）
- Metrics：http://localhost:8001/metrics（默认；以 `.env` 中 `PORT` 为准）

---

## 5) 常用开发命令

```bash
# 后端：测试 / Lint / 全量检查
make test
make lint
make check

# OpenAPI 合约对齐（后端 ↔ 前端 types，不允许漂移）
make openapi-types

# 前端：测试 / Lint / 构建
pnpm -C web test
pnpm -C web lint
pnpm -C web build
```

OpenAPI 合约对齐说明见 `docs/openapi-contract.md`。
