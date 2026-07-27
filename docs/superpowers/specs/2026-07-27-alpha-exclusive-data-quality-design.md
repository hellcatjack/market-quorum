# Alpha Vantage 独占路由与评估质量修复设计

## 目标

在不修改 `TradingAgents/` 的前提下修复三类已由 JPM 历史盲测暴露的问题：Alpha Vantage 技术指标响应被误当作 CSV、成功任务的研究阶段长期停留在 `running`、以及 Alpha Vantage 可用时仍可能访问 Yahoo 或在限流后回退到 Yahoo。

## 已确认根因

### Alpha Vantage 指标异常

TradingAgents 的 Alpha Vantage 请求函数会识别 `Information` 和 `Note` 中的常见限流提示，但其他 JSON 错误响应会作为普通字符串返回。指标函数随后把该 JSON 字符串当作 CSV，因而得到只有 `{` 的表头并返回 `time column not found`。此外，指标函数的宽泛异常处理会把已经类型化的限流异常转换成普通错误文本，使供应商路由无法可靠地区分限流和有效结果。

### 阶段状态未收尾

TradingNG 在接收阶段事件时只创建或更新当前 `RunStep`，没有在进入下一阶段时结束前一阶段，也没有在 `result`、失败或取消终态时统一关闭仍在运行的步骤。因此运行主状态能够成功，步骤表却保留 `running`。

### Yahoo 隐式参与

当前运行快照明确配置 `alpha_vantage,yfinance`，TradingAgents 路由器遇到限流、未配置、无数据或一般异常时会继续链中下一供应商。表现验证也配置相同的双供应商链。与此同时，`get_verified_market_snapshot` 的原始实现直接使用 yfinance，不受研究类别配置控制。32 个独立研究 Worker 没有共享 Alpha Vantage 配额状态，单进程限流无法保护同一个 Premium Key。

## 方案选择

### 未采用：配置切换

仅把供应商链改为 `alpha_vantage` 能阻止 Yahoo 回退，但无法吸收限流，也无法协调多 Worker，请求高峰会从“混用数据”变成“批量失败”。

### 采用：TradingNG 外层共享闸门与同源重试

TradingNG 增加一个平台自有的 Alpha Vantage 协调模块。它不改变 TradingAgents 源码，而是在每个运行子进程中临时包装 Alpha Vantage 的请求边界，并在上下文退出时恢复原函数。所有研究 Worker 和表现验证进程使用同一份基于文件锁的配额状态：每次请求先预约全局时间槽，避免不同进程各自认为仍有额度。

当遇到 HTTP 429、Alpha Vantage 的限流提示或明确的瞬态 JSON 错误时，请求留在 Alpha Vantage 上，按照有上限的指数退避延时后重试。等待会写入共享冷却时间，使其他 Worker 同时避让。超过最大尝试次数后任务明确失败并记录 `vendor_rate_limit`，绝不调用 Yahoo。

独立行情代理服务暂不采用，因为它会新增常驻服务、网络协议、缓存一致性和运维故障面；当前外层协调模块足以覆盖单机部署。

## 配置与不可变快照

新增下列非秘密配置，并将实际值写入每次运行的 `vendor_policies.alpha_vantage`：

- 每分钟最大请求数，继续复用 `TRADINGNG_ALPHA_VANTAGE_REQUESTS_PER_MINUTE`；
- 最大尝试次数；
- 初始退避秒数；
- 最大退避秒数。

TradingNG 分别检测研究 Key 与验证 Key：

- 研究 Key 存在时，四个受支持研究类别固定为 `alpha_vantage`；
- 验证 Key 存在时，表现验证供应商固定为 `alphavantage`；
- Alpha Vantage 不可用时才允许保留显式配置的其他供应商，避免没有 Key 的全新部署无法启动；
- Key 永不进入运行快照、日志、文件名或错误文本。共享闸门只使用不可逆摘要区分不同 Key。

`core_stock_apis`、`technical_indicators`、`fundamental_data` 和 `news_data` 均为 Alpha 独占；FRED 与 Polymarket 保持原路由。所有工具证据因运行快照只含单一供应商而能准确记录 `alpha_vantage`。

## 已验证行情快照

运行上下文将 TradingAgents 的行情快照加载器替换为 TradingNG 外层的 Alpha Vantage OHLCV 加载器。它复用同一请求闸门，从 `TIME_SERIES_DAILY_ADJUSTED` CSV 构造标准 `Date/Open/High/Low/Close/Volume` 数据框，并继续由原有确定性快照逻辑计算指标。

历史研究使用未复权的当日 OHLCV 作为精确行情事实，避免查询日之后发生的现金分配反向改变历史分析日的价格。表现验证仍同时保留原始价格收益与含现金分配的总回报，两种语义不混用。

## 阶段生命周期

步骤状态遵循以下规则：

- 首次收到阶段事件时创建当前 `running` 步骤；
- 进入新阶段前，将同一尝试中此前仍为 `running` 的步骤标记为 `completed` 并设置 `finished_at`；
- 收到终端 `result` 时完成最后阶段；
- 运行失败时将仍运行步骤标记为 `failed`，附带稳定错误码；
- 运行取消时将仍运行步骤标记为 `cancelled`；
- 数据迁移把已有终态运行遗留的步骤按运行终态回填，修复 JPM 等既有详情页。

阶段事件与运行主状态仍是权威时序；步骤表是可读投影，不改变现有状态机迁移规则。

## 错误与可观测性

- 限流重试日志只记录函数名、尝试次数和等待时长，不记录 URL 查询串或 Key；
- 共享闸门文件使用原子替换和进程级独占锁，损坏或空文件按无历史状态恢复；
- 429 的 `Retry-After` 可用时优先采用，并受最大退避约束；
- 非限流的认证错误立即失败，不以延时掩盖错误 Key；
- 持续无数据或无效标的仍按现有业务错误处理，不回退到 Yahoo；
- 价格目标保持可选。缺少可靠估值依据时继续明确记录 `not_set`，不从止损价或文本数字推断并伪造目标价。

## 测试与验收

- 失败测试证明 JSON 错误响应不再形成 `time column not found` 成功文本；
- 失败测试证明限流后等待并再次调用 Alpha，且 Yahoo 实现调用次数为零；
- 多实例文件闸门测试证明请求槽跨对象共享并按配置速率平滑；
- 研究 Key 存在时调度快照四类供应商均严格等于 `alpha_vantage`；
- 验证 Key 存在时路由器只含 `alphavantage`，429 后同源重试；
- Alpha 行情快照加载器生成正确列且不调用 yfinance；
- 阶段跃迁、成功、失败和取消测试验证步骤终态与结束时间；
- 迁移后既有 JPM 成功运行的五个步骤均为 `completed`；
- `TradingAgents/` 没有文件变化；
- 完整单元、集成、静态检查、产物哈希与服务健康检查通过；
- 使用一个低成本真实 Alpha 请求验证 MACD CSV 表头，并检查新运行元数据不含 Yahoo。
