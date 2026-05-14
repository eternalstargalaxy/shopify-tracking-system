# 当前实现与原方案对齐情况

这份说明用于对照最初开发文档，确认当前代码已经落地的部分、主动调整的部分，以及仍然保留为后续优化项的部分。

## 一、架构是否对齐

原方案：

`Shopify Tracking 页面 -> Shopify App Proxy -> 自建后端 -> 17TRACK API -> 数据库缓存`

当前实现：

`Shopify Tracking 页面 -> Shopify App Proxy -> FastAPI 后端 -> SQLite 缓存/风控 -> 17TRACK 公共 API + 17TRACK Shopify 店铺接口`

结论：

- 主链路与原方案一致
- 后端技术栈选择了文档里允许的 `Python + FastAPI`
- 数据库当前使用 `SQLite`，符合当前低运维成本阶段需求

## 二、Shopify 集成方式

已对齐：

- 使用 App Proxy，而不是前端直接跨域请求外部 API
- 店铺前台通过 `/apps/track/...` 请求后端
- 后端验证 Shopify App Proxy 签名
- 店铺域名白名单已启用

当前前台支持：

- `/pages/track?nums=...`
- `/pages/track?order_no=...&email=...`

说明：

第二种模式是当前实现相对原文档的增强项，用于兼容旧版 17TRACK Shopify 页的“订单号 + 邮箱”查询能力。

## 三、用户查询流程

原方案中主推 `nums` 查询，这一能力已经完整实现。

当前同时支持两条查询路：

### 1. Tracking number 查询

1. 读取 `nums`
2. 走 App Proxy
3. 后端验签
4. 做 IP 限流、单号刷新限流
5. 做本店订单运单校验
6. 查缓存
7. 缓存无效时，优先尝试 17TRACK Shopify 店铺接口补充 carrier / order summary
8. 必要时走 17TRACK 公共 API `/register` + `/gettrackinfo`
9. 标准化状态和轨迹
10. 写入缓存并返回前端

### 2. Order number + Email 查询

1. 读取 `order_no` 和 `email`
2. 走 App Proxy
3. 后端调用 17TRACK Shopify 店铺订单接口
4. 反查得到本店运单号、订单摘要、商品信息
5. 再复用 tracking 查询主流程补齐物流明细

## 四、前端实现

已对齐：

- Shopify Liquid + JavaScript + CSS
- 页面入口 `/pages/track`
- URL 自动查询
- 当前状态展示
- 承运商展示
- 更新时间展示
- 轨迹列表展示
- 异常提示 / 暂无信息提示 / 查询失败提示

增强项：

- Tracking Number / Order Number 双模式切换
- 订单摘要渲染
- 商品图片、规格、价格展示
- 时间线折叠 / 展开
- 品牌化样式适配

## 五、后端实现

已对齐：

- `GET /api/track?nums=...`
- Shopify App Proxy 签名校验
- tracking number 格式校验
- IP 限频
- 同单号限频
- 缓存查询
- 注册逻辑
- 轨迹查询逻辑
- 状态标准化
- 写库并返回统一格式

当前新增：

- `GET /api/order-track?order_no=...&email=...`
- 店铺订单号严格格式校验
- 17TRACK Shopify 店铺摘要/详情接口适配
- 结构化日志与事件落库

## 六、17TRACK 接入逻辑

原文档强调 `/register` 和 `/gettrackinfo` 双动作，这一点已经实现。

当前逻辑进一步增强为：

- 优先复用本地缓存
- 优先复用 17TRACK Shopify 店铺接口拿到的 order summary / carrier 信息
- 如果店铺接口拿不到详细轨迹，再 fallback 到 17TRACK 公共 API
- 对 `unknown/not_found` 结果使用更长缓存 TTL
- 对 `delivered` 结果降低刷新频率

## 七、数据库设计

原文档建议 `tracking_records` 作为核心表，这一点已实现。

当前实际核心表：

- `tracking_records`
- `order_tracking_numbers`
- `rate_limit_hits`
- `system_events`

说明：

`system_events` 是当前实现新增的运维事件表，用于支持内部巡检与告警留痕。

## 八、缓存与成本控制

已对齐：

- 不做主动轮询
- 不做定时自动刷新
- 缓存有效期内不重复打 17TRACK
- 缓存过期后才重新调用

当前缓存策略：

- 活跃单号：`120` 分钟
- 已签收：`72` 小时
- `unknown/not_found`：`6` 小时
- `exception/failed_attempt`：`60` 分钟

## 九、防滥用机制

已对齐并已收紧：

- Shopify App Proxy 签名校验
- tracking number 格式校验
- 订单号格式严格校验
- 本店订单物流单号校验
- 避免重复注册
- 同单号缓存期内不重复调用
- IP 限频
- 单号刷新限频
- 异常请求日志

当前线上默认值：

- IP 每分钟：`5`
- IP 每天：`50`
- 单号 `300` 秒内最多刷新 `3` 次
- `REQUIRE_ORDER_TRACKING_MATCH=true`

说明：

原文档中的 `30/min`、`300/day` 已根据当前上线要求收紧为 `5/min`、`50/day`。

## 十、状态标准化

已对齐：

- 前端只依赖 `normalized_status`
- 后端保留 `provider_status` 与原始描述
- 未覆盖状态归入 `unknown`

当前系统内部状态：

- `info_received`
- `in_transit`
- `out_for_delivery`
- `delivered`
- `exception`
- `failed_attempt`
- `not_found`
- `expired`
- `unknown`

## 十一、日志与告警

原方案提到“异常请求记录日志”，当前已经扩展为最小可上线监控：

- 结构化 JSON 请求日志
- 关键业务事件落库 `system_events`
- Webhook 告警
- Feishu webhook 签名支持
- 内部运维汇总接口：
  - `/internal/api/ops/summary`

## 十二、当前仍保留为后续优化项的内容

- 图形化监控大盘
- 外部探活与 SLO 监控
- 更多物流渠道直连
- 更丰富的自动同步本店订单运单映射方案

## 结论

当前实现与原开发文档的主方案已经基本对齐，核心差异主要是：

1. 数据库当前采用 `SQLite`，不是 `PostgreSQL`
2. 查询模式新增了 `Order Number + Email`
3. 告警与运维能力比原文档更完整
4. 限流默认值根据上线要求进一步收紧

如果只看“是否已经可作为自有 Shopify tracking 系统上线”，答案是：**可以，且已经具备基本风控和最小运维能力。**
