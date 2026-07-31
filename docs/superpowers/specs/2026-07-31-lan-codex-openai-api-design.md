# 局域网 Codex OpenAI API 设计

日期：2026-07-31

## 目标

在不改变 TradingAgents 内部调用方式、不向公网暴露 Codex Gateway 本体的前提下，
为物理局域网 `192.168.1.0/24` 提供一个受 Bearer API Key 保护的 OpenAI 兼容入口。
局域网客户端使用现有受信任 HTTPS 域名访问，避免 API Key、提示词和模型输出通过
明文 HTTP 传输。

对外基地址固定为：

```text
https://ushome.amycat.com/openai/v1
```

## 当前边界

- Codex Gateway 继续只监听 `127.0.0.1:8000`；
- TradingAgents 与管理平台继续使用 `http://127.0.0.1:8000/v1`；
- Gateway 当前不负责客户端鉴权，局域网鉴权由 Caddy 边缘完成；
- 物理局域网为 `192.168.1.0/24`；VPN `192.168.201.0/24`、Docker 网段、回环网段和
  其他来源不属于允许范围；
- Caddy 使用 TCP 连接的真实远端地址判定来源，不信任客户端提供的
  `X-Forwarded-For`。

## 方案选择

采用现有 HTTPS Caddy 增加受限路径，而不让 Gateway 直接监听局域网地址。

未采用的方案：

1. Gateway 直接监听 `192.168.1.31:8000`：需要额外处理 TLS 与内部调用鉴权，且扩大
   Gateway 内部接口的攻击面；
2. 新建局域网内部 CA 站点：隔离性良好，但每台客户端都需要安装和维护内部根证书；
3. 完全无鉴权开放：任何局域网设备都能消耗 Codex 账户额度并驱动本机工作区，不满足
   安全要求。

## 路由与数据流

只开放两个 OpenAI 兼容端点：

```text
GET  /openai/v1/models
POST /openai/v1/chat/completions
```

一次合法请求按以下顺序处理：

1. 客户端与 `ushome.amycat.com` 建立受信任的 HTTPS 连接；
2. Caddy 确认 TCP 来源属于 `192.168.1.0/24`；
3. Caddy 对 `Authorization: Bearer <API_KEY>` 做完整值匹配；
4. Caddy 删除进入上游的 `Authorization` 请求头，避免凭据进入 Gateway 日志或运行时；
5. Caddy 去掉 `/openai` 前缀，将请求转发至 `127.0.0.1:8000`；
6. Gateway 按现有 OpenAI 兼容协议返回结果。

`/internal/status`、`/healthz`、OpenAPI 文档以及任何其他 Gateway 路径均不通过该入口
开放。局域网客户端可用经过鉴权的 `/openai/v1/models` 检查可达性和协议兼容性。

## 鉴权与来源限制

- API Key 使用 `openssl rand -hex 32` 生成，即 256 位随机值；
- 客户端使用标准 OpenAI Bearer 头，不新增自定义协议；
- 密钥保存在仓库根目录的 `.env.gateway-lan`，文件权限固定为 `0600`；
- `.env.gateway-lan` 必须被 Git 忽略，仓库只提供不含真实值的配置说明；
- 系统级 `caddy.service` 通过专用 drop-in 只加载 `.env.gateway-lan`，不得为了读取该
  密钥而加载包含其他平台凭据的 `.env.platform`；仓库中的用户级
  `tradingng-platform-caddy.service` 是已禁用的回滚单元，不参与本功能；
- 系统 Caddy 的发行版单元当前使用 `caddy run --environ`，会把环境变量写入 journal；
  专用 drop-in 必须清空并重写 `ExecStart`，移除 `--environ` 后才能注入 API Key；
- Caddy 配置引用 `CODEX_GATEWAY_LAN_API_KEY`，真实值不得写入 Caddyfile、测试快照、
  journal、README 或 Git；
- 密钥文件缺失时 Caddy 服务启动必须失败关闭，不能退化为无鉴权访问；部署顺序必须先
  安装密钥，再加载服务配置；
