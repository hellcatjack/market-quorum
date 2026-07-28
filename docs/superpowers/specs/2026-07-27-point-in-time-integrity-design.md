# 点时数据完整性与污染隔离设计

## 目标

在 Market Quorum 外部平台层建立统一、可版本化、失败关闭的点时数据策略，确保历史评估只能使用在分析日期当时已经公开的数据；对既有评估执行可重复的完整性审计，并阻止风险结果进入默认准确率统计和未来历史记忆。

本设计是报告准确性优化计划的第一阶段。它不修改 `TradingAgents/`，不删除或改写既有决策、证据、报告和验证。

## 范围

包含：

- 历史财务报表的真实可用日过滤。
- 当前快照、新闻、社交、宏观和行情工具的统一策略登记。
- 每次运行的完整性声明、审计原因和永久产物。
- 既有运行的离线重审与风险隔离。
- 默认统计和历史记忆的完整性门禁。
- 风险评估的干净重跑关联。
- REST、MCP 和 Web 的只读呈现。

不包含：

- forecast.v1、60/120 日验证或预测置信度。
- 历史辅助 A/B 实验和组合回放。
- 修改 TradingAgents 提示词或数据流实现。
- 将 SEC 财务数值本身提供给模型；第一阶段只使用 SEC 元数据判断 Alpha Vantage 报表何时可用。

## 完整性策略

策略标识首版为 `point-in-time.v1`。每个工具方法声明一个策略：

| 工具类别 | 历史策略 | 安全条件 |
|---|---|---|
| 日线、技术指标 | 截止日期裁剪 | 返回会话不晚于分析日 |
| 新闻 | 查询窗口与结果日期裁剪 | 发布时间不晚于分析日 |
| FRED | vintage 查询 | `realtime_start`、`realtime_end` 固定为分析日 |
| 当前 fundamentals / insider / prediction markets | 禁用 | 历史运行不返回当前快照 |
| Stocktwits / Reddit 当前抓取 | 禁用 | 历史运行不返回当前内容 |
| 资产负债表、现金流、利润表 | 公开日过滤 | `available_at <= analysis_date` |

任何未登记工具在历史运行中默认为 `unknown`，不得被静默视为安全。

## 财务报表可用日解析

### 数据流

平台通过运行时路由包装器接管 `get_balance_sheet`、`get_cashflow`、`get_income_statement` 的返回值，不修改 vendored 函数。

1. 解析 Alpha Vantage 报表中的 `fiscalDateEnding` 和频率。
2. 使用标的身份解析 CIK；只接受确定性唯一映射。
3. 查询并缓存 SEC submissions 元数据，选择匹配期间与表单类型的 10-Q、10-K、20-F 或 40-F 申报。
4. 将 SEC `filingDate` 作为首选 `available_at`。
5. 无 SEC 映射时，可尝试 Alpha Vantage earnings `reportedDate`；只有期间唯一匹配、日期格式有效且不早于 fiscal end 时才接受，并标记较低 assurance。
6. 只把 `available_at <= analysis_date` 的报表记录重新序列化并传给 TradingAgents。
7. 无可靠可用日的记录丢弃，并把原因写入审计产物。

过滤器不得把用于判定的未来 SEC 或 earnings 内容提供给模型；模型只能看到通过过滤的原始财务报表字段。

### 失败语义

- 元数据供应商临时错误：按独立限流和退避策略重试；耗尽后该财务工具返回点时不可用提示。
- 标的无法映射、期间歧义或缺少公开日：丢弃对应记录，不用 `fiscalDateEnding` 猜测。
- 所有记录被过滤：返回结构合法的空报表和机器可读提示，允许 TradingAgents 在缺少该证据的情况下继续。
- 解析格式异常：不传原始响应，标记 `unknown_schema`。

当前评估日期不启用历史过滤，但仍记录策略为 `current_snapshot`；它不能被未来的历史准确率回测误标为点时安全。

## 运行时并发与缓存

