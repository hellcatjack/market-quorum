# 多数据源表现验证 v2 设计

## 目标

在不修改 `TradingAgents/` 子项目的前提下，将当前表现验证升级为可自动恢复、交易日精确、供应商中立且可审计的 `validation.v2`。系统继续兼容既有 `validation.v1` 数据，并为未来启用 Alpha Vantage Premium 或其他行情源保留稳定边界。

## 范围与约束

- TradingAgents 只产生评级、目标价、报告和研究产物，不感知验证调度、复权或行情供应商。
- 当前已完成及等待中的验证保持 `validation.v1`，不重算、不改写；升级后新评估使用 `validation.v2`。
- yfinance 仍可作为默认来源；Alpha Vantage 通过可选配置启用，未配置密钥时系统正常运行。
- 一次正式验证只能使用一个主供应商的完整数据，不允许拼接不同供应商的字段。
- 原始供应商语义、平台标准化语义和计算规则分别版本化。
- REST、MCP、Web 和历史辅助记忆保持向后兼容。

## 架构

```text
TradingAgents 决策
      │
      ▼
平台 Decision（不可变）
      │
      ├── MarketCalendarResolver ── 精确 entry/exit/matures_at
      │
      ├── PriceProviderRouter ───── 主源/故障转移/限流
      │          │
      │          ├── YFinanceAdapter
      │          └── AlphaVantageAdapter（可选）
      │
      ├── PriceNormalizer ───────── 统一拆股尺度与现金分配
      │
      └── ValidationCalculatorV2 ── 价格收益、总回报、Alpha、目标价
                       │
                       ▼
          永久产物 + Validation + REST/MCP/Web/Memory
```

`PriceProvider` 只负责把供应商响应解析成语义明确的供应商序列；`PriceNormalizer` 负责转换成平台统一序列；计算器只接受统一序列，不引用 yfinance 或 Alpha Vantage 类型。

## 数据与版本模型

### Validation 扩展

`validations` 增加：

- `calculation_version`：既有记录回填 `validation.v1`，新记录写入 `validation.v2`。
- `calendar_code`：如 `XNYS`、`24/7` 或 `WEEKDAY_FALLBACK`。
- `entry_session`、`exit_session`：调度时冻结的交易日。
- `matures_at`：预计行情可获得的 UTC 时间；`scheduled_for` 对 v2 与其相同。
- `claimed_at`、`lease_expires_at`、`worker_instance`：后台任务租约。
- `price_return`、`benchmark_price_return`、`price_alpha`。
- `total_return`、`benchmark_total_return`、`total_alpha`。
- `normalization_version`、`provider_adapter_version`、`provider_id`。

兼容字段保持：

- `raw_return` 等于 `total_return`。
- `benchmark_return` 等于 `benchmark_total_return`。
- `alpha` 等于 `total_alpha`。

v1 行仍按原逻辑解释，新增字段允许为空。

### 目标价基准

新增 `decision_price_bases`，每个有目标价的 run 最多一条：

- 原始目标价、参考交易日、参考收盘价、币种。
- 供应商、适配器版本、采集时间和状态。
- `target_multiple = price_target / reference_close`。

评估成功后只创建待准备记录；验证工作进程优先异步采集，不阻塞评估成功事务。若基准在验证成熟时仍不可用，收益验证可以完成，但 `price_target_hit` 为 `null` 并记录原因。

## 交易日历与成熟调度

`MarketCalendarResolver` 根据资产类型和交易所选择日历：

- 美国股票、ETF和共同基金使用 `XNYS` 交易会话。
- 加密资产使用 UTC 24/7 日历。
- 无法映射的证券使用工作日回退日历，并显式记录 `WEEKDAY_FALLBACK`。

入场日为大于等于评估日期的第一个交易会话；终点为入场日之后第 `horizon` 个会话。成熟时间为终点收盘时间加供应商缓冲：美股两小时、基金六小时、加密日线十五分钟。交易所日历负责节假日、夏令时和提前收盘。

v2 不再按自然日提前抓取。若供应商仍未发布行情，则进入 `retry_wait`；下一次尝试按供应商退避策略安排。

## 租约与自动恢复

领取验证时在同一事务中写入租约。默认租约五分钟；网络请求和写入完成前可续租。每轮领取前回收已过期的 `running` 行并记录 `validation.recovered` 事件。

- 行情未成熟：次日或下一个供应商窗口重试。
- 网络、限流和供应商临时错误：5、10、20、30分钟退避。
- 数据质量错误：先重试，达到阈值后转为 `unavailable`。
- 未知计算错误：转为 `failed`，保留机器可读错误码。

管理员可通过现有验证写权限调用重试接口重置 `failed`、`unavailable` 或过期 `running` 行；成功结果不可重算覆盖。

## 供应商中立价格合同

供应商序列必须声明：

