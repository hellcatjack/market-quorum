# MarketQuorum

[English](README.md)

MarketQuorum 是一套可追溯的多智能体投资研究与评估平台。它将本机已登录的
Codex CLI 接入 TradingAgents，以容量感知的方式调度研究任务，并在一个支持
多人使用的系统中保存投资结论、证据、报告、模型设置、评审以及后续表现验证。

> MarketQuorum 与 TradingAgents 均为研究软件，其输出不构成金融、投资、交易、
> 法律或税务建议；系统不会自动下单。

## 主要能力

- 由本机 Codex CLI 驱动、兼容 OpenAI Chat Completions 的 Gateway。
- 支持股票、ETF 和加密资产的 Web 派发与状态跟踪。
- 可信标的类型解析以及按资产类型调整的分析师组合。
- 1–32 个评估任务的队列化并发控制，并禁止同一标的同时运行。
- CPU、内存、磁盘、Gateway 容量、数据供应商和熔断器准入保护。
- 不可变运行历史、完整报告、证据、产物、LLM 交互元数据、评论、评审和表现验证。
- 可选的历史辅助评估，只引用分析日前已经完成表现验证的同标的旧记录。
- 版本化 REST API、Streamable HTTP/stdio MCP、API 凭据、SSE 事件及签名 Webhook。
- 面向内部多用户部署的 OIDC/PKCE 登录与角色/权限控制。

## 系统结构

```text
浏览器 / REST 客户端 / MCP 客户端
                 |
        Caddy + OAuth2 Proxy
                 |
   FastAPI 管理平台 + MySQL
        |          |            |
     调度器      Worker 池      表现验证
        |          |
        |       隔离 Runner
        |          |
        +---- Codex Gateway ---- 本机 Codex app-server
                         |
                  固定版本 TradingAgents
```

Gateway 只监听本机回环地址，并在每次请求前读取 Codex 当前生效的模型和思考深度。
平台会把这两个值与 TradingAgents 版本、Prompt Schema、数据源及工具源一起固化，
使后续验证者能够还原结论产生时的执行条件。

## 仓库结构

| 路径 | 职责 |
|---|---|
| `gateway/` | 最小 OpenAI 兼容 Codex Gateway 与可选审计代理 |
| `platform/` | FastAPI、调度器、Worker、MCP、持久化与表现验证 |
| `web/` | React 管理端 |
| `TradingAgents/` | 固定版本的上游研究引擎 Git 依赖 |
| `deploy/` | Docker Compose、Keycloak、OAuth2 Proxy 与 Caddy 部署示例 |
| `systemd/user/` | 用户级服务和 32 实例 Worker target 示例 |
| `scripts/` | 安装、验证、备份、恢复、迁移与诊断脚本 |
| `integration_tests/` | 跨组件与真实验收测试 |

## 环境要求

- Linux
- Python 3.10+
- Node.js 22+ 与 npm
- Codex CLI 0.145.0+，且本机 ChatGPT 已登录
- 支持 submodule 的 Git
- 用于身份服务和开发数据库的 Docker/Compose
- 当前生产平台配置使用 MySQL 8

安装前确认 Codex 可用：

```bash
codex --version
codex login status
```

## 克隆与安装

```bash
git clone --recurse-submodules git@github.com:hellcatjack/market-quorum.git
cd market-quorum
./scripts/bootstrap.sh
npm --prefix web ci
```

如果克隆时没有初始化依赖：

```bash
git submodule update --init --recursive
```

实际使用的 `.env`、`.env.platform`、`var/` 和 `reports/` 均被 Git 忽略。
不得把真实环境文件、评估报告、数据库或 Gateway 审计文件放入提交。

## 启动 Codex Gateway

开发环境以前台方式运行：

```bash
./scripts/run_gateway.sh
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8000/v1/models
```