- 密钥轮换通过原子替换私有文件并重启 Caddy 完成，旧密钥立即失效。

## 响应与失败行为

- 来源不属于 `192.168.1.0/24`：返回 HTTP 403；
- 局域网来源缺少、格式错误或密钥不匹配：返回 HTTP 401；
- 局域网来源访问 `/openai/*` 下未开放的路径：返回 HTTP 404；
- 以上响应使用 OpenAI 风格 JSON `error` 信封，不重定向到 Web 登录页；
- Gateway 不可用时保留反向代理的 502/503 语义，不绕过鉴权提供降级入口；
- 认证头不得出现在访问日志或错误响应中。

## 配置与部署边界

源代码和部署资产需要保持以下一致性：

- `deploy/caddy/tradingng.caddy`：局域网路径、来源限制、Bearer 鉴权和反向代理；
- `deploy/systemd/caddy-lan-openai.conf`：系统级 `caddy.service` drop-in，只加载独立
  密钥文件，移除会转储环境的 `--environ`，并维持系统 Caddy 的开机启动；
- 公共 Caddy 安装脚本：生成或校验私有密钥、安装/回滚 systemd drop-in、验证配置并
  重启系统 Caddy；
- `.gitignore`：忽略 `.env.gateway-lan`；
- 中英文 README：记录基地址、客户端配置、密钥安装和轮换方式；
- 部署配置测试：约束来源网段、精确路径、上游回环地址、认证头删除和密钥隔离；
- Gateway 应用、平台 API、调度器、Worker 与 `TradingAgents/` 不修改。

部署只短暂重启系统 Caddy 以加载新的 systemd 环境。Gateway、平台 API、调度器、验证器
和 Worker 不重启，运行中评估不应被中断。Caddy 重启前后必须记录评估总数、运行数和
队列数，确认业务状态未被修改。

## 测试与验收

按测试驱动方式完成：

1. 部署配置测试先证明当前没有局域网 OpenAI 路由、密钥环境和失败关闭约束；
2. 最小修改使配置测试通过，并运行完整仓库验证；
3. 使用 `caddy validate` 验证带占位密钥的配置可解析；
4. 从本机显式绑定 `192.168.1.31` 发起 HTTPS 请求，正确密钥访问
   `/openai/v1/models` 必须返回 200 和 OpenAI 模型列表；
5. 同一局域网来源使用错误密钥必须返回 401；
6. 通过回环来源模拟非允许网段，正确密钥仍必须返回 403；
7. 正确密钥访问 `/openai/internal/status` 必须返回 404；
8. 使用 OpenAI SDK 和基地址 `https://ushome.amycat.com/openai/v1` 完成一条最小
   chat completion，验证请求与响应兼容；
9. 验收日志只记录状态和请求 ID，不输出 API Key、提示词、模型回答或账户凭据；
10. 验证 Gateway、平台服务、所有 Worker 健康，评估记录和运行状态未变化；
11. 验证 `.env.gateway-lan` 权限为 `0600`、被 Git 忽略且未被提交。

## 运维使用

兼容 OpenAI SDK 的客户端配置为：

```dotenv
OPENAI_BASE_URL=https://ushome.amycat.com/openai/v1
OPENAI_API_KEY=<由管理员安全分发的局域网密钥>
```

密钥只通过现有内部安全渠道分发，不在管理 Web 中展示，也不提供匿名自助获取。发生
疑似泄露时直接轮换密钥；本阶段不实现多密钥、逐客户端撤销、用量配额或 Web 密钥
管理。

## 非目标

- 不允许公网、VPN、Docker 网段或其他子网调用；
- 不提供无鉴权模式；
- 不对局域网入口开放 MCP、平台 API 或 Gateway 内部状态；
- 不增加浏览器 CORS 支持；
- 不增加每用户密钥、计费、速率限制或用量面板；
- 不改变 Codex 模型、思考深度、网络能力、并发策略或账户继承行为；
- 不修改 `TradingAgents/`。