- 可用日查询使用平台级异步/跨进程协调，不绕过现有 Alpha Vantage 全局配额代理。
- SEC 请求使用明确 User-Agent、独立速率限制和短期缓存。
- 缓存键包含供应商、标的身份、响应版本或 ETag；缓存内容不包含密钥。
- 同一 run、ticker、statement period 的解析结果只计算一次，三个报表工具复用。
- 元数据服务故障不能令整个评估失败；结果降级为财务证据不可用并留下完整事件。

## 数据模型

新增 `run_integrity_assessments`：

- `run_id`。
- `policy_version`。
- `status`：`safe`、`at_risk`、`unknown`。
- `audit_mode`：`live` 或 `retrospective`，表示判定是在运行结束时生成，还是对封存证据离线重审。
- `temporal_scope`：`contemporaneous` 或 `historical_reconstruction`，表示评估是在分析日当日运行，还是事后重建历史日期。
- `checked_at`。
- `analysis_date`。
- `reason_codes_json`。
- `tool_findings_json`：每个工具的状态、记录数和 assurance，不保存敏感原文。
- `artifact_id`：指向永久完整性审计 JSON。
- `input_fingerprint`：运行证据、配置与策略的组合哈希。

唯一键为 `(run_id, policy_version, input_fingerprint)`。同一策略和相同输入的重审幂等；证据或策略变化会新增记录，不覆盖旧审计。读取时选择最高受支持策略下最新完成的记录。

新增 `assessment_runs.clean_reassessment_of_run_id` 可空外键，区别于失败重试所用 `retry_of_run_id`。干净重评估创建新的 request/run、配置快照、报告和验证，不继承受污染的 memory snapshot。

完整性产物包含：

- 策略版本和代码提交。
- 分析日期与审计时间。
- 工具调用哈希和点时判定。
- 每个财务期间的 fiscal end、available at、来源、assurance 和保留/丢弃结果。
- 汇总状态与原因码。

## 状态判定

### safe

- 所有实际用于报告的历史工具均有已登记策略。
- 财务记录均有可靠可用日且不晚于分析日。
- 没有当前快照或分析日之后的数据进入模型。
- 必要审计产物完整且哈希可验证。

### at_risk

- 证据显示当前快照、分析日之后的记录或不可接受的财务记录曾进入模型。
- 历史工具输出绕过点时保护。
- 永久证据足以确认污染风险。

### unknown

- 旧证据不足以证明安全或风险。
- 工具/供应商模式未登记。
- 输出缺失、无法解析或缺少可用日映射。

`unknown` 不是 `safe`。默认统计、记忆和正式实验同时排除 `at_risk` 与 `unknown`。

## 既有评估重审

离线审计器只读取数据库和封存 evidence/artifact：

1. 校验运行配置、证据产物和内容哈希。
2. 根据工具名、参数、输出摘要和供应商重放 `point-in-time.v1`。
3. 对旧财务输出提取所有 fiscal period，并通过相同可用日解析器核验。
4. 保存新的 retrospective 完整性记录和审计产物。
5. 输出 safe、at_risk、unknown 数量及逐原因统计。

已知六条可疑运行作为回归样例，而不是硬编码名单。重审可能识别更多风险或未知运行。

审计批次可暂停、继续和幂等重跑。不得更新 `AssessmentRun.status`、`Decision`、`Validation` 或旧产物。

## 默认消费门禁

### 准确率与台账

- 聚合查询默认只使用当前策略判定为 `safe` 的 run。
- API 同时返回 `eligible_count`、`excluded_at_risk_count`、`excluded_unknown_count`。
- 管理员可请求 `integrity=all` 查看全量，但页面必须保持风险标记，不能把全量百分比冒充可信准确率。
- 单个 run 的旧验证仍然可见并可回放。

### 历史记忆

`HistoricalMemoryRepository` 选择候选来源时额外要求：

- 来源 run 在当前策略下为 `safe`。
- 验证已经成熟并完成。
- `exit_session < 新评估 analysis_date`。

