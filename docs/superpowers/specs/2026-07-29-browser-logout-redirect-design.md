# 浏览器退出后直接返回登录页设计

日期：2026-07-29

## 目标

用户点击管理端“退出”后必须离开当前受保护页面，终止 OAuth2 Proxy 应用会话，
并直接显示 Keycloak 登录表单。退出后不得因为残留的 Keycloak SSO 会话自动回到
原页面，重复点击退出也不得返回 HTTP 400。

## 根因

最初的前端链接仅指向 `/oauth2/sign_out`。OAuth2 Proxy 会清除自己的会话 Cookie，
但配置的 `backend_logout_url` 是服务端请求，不能可靠清除浏览器持有的 Keycloak
SSO Cookie。生产日志显示退出后浏览器重新进入 `/oauth2/start`，Keycloak 随即自动
完成 callback，因此用户看起来仍停留在退出前的位置。

第一版修复把 OAuth2 Proxy 会话中的 `{id_token}` 放入浏览器前端 Keycloak logout
请求。新会话测试能够通过，但真实的长会话中该 ID Token 可能早于应用会话失效；
Keycloak 因此记录 `LOGOUT_ERROR: session_expired` 并返回 HTTP 400。第二次退出仍会
访问同一条不可恢复的前端注销链路，所以问题稳定复现。

## 方案

采用“清除应用会话 + 强制重新认证”的固定浏览器链路：

1. 前端使用普通 `<a>` 执行整页导航，不经过 SPA 路由；
2. 首先访问 OAuth2 Proxy `/oauth2/sign_out`，使其清除本地会话；
3. 固定的 `rd` 直接指向同源 `/oauth2/start?rd=/`，不携带 ID Token，也不访问
   Keycloak end-session endpoint；
4. OAuth2 Proxy 的 OIDC 授权请求统一携带 `prompt=login`；即使浏览器仍有 Keycloak
   SSO Cookie，Keycloak 也显示登录表单而不会自动 callback；
5. 登录成功后进入平台根页面，不恢复退出前的敏感下钻位置。

OAuth2 Proxy 已有的 `backend_logout_url` 保留，用于尽力通知 Keycloak 注销；该请求
是否成功不影响浏览器清除平台会话和显示登录表单。Keycloak 的 `tradingng-web`
客户端继续只允许受信任的同域 post-logout redirect，作为受限的兼容配置。

## 组件边界

- `web/src/auth/logout.ts`：集中构造并导出固定的安全退出 URL；不接受用户输入；
- `web/src/app/Layout.tsx`：退出链接只消费该 URL；
- `deploy/oauth2-proxy.cfg`：为新的 OIDC 授权请求设置 `prompt=login`；
- `deploy/keycloak/tradingng-realm.json`：声明精确的平台根地址作为同域
  post-logout redirect，保留为受限兼容配置；
- Keycloak 公共 URL 对账脚本：确保现有生产 Realm 同样具备该受限允许项；
- Caddy、平台 API、Gateway、调度器和 `TradingAgents/` 不需要修改。

## 安全与失败处理

- 所有跳转目标均为编译时固定的同域路径，不提供开放重定向输入；
- 浏览器退出跳转不携带 `id_token_hint`，不会因长会话中的过期 ID Token 收到
  Keycloak `session_expired` 或 HTTP 400；
- 若 OAuth2 Proxy 无法解析当前会话，其本地 Cookie 仍按现有逻辑清除，最终不得返回
  受保护业务页面；
- `prompt=login` 会使新的认证请求要求用户显式登录；现有 OAuth2 Proxy 会话的普通
  页面访问和静默刷新不经过新的授权请求，不受影响；
- 使用整页导航保证浏览器重新经过 Caddy/OAuth2 Proxy 鉴权边界。

## 测试与验收

按测试驱动方式完成：

1. 前端测试要求退出目标是 `/oauth2/start?rd=/`，并明确禁止 `id_token_hint` 和
   Keycloak end-session 路径；
2. 部署测试要求 OAuth2 Proxy 配置 `prompt=login`；
3. 最小实现使测试转绿，并运行完整 Web、部署配置和 Caddy 校验；
4. 构建并部署静态 Web，只重载 OAuth2 Proxy 配置；
5. 使用保留 Keycloak SSO Cookie 的会话验证：平台 → OAuth2 Proxy sign-out →
   OAuth2 start → Keycloak 登录表单；不得自动 callback 回平台；
6. 在已清除 OAuth2 Proxy Cookie 后再次请求退出，仍必须返回登录流程而非 HTTP 400；
7. 验证运行中的评估、Gateway、调度器和工作进程不被重启或中断。

## 非目标

- 不增加“已退出”中间页面；
- 不保证注销同一 Keycloak Realm 中的其他应用会话；
- 不改变登录有效期或 Cookie 刷新周期；
- 不修改管理员对其他用户执行的强制登出功能；
- 不修改 `TradingAgents/`。
