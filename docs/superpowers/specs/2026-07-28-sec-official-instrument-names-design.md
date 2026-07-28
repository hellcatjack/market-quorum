# SEC 官方标的名称设计

日期：2026-07-28
状态：已确认

## 背景与目标

平台当前通过 EastMoney 搜索和行情接口填充 `Instrument.name`。该名称可能是中文简称、英文简称或供应商自定义展示名，既不完整，也不是美国证券的权威注册名称。PG、PLD 等短 ticker 还会与中文拼音搜索结果冲突，并在备用行情接口不可用时长期保持未解析状态。

本次调整将美国证券的名称语义统一为“SEC EDGAR 当前注册名称”：

- 不再要求或优先获取中文名称；
- 不把 Alpha Vantage、EastMoney 或其他商业数据商的名称标记成官方名称；
- 名称必须保留来源、标识符和核验时间；
- SEC 无法唯一确认时安全降级为 ticker，不猜测、不翻译、不扩写；
- 不修改 `TradingAgents/`，只调整外部平台层。

## 数据源评估与选择

### SEC EDGAR

SEC 的 `company_tickers.json` 提供 ticker、CIK 和注册名称，`data.sec.gov/submissions/CIK##########.json` 提供当前名称、历史名称、ticker 和交易所。它是本设计唯一可以写入官方名称字段的来源。

实测结果包括：

- PG：`PROCTER & GAMBLE Co`；
- PLD：`Prologis, Inc.`；
- NVDA：`NVIDIA CORP`。

SEC 的拼写、大小写和公司类型缩写按原值保存和展示。平台不得把 `Co` 扩写为 `Company`，也不得自行调整为标题大小写，因为这些操作会改变来源原文。

### Alpha Vantage

Alpha Vantage Premium 的 `OVERVIEW` 实测可以返回较完整、易读的英文名称：

- PG：`Procter & Gamble Company`；
- PLD：`Prologis Inc`；
- NVDA：`NVIDIA Corporation`。

它同时提供交易所、国家、行业和公司官网，但 Alpha Vantage 是第三方商业数据商，不是发行人、交易所或监管机构。因此：

- 不作为官方名称主来源；
- 不作为 SEC 失败时的名称回退；
- 不因名称解析额外消耗 Alpha Vantage 配额；
- 仍可在研究数据链路中独立使用，其名称不写入 `Instrument.name`。

### EastMoney

EastMoney 不再参与官方名称解析。现有中文名称只作为旧来源记录保留在解析历史中，不继续作为页面主名称。

## 领域语义与数据模型

继续复用 `instruments.name`，避免破坏既有 API 合同，但将其语义明确改为“已核验官方名称”。`metadata_json.name_resolution` 使用以下结构：

```json
{
  "status": "resolved",
  "provider": "sec_edgar",
  "source_identifier": "CIK0000080424",
  "source_url": "https://data.sec.gov/submissions/CIK0000080424.json",
  "locale": "en-US",
  "verified_at": "2026-07-28T12:00:00Z",
  "next_refresh_at": "2026-08-04T12:00:00Z"
}
```

未解析时记录明确原因，例如：

- `ticker_not_listed`：SEC ticker 索引不存在；
- `ambiguous_cik`：同一 ticker 无法唯一映射到 CIK；
- `exchange_mismatch`：SEC 交易所与平台标的身份冲突；
- `upstream_unavailable`：SEC 请求暂时失败；
- `invalid_payload`：SEC 响应结构不符合合同。

暂时性错误与永久性未找到必须分开。暂时错误按指数退避重试，最近一次已核验名称不得因暂时错误被清空。

旧的 `name_resolution` 在来源替换时追加到 `metadata_json.name_resolution_history`。历史数组只保存必要的来源、名称、标识符和时间，不保存完整上游响应。

## 解析架构

新增独立的 SEC 标的身份提供器，复用现有 SEC User-Agent、超时约束和磁盘缓存基础设施，但不把名称解析耦合到财务报表发布日期逻辑。

数据流如下：

1. 对 ticker 执行现有标准化。
2. 从 SEC ticker 索引查找候选 CIK。
3. 唯一候选直接进入 submissions 核验；多个候选使用平台已有交易所进行精确筛选。
4. 核对 submissions 中 ticker、交易所和当前 `name`。
5. 保存 SEC 原始名称、CIK、来源 URL、核验时间和下次刷新时间。
6. 无法唯一确认时保留 ticker 展示并记录结构化原因。