对外模型别名固定为 `codex`。每次请求前，Gateway 都会读取 Codex 当前生效的模型
和思考深度并固定到本次执行。它使用
`mcp_servers.playwright.enabled=false` 启动私有 app-server，防止评估请求不断累积
Playwright 子进程。这个覆盖不会修改用户的 Codex 配置，也不影响普通 Codex 会话。

Codex 以只读文件系统沙箱和 `networkAccess=true` 执行研究。网络内容是不可信输入；
只读权限并不意味着原本可读取的文件会自动保密，因此凭据必须放在 Codex 无法读取的
位置。

## 连接 TradingAgents

把本地 Gateway 示例复制为被忽略的实际环境文件：

```bash
cp .env.tradingagents.example .env
```

必要配置如下：

```dotenv
TRADINGAGENTS_LLM_PROVIDER=openai_compatible
TRADINGAGENTS_DEEP_THINK_LLM=codex
TRADINGAGENTS_QUICK_THINK_LLM=codex
TRADINGAGENTS_LLM_BACKEND_URL=http://127.0.0.1:8000/v1
OPENAI_COMPATIBLE_API_KEY=local
```

`OPENAI_COMPATIBLE_API_KEY=local` 只是客户端库需要的本地占位值，不是 OpenAI 或
Codex 凭据。启动 CLI：

```bash
.venv/bin/tradingagents
```

## 本地运行管理平台

启动可丢弃的 PostgreSQL 开发服务并应用迁移：

```bash
docker compose -f deploy/compose.dev.yml up -d postgres
docker compose -f deploy/compose.dev.yml exec postgres \
  createdb -U tradingng tradingng_test
export TRADINGNG_DATABASE_URL=postgresql+psycopg://tradingng:tradingng@127.0.0.1:5432/tradingng
.venv/bin/alembic -c platform/alembic.ini upgrade head
```

使用 `PYTHONPATH=platform/src:TradingAgents` 在不同终端运行：

```bash
.venv/bin/tradingng-platform-api
.venv/bin/tradingng-platform-scheduler
TRADINGNG_WORKER_INSTANCE=1 .venv/bin/tradingng-platform-worker
.venv/bin/tradingng-platform-validation
```

启动 Web 管理端：

```bash
.venv/bin/python scripts/export_openapi.py
npm --prefix web run api:generate
npm --prefix web run dev
```

API 默认监听 `127.0.0.1:8010`。存活与就绪检查分别位于 `/health/live` 和
`/health/ready`；需要身份认证的业务接口使用 `/api/v1`。

## 调度与并发

新安装环境默认同时运行两个评估。管理员可以在“系统状态”页面把最大并发设置为
1–32。预先启动的 32 个轻量 Worker 能执行任何允许值；空闲 Worker 不会发起
Codex 请求。

已保存的并发值并不是唯一准入条件。当 Gateway 活动请求达到阈值、CPU 持续过高、
内存或磁盘不足、数据源熔断，或相同标的正在运行时，新任务会暂停准入。准入保护不会
取消已经运行的任务。

## 独立评估与历史辅助

新任务默认使用 `independent`，不会让旧结论影响本次研究。派发页、REST 和 MCP
也可以显式选择 `memory_mode=historical`。调度器会在任务准入时为同一标的选择
最多 5 次旧评估；每次旧评估只采用当时已经成熟的最高验证周期，并要求验证截止日
严格早于本次分析日期，防止未来数据泄漏。

选中的来源、验证 ID、收益、Alpha 和内容哈希会固化到不可变运行快照，并写入该任务
独享的 TradingAgents 记忆文件。不同并发任务不会共享记忆文件。评估详情默认折叠
展示历史来源，并可跳转到原始评估记录；已有任务和不含记忆字段的旧快照继续按独立
模式运行。该能力完全位于平台外层，不修改 TradingAgents 子模块。

## REST、MCP、事件与 Webhook

REST 与 Web 共用同一套应用服务和不可变记录。MCP 在 `/mcp` 提供无状态
Streamable HTTP，本地认证客户端也可以使用 stdio：

