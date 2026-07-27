# Alpha Vantage 默认研究数据源与评估清理设计

## 目标

清空当前 TradingNG 平台内全部已有评估及其派生数据，让研究台账重新从零开始；同时在不修改 `TradingAgents/` 的前提下，使所有新准入评估优先使用 Alpha Vantage，并在 Alpha Vantage 未配置、限流或无可用数据时回退到 yfinance。

## 已确认范围

### 清理的数据

- 评估批次、评估请求、评估运行和不可变运行配置快照；
- 运行事件、运行步骤和工作租约；
- 投资结论、证据、复核、评论；
- 表现验证和结论价格基准；
- 与评估事件关联的 Webhook 投递；
- 评估提交、取消、重试和验证调度审计记录；
- `var/artifacts` 下的全部评估产物；
- `var/jobs` 下的全部评估工作目录。

### 保留的数据

- 用户、角色、API 凭证和登录配置；
- 调度并发策略、断路器、Gateway 与数据源健康采样；
- Webhook 定义；
- 标的身份与中文名称缓存；
- 数据库迁移版本和协调锁；
- Gateway、API、Caddy、Keycloak 等系统服务及其配置。

## 数据源设计

TradingAgents 原始默认配置仍然保持不变。TradingNG 在调度器构建不可变运行快照时，用自己的配置覆盖四个受 Alpha Vantage 支持的研究类别：

| 类别 | 新默认链 |
| --- | --- |
| `core_stock_apis` | `alpha_vantage,yfinance` |
| `technical_indicators` | `alpha_vantage,yfinance` |
| `fundamental_data` | `alpha_vantage,yfinance` |
| `news_data` | `alpha_vantage,yfinance` |
| `macro_data` | 保持 `fred` |
| `prediction_markets` | 保持 `polymarket` |

新增 `TRADINGNG_RESEARCH_DATA_VENDOR_CHAIN`，默认值就是 `alpha_vantage,yfinance`。配置采用有序、唯一的逗号分隔列表，只允许 `alpha_vantage` 和 `yfinance`。该变量只决定研究数据源快照，不影响表现验证已有的 `TRADINGNG_VALIDATION_PRICE_PROVIDERS`。

调度器读取配置并把结果固化到每次运行的 `data_vendors` 快照。这样 Web、REST 和 MCP 创建的任务行为完全一致，详情页和产物仍可追溯具体数据源；运行过程中修改环境变量不会改变已准入任务。

## 回退与错误边界

- 已配置 Premium Key 时，Alpha Vantage 是四类研究工具的首选；
- Alpha Vantage 未配置、触发限流或返回无数据时，TradingAgents 现有供应商路由继续尝试 yfinance；
- FRED 和 Polymarket 不进入该链，避免把不支持的类别错误路由到 Alpha Vantage；
- 无效、重复或空的数据源链在服务启动时直接拒绝，防止生成不可信快照；
- 当前评估清理后没有历史记忆来源，新任务即使选择历史辅助也会以零记忆正常运行。

## 安全清理流程

1. 只读统计评估域行数、运行状态、租约、产物和工作目录；
2. 确认没有运行中任务；若存在则先停止调度器、评估工作进程和表现验证进程；
3. 停止上述三个进程，阻断新的准入、写入和验证领取；
4. 在单个数据库事务内按照外键依赖从叶子到根删除评估域数据；
5. 事务成功后删除 `var/artifacts` 和 `var/jobs` 下已解析的直接内容，并重新创建空目录；
6. 重启调度器、评估工作进程和表现验证进程；
7. 验证所有评估域表和文件目录为零，系统配置表保持原有数量。

数据库事务失败时不删除文件，服务保持停止以避免产生数据库与文件不一致；排查后再重新执行。文件删除只针对两个明确的项目子目录，不能使用工作区根目录、环境变量展开或宽泛 glob 作为目标。

## 测试与验收

- 配置默认解析为 `("alpha_vantage", "yfinance")`；
- 显式反转为 `yfinance,alpha_vantage` 时保持调用方顺序；
- 空值、重复值和未知供应商被拒绝；
- 调度器生成的元数据只覆盖四个目标类别，FRED 和 Polymarket 保持不变；
- 新运行快照包含四项 `alpha_vantage,yfinance`；
- `TradingAgents/` 无文件变化；
- 清理后所有评估域表、产物文件和作业目录计数为零；
- 用户、角色、调度策略和标的缓存仍存在；
- Gateway、API、调度器、工作进程与表现验证服务均正常运行。