交易所别名在比较前标准化：`NYQ`/`NYSE`、`NMS`/`NASDAQ`、`ASE`/`AMEX` 视为对应同一市场。不得仅凭相同 ticker 接受交易所冲突的记录。

SEC ticker 索引缓存 24 小时，单个 submissions 文档缓存最多 7 天。新标的创建后立即进入解析队列；已解析标的每 7 天检查一次，以捕获公司更名、ticker 迁移或交易所变化。请求遵守 SEC User-Agent 和速率规范，并对并发做全局限制。

## 现有数据迁移

提供一个幂等回填操作：

1. 仅选择 `name_resolution.provider == "eastmoney"` 的标的；
2. 将旧名称和来源追加到解析历史；
3. 使用 SEC 提供器重新解析并在同一标的记录上更新；
4. SEC 无法确认时将主名称置空，由页面显示 ticker；
5. 人工来源或已经标记为 `sec_edgar` 的记录不被覆盖；
6. 每批独立提交，重复执行不会重复写入历史或破坏已核验结果。

回填是平台运维步骤，不放进 Alembic schema migration，也不在数据库迁移事务中调用外部网络。

## API 与页面展示

既有 `instrument.name`、`instrument_name` 字段保持不变，因此 API、MCP 和外部调用方无需迁移字段。OpenAPI 描述和文档改为“SEC-verified official name when available”。

前端调整包括：

- 中文界面使用“官方名称”，英文界面使用“Official name”；
- 总览台账、运行列表、详情页和标的历史页统一显示 `name`，缺失时显示 ticker；
- ticker 与交易所继续作为第二行身份信息；
- 官方名称不做字符串缩写或省略号裁剪；桌面端适当增加身份列宽，并允许超长名称自然换行；
- 链接的无障碍名称包含完整官方名称、ticker 和交易所；
- 不再以 `locale=zh-CN` 暗示商业供应商英文名称是中文名称。

## 错误处理与可观测性

- SEC 429、5xx、超时和网络错误记录为暂时性上游故障，不记录为 `not_found`；
- 首次解析失败时页面继续显示 ticker，不阻塞评估任务；
- 刷新失败时继续使用最近一次已核验名称，并保留上次成功时间；
- 解析状态暴露成功、待重试、永久未找到和身份冲突计数；
- 日志只记录 ticker、CIK、状态码和原因码，不记录密钥或完整响应；
- 名称来源变化写入审计事件，支持后续追溯。

## 测试与验收

### 单元测试

- PG 解析为 `PROCTER & GAMBLE Co`；
- PLD 解析为 `Prologis, Inc.`；
- NVDA 解析为 `NVIDIA CORP`；
- ticker 拼音冲突不再影响结果；
- 重复 CIK、交易所冲突、无 ticker、无 name 和无效 JSON 均安全失败；
- SEC 429/5xx 与永久未找到产生不同原因和重试时间；
- 最近一次成功名称在刷新失败后保持不变；
- EastMoney 回填幂等且不覆盖人工/SEC 来源。

### 集成与页面测试

- 新建标的后调度器能写入名称及 SEC 来源元数据；
- 总览、运行列表、详情和历史页均展示同一官方名称；
- 超长公司名称完整可见，桌面台账仍保持紧凑；
- 名称缺失时所有页面稳定回退到 ticker；
- API、OpenAPI 和 MCP 继续兼容既有字段。

### 生产核验

- 对当前全部标的执行一次 SEC 回填；
- 抽查 PG、PLD、NVDA、BRK-B 及一个无 SEC 映射标的；
- 对照 SEC submissions 响应检查名称、ticker、交易所和 CIK；
- 确认名称回填不会触发 Alpha Vantage 请求，也不会影响评估队列。

## 发布与回滚

发布顺序为：代码与测试、调度器重启、分批回填、页面核验。发生系统性 SEC 故障时停止回填并保留已成功数据；名称失败不影响评估执行。

回滚时恢复旧解析任务代码，但不自动把已经核验的 SEC 名称替换回第三方简称。若必须恢复旧名称，使用解析历史执行显式、可审计的运维操作。

## 非目标

- 不建立中文名称翻译库；
- 不推断或美化 SEC 注册名称；
- 不修改 TradingAgents；
- 不把名称变更作为历史价格、估值或点时分析数据；
- 不在本次范围内为加密资产、商品或非 SEC 覆盖产品引入新的官方注册机构。