若没有合格来源，仍保持用户请求的 historical 模式，但 snapshot entries 为空，并记录 `no_integrity_eligible_memory`。不得自动放宽门禁。

## 干净重评估

- 管理员可对 `at_risk` 或 `unknown` 运行发起 clean reassessment。
- 新运行复制标的、分析日期、分析师、深度、语言和当前模型路由；数据源使用当前安全策略。
- memory 默认 independent，防止污染链继续传播。
- 新运行通过 `clean_reassessment_of_run_id` 关联原运行。
- 原运行页面显示替代运行状态；新运行页面显示其修复来源。
- 批量重跑服从现有调度容量和 Alpha Vantage 全局配额，不绕过在途任务。

## REST、MCP 与 Web

新增只读资源：

- `GET /api/v1/assessments/{run_id}/integrity`。
- `GET /api/v1/integrity/summary`。
- MCP assessment resource 追加完整性摘要，另提供按 run 读取完整性详情的资源。

新增受 `assessments:write` 和审计权限保护的 clean reassessment 操作。所有变更写入 AuditEvent。

Web：

- 详情页在最终判断附近显示完整性徽标、策略版本和原因。
- at-risk 使用危险提示，unknown 使用警告提示，safe 使用低干扰确认提示。
- 台账准确率显示合格样本与排除数量。
- 管理员可展开工具级判定及永久审计产物，但默认折叠。

中英文文案同步加入现有国际化词典。

## 迁移与发布

1. 增加模型、迁移、合同与只读接口，尚不改变消费者过滤。
2. 发布运行时历史工具保护和完整性产物生成。
3. 对新测试评估验证 safe 判定，再启用新 run 的同步审计。
4. 运行旧数据离线重审并核对计数。
5. 启用默认统计和 memory 门禁。
6. 在容量允许时批量创建风险评估的 clean reassessment。

门禁启用前必须确认新代码能够区分“尚未审计”和“审计为 unknown”，避免把迁移期间未处理行误判为安全。

## 测试策略

### 单元测试

- fiscal end 在分析日前但 filing date 在分析日后的记录必须丢弃。
- filing date 等于分析日时允许，之后一天不允许。
- SEC 唯一映射、歧义、缺失和 Alpha 次级映射。
- 三种 statement 类型和季度/年度表单。
- 非法 JSON、未知 schema、所有记录被过滤和供应商超时。
- context manager 退出后所有 vendored routes 恢复。
- 当前日期运行不套用历史结果，但标记 current snapshot。
- 状态聚合与输入指纹幂等。

### 集成测试

- 历史 runner 实际观察到的财务工具输出只含已公开期间。
- `TradingAgents/` 文件和子模块指针不变化。
- safe 来源可进入 memory；at-risk、unknown 均被排除。
- 聚合 API 默认计数与管理员全量计数不同且原因可解释。
- clean reassessment 创建独立 request/run，原记录不变。
- REST、MCP 权限与审计事件完整。

### 回归与生产验证

- 用已知六条可疑运行验证审计器至少将其识别为 at-risk 或 unknown，不得标为 safe。
- 抽样人工核对 SEC filing date、Alpha fiscal period 和分析日期。
- 部署前后比较旧 Decision、Validation 和 Artifact 的行数、哈希与内容不变。
- 观察 Alpha Vantage broker、SEC 限流、运行队列和 memory 空来源比例。

## 成功标准

- 所有历史财务报表以真实公开可用日过滤，不能只依赖 `fiscalDateEnding`。
- 无法证明安全的数据失败关闭，评估仍可在缺少该证据时完成。
- 每个新历史评估都有可验证的 `point-in-time.v1` 完整性产物。
- 旧运行全部得到 safe、at-risk 或 unknown 的可解释判定。
- 默认统计和历史记忆不消费 at-risk/unknown 数据，单条记录仍可审计。
- clean reassessment 不覆盖原记录，也不继承受污染 memory。
- API、MCP 和 Web 清楚显示合格与排除样本。
- `git diff -- TradingAgents` 为空，TradingAgents 子模块指针不变。