- `provider_id`、`provider_adapter_version`、供应商代码和请求指纹。
- 交易币种、交易所时区和会话日期。
- OHLCV、供应商复权收盘、拆股系数和现金分配。
- `ohlc_basis`：`as_traded`、`split_normalized` 或 `unknown`。
- 能力集合：拆股、现金分红、资本利得、返还资本。

标准化器输出：

- 统一到终点份额尺度的 `split_normalized_ohlc`。
- 同一尺度的现金分配。
- 从入场点为100的价格指数和总回报指数。
- `normalization_version = prices.v1`。
- 数据质量检查和供应商复权收益对账结果。

供应商 `adjusted_close` 仅用于对账，不是平台唯一真相。供应商行为不明确或公司行动不完整时，标准化失败关闭，不能猜测。

## 供应商实现与路由

### yfinance

- 显式使用 `auto_adjust=False`、`actions=True`、`threads=False`。
- 适配器声明 OHLC 为拆股尺度已统一，并保存 Yahoo 的拆股、分红及资本利得事件。
- 当前默认来源保持不变。

### Alpha Vantage

- 使用 `TIME_SERIES_DAILY_ADJUSTED`。
- 映射原始成交 OHLC、复权收盘、分红和拆股系数。
- 适配器声明 OHLC 为 `as_traded`，由标准化器处理拆股。
- `compact` 覆盖范围不足时使用 `full`，本地按所需日期裁剪和缓存。
- API 密钥只从 `TRADINGNG_ALPHA_VANTAGE_API_KEY` 读取，不进入日志、产物或响应。

### 路由策略

配置提供按资产类型排列的供应商顺序。默认 `yfinance`；配置 Alpha Vantage 后可选 `alphavantage,yfinance`。供应商调用使用独立限流器和现有 vendor 熔断器。故障转移必须在产生正式计算前完成；产物记录最终选中的唯一主源。

第一版不自动执行双源正式对账请求。标准化合同和对账字段预留能力，避免引入不必要的调用成本；后续可为20日或重点标的启用影子对账。

## validation.v2 计算语义

- `price_return`：统一拆股尺度的退出收盘除以入场收盘减一，不含现金分配。
- `total_return`：通过统一价格与现金分配逐日链接得到的总回报。
- `price_alpha`、`total_alpha`：标的对应收益减基准对应收益。
- 方向正确性使用 `total_return`，保持当前投资表现语义。
- MAE/MFE 使用统一拆股尺度的日内低价和高价，不含现金分配。
- 目标价通过冻结的 `target_multiple` 在本次标准化参考价上重建，再与价格路径比较。

供应商复权收盘生成的总回报与平台总回报差异超过容差时，结果仍可保存，但标记 `material_difference` 并在界面显示数据质量告警。

## 产物与数据最小化

永久验证产物包含供应商语义序列、平台标准化序列、查询元数据和质量检查，只保留：

- 目标价参考交易日；
- 入场前最多五个背景会话；
- 入场至验证终点；
- 该区间所需公司行动。

不保存验证终点之后的价格。原始第三方响应是否长期保存由供应商许可决定；默认仅保存字段级标准化输入和不可逆请求指纹。

## REST、MCP、Web 与历史记忆

REST 和 MCP 在现有验证资源中追加 v2 字段，不删除旧字段。新增管理员重试操作通过同一应用服务暴露给 REST 和 MCP。

Web 默认显示：

- 总回报（含现金分配）、价格回报、总回报 Alpha。
- 价格口径 MAE/MFE。
- 数据源、适配器版本、标准化版本、计算版本和质量状态。
- 预计成熟时间而非误导性的自然日计划时间。

历史辅助记忆继续读取 `raw_return` 和 `alpha` 兼容字段，因此 TradingAgents 输入结构无需修改。

## 迁移与发布

1. 数据库迁移新增可空字段并将既有 `calculation_version` 回填为 `validation.v1`。
2. 既有 completed、scheduled、retry_wait 和 running 行全部保持 v1；新评估开始创建 v2。
3. 新代码同时运行 v1 和 v2 计算器。
4. 使用现有33份永久产物回放，确认 v2 总回报与 v1 复权收益在精度范围内一致。
5. 使用固定供应商夹具验证拆股、分红、资本分配、周末、节假日、提前收盘、限流、故障转移和租约回收。
6. 迁移生产数据库，重启 API 与验证服务，确认现有等待任务未改变版本或状态。

## 成功标准

- `git diff -- TradingAgents` 为空，子模块指针不变。
- 每个新成功评估自动产生三个 `validation.v2` 记录和准确成熟时间。
- 验证进程在领取后崩溃可由租约自动恢复。
- 同一标准化夹具经 yfinance 与 Alpha Vantage 适配器得到相同价格/总回报结果。
- 拆股不会造成价格收益跳变；现金分配只进入总回报；目标价不因分红误命中。
- 既有 v1 API、MCP、Web 和历史记忆消费者继续工作。
- 未配置 Alpha Vantage 密钥时不发起任何 Alpha Vantage请求。
- 生产迁移后服务健康、无到期积压、现有验证记录未被重写。
