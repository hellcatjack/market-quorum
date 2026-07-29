# 完整用户管理体系设计

日期：2026-07-29  
状态：已实现并通过完整自动化验证，待生产部署验收

## 背景与目标

MarketQuorum 当前使用 Keycloak、OAuth2 Proxy 和 OIDC 处理浏览器登录，平台 MySQL
保存用户业务镜像、角色关联、评估归属和审计记录。现状只在业务操作发生时被动同步
用户，缺少创建、编辑、禁用、密码重置和会话撤销能力；既有 `Viewer` 角色还拥有
`system:read`，不符合新的权限边界。

本次开发建立一套完整但可审计的内部用户管理体系：

- 正式角色只保留 `Admin` 与 `User`；
- 管理员可以管理账号并查看系统状态；
- 一般用户可以执行研究业务，但不能访问系统诊断、系统策略或用户管理；
- Keycloak 继续作为账号、密码、启用状态、角色和登录会话的唯一权威；
- 平台不保存明文密码，也不建立第二套登录体系；
- 不永久删除用户，确保历史评估、评论、复核和审计归属不被破坏。

## 方案选择

采用“平台后端调用 Keycloak Admin REST API”的方案。平台使用独立、最小权限的
Keycloak 服务账号管理 `tradingng` realm 的用户、角色和会话，同时把必要身份镜像
同步到 MySQL。

不采用以下方案：

- 直接写 Keycloak 数据库：会绕过密码策略、缓存、事件和升级兼容机制；
- 平台本地账号：会形成两套身份、密码和会话体系，并破坏当前 OIDC 架构。

## 权威边界与数据模型

### Keycloak

Keycloak 是以下信息的权威来源：

- username；
- display name 与 email；
- enabled 状态；
- `Admin` / `User` realm role；
- 密码、临时密码标记和 required actions；
- 当前登录会话。

新增机密客户端 `tradingng-user-admin`。其 service account 只取得
`realm-management` 中用户管理所需的 `query-users`、`view-users`、`manage-users`
和 `view-realm`；`view-realm` 是读取 `Admin` / `User` 角色表示并执行角色分配所必需。
平台通过 client credentials 获取短期管理令牌，不使用 bootstrap 管理员密码执行
日常操作。

私有环境新增以下配置，示例文件只保存占位值：

```dotenv
TRADINGNG_KEYCLOAK_ADMIN_URL=http://127.0.0.1:18081
TRADINGNG_KEYCLOAK_ADMIN_REALM=tradingng
TRADINGNG_KEYCLOAK_ADMIN_CLIENT_ID=tradingng-user-admin
TRADINGNG_KEYCLOAK_ADMIN_CLIENT_SECRET=replace-with-secret
```

Secret 使用 `SecretStr` 读取，不进入 repr、日志、API 响应、运行快照或审计元数据。

### 平台 MySQL

继续复用 `users`、`roles` 和 `user_roles`：

- `users.subject` 保存 Keycloak user id；
- `users.status` 镜像 `active` / `disabled`；
- `user_roles` 镜像 `Admin` 或 `User`；
- 业务外键继续指向平台用户 UUID；
- 密码和临时密码永不落库。

用户名是创建后不可修改的稳定登录标识。姓名、邮箱、角色和启用状态允许修改。
现有历史用户保持原平台 UUID，避免改变评估和审计归属。

## 角色与权限模型

### User

一般用户拥有研究业务所需权限：

- `assessments:read`；
- `assessments:submit`；
- `assessments:cancel`；
- `assessments:review`；
- `validations:read`；
- `validations:write`；
- `artifacts:read`。

一般用户明确不拥有：

- `system:read`；
- `users:manage`；
- `assessments:admin`；
- 调度策略、模型路由、API 凭据和 Webhook 管理权限。

### Admin

管理员继承全部 `User` 权限，并增加：

- `system:read`；
- `users:manage`；
- `assessments:admin`。

管理接口同时校验 `Admin` realm role 与 `users:manage` scope。只有 scope 或只有角色
都不能通过授权。

### 授权执行

OIDC token 可以携带 Web 客户端的全量候选 scope，但后端按角色白名单再次裁剪。
对人类用户，每个受保护请求还会读取本地用户状态：

- `disabled` 用户即使仍持有短期旧 JWT，也立即被拒绝；
- 降级后的管理员立即失去本地管理权限；
- 升级为管理员后必须重新登录，旧令牌不会被本地镜像直接提权；
- 角色变更、禁用和显式强制登出都会撤销 Keycloak 用户会话。

API credential 继续校验其所属用户的本地状态；禁用用户后，其 API credential 也立即
失效。服务账号保持现有 client-credential 权限模型，不被误识别为人类用户。

## 安全容量摘要与系统状态隔离

当前总览和新建评估会读取 `/system/capacity`，该响应包含 Gateway、模型、熔断器等
诊断信息，不能继续暴露给 `User`。

