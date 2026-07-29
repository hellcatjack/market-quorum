# 浏览器退出后直接返回登录页设计

日期：2026-07-29

## 目标

用户点击管理端“退出”后必须离开当前受保护页面，依次终止 OAuth2 Proxy 与
Keycloak 浏览器会话，并直接进入登录流程。退出后不得因为残留的 Keycloak SSO
会话自动回到原页面。

## 根因

当前前端链接仅指向 `/oauth2/sign_out`。OAuth2 Proxy 会清除自己的会话 Cookie，
但配置的 `backend_logout_url` 是服务端请求，不能可靠清除浏览器持有的 Keycloak
SSO Cookie。生产日志显示退出后浏览器重新进入 `/oauth2/start`，Keycloak 随即自动
完成 callback，因此用户看起来仍停留在退出前的位置。

## 方案

采用 OIDC RP-Initiated Logout 的浏览器前端链路：

1. 前端使用普通 `<a>` 执行整页导航，不经过 SPA 路由；
2. 首先访问 OAuth2 Proxy `/oauth2/sign_out`，使其清除本地会话；
3. `rd` 指向同域 Keycloak end-session endpoint，并携带 OAuth2 Proxy 可替换的
   `{id_token}`、`client_id=tradingng-web` 和经过编码的 `post_logout_redirect_uri`；
4. Keycloak 在浏览器上下文中注销 SSO 会话并清除相应 Cookie；
5. Keycloak 返回平台根地址；Caddy 发现本地会话已经清除后立即进入
   `/oauth2/start?rd=/`，没有现存 SSO 会话时显示登录页；
6. 登录成功后进入平台根页面，不恢复退出前的敏感下钻位置。

Keycloak 的 `tradingng-web` 客户端继续只允许受信任的同域 post-logout redirect。
不设置全局 `prompt=login`，避免正常的首次访问或会话刷新都被强制重新输入凭据。

## 组件边界

- `web/src/auth/logout.ts`：集中构造并导出固定的安全退出 URL；不接受用户输入；
- `web/src/app/Layout.tsx`：退出链接只消费该 URL；
- `deploy/keycloak/tradingng-realm.json`：声明精确的平台根地址作为同域
  post-logout redirect；
- Keycloak 公共 URL 对账脚本：确保现有生产 Realm 同样具备该允许项；
- Caddy、平台 API、Gateway、调度器和 `TradingAgents/` 不需要修改。

## 安全与失败处理

- 所有跳转目标均为编译时固定的同域路径，不提供开放重定向输入；
- `id_token` 只由 OAuth2 Proxy 替换，不进入前端状态、持久化存储或源码；退出跳转会
  按 OIDC 规范把它作为 `id_token_hint` 发送给 Keycloak，因此相关入口不得启用未脱敏
  的查询字符串访问日志；
- 即使 Keycloak 会话已经失效，`client_id` 与受信任的 post-logout redirect 仍可把
  浏览器带回登录流程；
- 若 OAuth2 Proxy 无法解析当前会话，其本地 Cookie 仍按现有逻辑清除，最终不得返回
  受保护业务页面；
- 使用整页导航保证浏览器重新经过 Caddy/OAuth2 Proxy 鉴权边界。

## 测试与验收

按测试驱动方式完成：

1. 前端测试先证明当前退出链接缺少 Keycloak 前端退出与登录返回参数；
2. 部署测试先证明 Realm 配置缺少精确的 post-logout redirect 声明；
3. 最小实现使测试转绿，并运行完整 Web、部署配置和 Caddy 校验；
4. 构建并部署静态 Web；如 Realm 存在差异，仅运行幂等 Keycloak 公共 URL 对账；
5. 使用临时测试会话验证完整重定向链：平台 → OAuth2 Proxy sign-out → Keycloak
   logout → OAuth2 start → Keycloak 登录页；不得自动 callback 回平台；
6. 验证运行中的评估、Gateway、调度器和工作进程不被重启或中断。

## 非目标

- 不增加“已退出”中间页面；
- 不改变登录有效期、Cookie 刷新周期或所有登录请求的 prompt 策略；
- 不修改管理员对其他用户执行的强制登出功能；
- 不修改 `TradingAgents/`。