```bash
export TRADINGNG_MCP_TOKEN='short-lived-oidc-service-token'
.venv/bin/python scripts/inspect_mcp.py \
  --url http://127.0.0.1:8010/mcp \
  --token-env TRADINGNG_MCP_TOKEN
TRADINGNG_MCP_TOKEN="$TRADINGNG_MCP_TOKEN" \
  .venv/bin/tradingng-platform-mcp-stdio
```

MCP 的派发和控制工具立即返回，客户端通过状态工具、资源、REST 或 SSE 跟踪排队
任务。Webhook 密钥加密保存，目标地址具有 DNS 重绑定/SSRF 防护，投递重试不会改变
评估状态。

## 部署示例

`deploy/` 和 `systemd/user/` 描述原始部署，因此包含示例公开域名及检出路径
`/app/devs/TradingNG`。这些文件不含实际凭据。在其他环境使用前，必须替换域名、
路径、证书假设以及所有空值/示例密钥。

启用服务前先构建和验证：

```bash
npm --prefix web run build
PYTHONPATH=platform/src .venv/bin/alembic -c platform/alembic.ini upgrade head
./scripts/verify_platform.sh
```

参考用户服务生命周期：

```bash
systemctl --user disable --now tradingng-platform-caddy.service
systemctl --user link "$PWD"/systemd/user/tradingng-platform-*.service
systemctl --user link "$PWD"/systemd/user/tradingng-platform-workers.target
systemctl --user daemon-reload
systemctl --user enable --now tradingng-platform-containers.service
systemctl --user enable --now tradingng-platform-api.service
systemctl --user enable --now tradingng-platform-scheduler.service
systemctl --user enable --now tradingng-platform-workers.target
systemctl --user enable --now tradingng-platform-validation.service
```

Gateway 仍是独立的本机回环服务，不会被公开 Caddy 路由。备份和恢复必须显式执行：

```bash
./scripts/backup_platform.sh
./scripts/backup_platform.sh --verify-only
./scripts/restore_platform.sh \
  --archive "$PWD/var/backups/tradingng-YYYYMMDDTHHMMSSZ.tar.zst" \
  --confirm-restore RESTORE
```

## 验证

离线测试使用假的 Codex 响应和合成数据，不会消耗 Codex 配额：

```bash
PYTHONPATH=platform/src:gateway/src:TradingAgents .venv/bin/pytest \
  TradingAgents/tests/test_platform_events.py \
  gateway/tests platform/tests/unit integration_tests -q
.venv/bin/ruff check gateway/src gateway/tests platform/src platform/tests scripts
.venv/bin/ruff format --check gateway/src gateway/tests platform/src platform/tests scripts
npm --prefix web run lint
npm --prefix web run typecheck
npm --prefix web run test -- --run
npm --prefix web run build
systemd-analyze --user verify systemd/user/*.service systemd/user/*.target
```

使用 `./scripts/verify_platform.sh` 执行完整数据库和部署检查。真实 Codex 检查会消耗
账户配额，必须显式运行：

```bash
.venv/bin/python scripts/smoke_gateway.py
```

## 诊断审计

可选的本机回环审计代理只记录显式选择任务的完整请求和响应。输出必须保存在被忽略的
`reports/` 下。这些文件可能包含专有 Prompt、工具参数、市场数据和用户信息，绝不能
公开。

## 安全与贡献

私密漏洞报告方式见 [SECURITY.md](SECURITY.md)，测试及隐私要求见
[CONTRIBUTING.md](CONTRIBUTING.md)。公开示例只能包含占位符，禁止提交认证文件、
Token、Cookie、私钥、数据库、备份或评估产物。

## 许可证

MarketQuorum 使用 [MIT 许可证](LICENSE) 开源。第三方组件继续遵循各自的许可证，
详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 上游致谢

MarketQuorum 基于
[TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)。
固定依赖保留上游 Apache License 2.0 和作者归属，详见
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