新增只读的评估准入摘要，例如 `GET /api/v1/assessments/admission-summary`，只返回：

- 当前运行数和允许上限；
- 排队数与最老等待时间；
- 新任务会立即准入还是进入队列；
- 面向普通用户的稳定原因分类，不返回依赖名称和内部资源数值。

总览和新建评估改用该摘要。既有 `/system/status`、`/system/capacity`、调度策略和模型
路由接口继续要求 `system:read`，仅管理员可以调用。

## 用户管理服务

新增独立的 `IdentityAdminService` 与 `KeycloakAdminClient`，避免把 Keycloak 协议细节
放进 API 路由或现有评估服务。

`KeycloakAdminClient` 负责：

- 获取并缓存短期 service-account token；
- 搜索、分页和读取用户；
- 创建用户；
- 更新姓名、邮箱和 enabled；
- 分配唯一的 `Admin` 或 `User` role；
- 设置临时密码与 required action；
- 撤销用户全部会话；
- 将 Keycloak 409、401/403、404、超时和 5xx 转换成稳定领域错误。

`IdentityAdminService` 负责：

- 管理员和 scope 双重授权；
- 输入规范化和业务校验；
- 当前管理员及最后管理员保护；
- Keycloak 变更后的平台镜像同步；
- 审计事件；
- Keycloak 与 MySQL 的差异对账。

用户列表从 Keycloak 读取当前权威状态，并批量同步平台镜像。用户详情可附带当前活动
会话数和最近活动会话时间；不承诺在无活动会话时提供历史登录时间。

## REST API

### 列表和详情

```text
GET /api/v1/admin/users
GET /api/v1/admin/users/{platform_user_id}
```

列表支持 `search`、`role`、`status`、`page` 和 `page_size`。响应包含平台用户 UUID、
Keycloak subject、username、姓名、邮箱、角色、启用状态和最后同步时间。详情额外返回
会话摘要及当前管理员是否允许对目标执行各项操作。

### 创建

```text
POST /api/v1/admin/users
```

请求包含 username、display name、email 和 `Admin|User`。平台生成至少 24 个字符的
高熵临时密码，Keycloak 标记为 temporary 并要求首次登录修改。响应只在本次请求返回
临时密码；之后不能读取。

### 更新

```text
PATCH /api/v1/admin/users/{platform_user_id}
```

允许修改姓名、邮箱、角色和 enabled 状态。角色字段是单选，不允许无角色或同时具有
两种正式角色。用户名不可修改。

### 密码和会话

```text
POST /api/v1/admin/users/{platform_user_id}/reset-password
POST /api/v1/admin/users/{platform_user_id}/logout
```

重置接口生成并只返回一次新的临时密码，要求下次登录修改，并撤销现有会话。登出接口
撤销目标用户全部会话。两者都不返回或记录旧密码。

所有写接口使用标准 request id，返回稳定错误码。用户管理首期不作为 MCP 工具暴露，
避免临时密码进入模型上下文；REST API 是外部自动化的唯一管理入口。

## 管理员防锁死规则

执行角色或状态变更前，服务在平台数据库取得身份管理事务锁，并从 Keycloak 读取最新
启用管理员集合：

- 当前登录管理员不能禁用自己的账号；
- 当前登录管理员不能把自己降级为 `User`；
- 不允许禁用或降级最后一个启用管理员；
- 系统始终至少保留一个启用的 `Admin`；
- 外部 Keycloak 管理操作造成异常状态时，用户管理页显示阻塞告警，只允许先恢复一个
  管理员，不能继续执行危险变更。

不提供永久删除。离职用户使用禁用和强制登出处理。

## Web 管理端

新增 `/users` 页面。管理员侧边栏显示“系统状态”和“用户管理”；一般用户只显示
“总览”和“新建评估”。

页面使用高密度用户台账：

- 行内显示 username、姓名、邮箱、角色、状态和同步时间；
- 支持搜索及角色、状态筛选；
- 创建用户使用独立表单；
- 创建或重置后使用一次性凭据对话框展示临时密码，并提供复制操作；
- 关闭对话框后从 React state 清除临时密码，禁止再次查看；
- 用户详情面板支持编辑资料、角色、启停、重置密码和强制登出；
- 自身账号和最后管理员的危险操作禁用并解释原因；
- 所有界面文本支持 `zh-CN` 与 `en-US`。

前端根据 `/me` 的角色和 scope 隐藏受限导航。一般用户直接访问 `/system` 或 `/users`
时显示无权限页，不发起对应业务查询；后端接口仍独立返回 403。

## 审计与隐私

以下操作写入 `AuditEvent`：

- `user.create`；
- `user.profile_update`；
- `user.role_change`；
- `user.enable` / `user.disable`；
- `user.password_reset`；
- `user.logout`；
- `user.reconcile`。

审计记录操作者、目标平台 UUID、Keycloak subject、变更字段、旧/新角色或状态、请求
ID 和结果。不得记录临时密码、管理 client secret、access token、Authorization header
或完整 Keycloak 错误正文。

日志只记录稳定错误码、目标用户标识和请求 ID。临时密码使用 Pydantic `SecretStr`，
只有创建/重置成功响应的专用序列化路径可以取出其值。

## 一致性与错误处理

Keycloak 是权威系统，平台不尝试跨 Keycloak 与 MySQL 建立伪分布式事务：

1. 校验权限和防锁死规则；
2. 执行 Keycloak 变更；
3. read-after-write 读取权威用户；
4. 在一个 MySQL 事务中 upsert 镜像并写入审计。

若 Keycloak 操作失败，MySQL 不写入成功状态。若 Keycloak 已成功但 MySQL 提交失败，
返回 `identity_sync_pending`，不伪称整个操作失败；下一次列表、详情或登录会自动修复
镜像。创建操作发生这种情况时，临时密码不可恢复，管理员完成对账后执行一次密码
重置。

稳定错误包括：

- `username_conflict` / `email_conflict`；
- `user_not_found`；
- `self_admin_change_forbidden`；
- `last_admin_protected`；
- `identity_provider_unavailable`；
- `identity_provider_forbidden`；
- `identity_sync_pending`。

Keycloak 401/403 视为服务配置故障，不把上游响应正文返回浏览器。超时和 5xx 返回
503，并保留无敏感信息的运维日志。

## 现有用户与 Keycloak 迁移

提供幂等的 Keycloak/平台迁移命令：

1. 创建 `User` realm role 和 `users:manage` client scope；
2. 创建或核验 `tradingng-user-admin` client、service account 和最小管理角色；
3. 将 Web 客户端的候选 scope 更新为包含 `users:manage` 和
   `assessments:review`；
4. 对现有 `Analyst`、`Viewer` 用户分配 `User`，移除旧角色，并撤销会话；
5. 保留现有 `Admin`，核验至少一个启用管理员；
6. 同步全部用户到 MySQL，同时保持平台用户 UUID；
7. 无用户再引用后，移除或禁用旧 `Analyst`、`Viewer` realm role。

迁移重复执行不重复创建用户、不改变现有密码、不重置管理员凭据、不重复审计相同结果。
部署时先迁移 Keycloak，再发布 API 与 Web；如果管理员保护检查失败，停止发布且不修改
现有用户。

## 测试与验收

### 单元测试

- `Admin` 与 `User` scope 矩阵；
- Keycloak token 获取、刷新和错误分类；
- 创建、更新、角色分配、启停、临时密码和会话撤销请求；
- 自我禁用、自我降级和最后管理员保护；
- 临时密码不出现在 repr、日志、审计或数据库数据中；
- Keycloak 成功/MySQL 失败的对账行为；
- 迁移重复执行幂等。

### API 与集成测试

- Admin 可以完整管理用户；
- User 调用所有用户管理和系统诊断接口均为 403；
- User 可以读取、创建和取消评估；
- 禁用用户的现有 OIDC token 和 API credential 都立即被拒绝；
- 角色降级立即收紧权限，升级需要重新登录；
- Keycloak 409、404、401/403、超时和 5xx 映射为稳定错误；
- 审计事件完整且不含凭据；
- MySQL/PostgreSQL 测试适配保持通过。

### Web 测试

- 角色决定导航和路由守卫；
- User 不请求 `/system/*` 或 `/admin/users`；
- 用户列表搜索、筛选、分页和详情；
- 创建、编辑、启停、重置密码和强制登出；
- 一次性密码关闭后不可恢复；
- 防锁死按钮状态及错误提示；
- 中文、英文、桌面和窄屏布局。

### 生产验收

- 对现有 realm 执行幂等迁移并核对用户数量；
- 使用测试 Admin 创建一个 `User` 并完成首次改密；
- 验证 User 可发起评估但看不到系统状态与用户管理；
- 验证直接调用受限 API 返回 403；
- 禁用 User 后确认现有页面/API 会话失效；
- 重置密码和强制登出后确认旧会话无法刷新；
- 核对审计事件无密码、client secret 或 token；
- 删除测试账号采用“禁用并保留审计归属”，不做物理删除。

## 发布与回滚

发布顺序：配置和 Keycloak 管理客户端、幂等迁移、数据库迁移、API、Web、权限与审计
验收。发布前检查活动评估，API 重启必须依赖已经修复的调度器/Worker 联动关系，不中断
运行任务。

回滚应用代码时保留 Keycloak `User` 角色和用户，不回退密码或删除账号。必要时恢复旧
角色映射，但不得重新授予一般用户 `system:read`。用户禁用和审计记录不自动回滚。

## 非目标

- 不提供用户自助注册；
- 不提供永久删除账号；
- 不允许管理员查看现有密码；
- 不建立组织、团队、细粒度资源所有权或按标的隔离；
- 不把用户管理作为 MCP 工具暴露；
- 不修改 TradingAgents。
