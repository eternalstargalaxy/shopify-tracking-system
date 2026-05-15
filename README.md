# LINTICO Tracking System

LINTICO 自建物流查询系统，用来替换现有 17TRACK Shopify 应用嵌入页。

当前线上方案已经落地为：

`Shopify 查询页 -> Shopify App Proxy -> FastAPI 后端 -> SQLite 缓存/风控 -> 17TRACK API`

系统目标是让用户始终在店铺域名内完成物流查询，同时把签名校验、防刷、缓存、状态标准化和异常监控放到自建后端中处理。

## 当前能力

- Shopify 前台查询页：`/pages/track`
- App Proxy 查询接口：
  - `GET /api/track?nums=...`
  - `GET /api/order-track?order_no=...&email=...`
- URL 参数自动查询：
  - `?nums=4PX3002754801725CN`
  - `?order_no=LUK2806&email=doyle.sj@outlook.com`
- 17TRACK 公共 API 查询与注册逻辑
- 17TRACK Shopify 店铺接口摘要查询
- 物流状态标准化
- 订单摘要、商品图片、价格和规格渲染
- SQLite 缓存
- IP 限流、单号刷新限流、本店订单校验
- 结构化日志、Webhook 告警、内部运维摘要接口

## 目录结构

- `backend/app/`
  FastAPI 主逻辑，包含配置、DB、Shopify 验签、17TRACK 适配、风控、状态标准化和监控逻辑。
- `backend/tests/`
  单元测试。
- `backend/tools/`
  辅助导入和维护脚本。
- `shopify/templates/page.track.json`
  Shopify 页面模板入口。
- `shopify/sections/tracking-page.liquid`
  查询页主体。
- `shopify/assets/tracking.js`
  前端查询逻辑、模式切换、自动查询、订单摘要和时间线渲染。
- `shopify/assets/tracking.css`
  LINTICO 查询页样式。
- `docs/`
  项目中文文档。

## 查询方式

### 1. Tracking number

用户访问：

```text
/pages/track?nums=4PX3002754801725CN
```

前端会自动请求：

```text
/apps/track/api/track?nums=4PX3002754801725CN
```

### 2. Order number + Email

用户访问：

```text
/pages/track?order_no=LUK2806&email=doyle.sj@outlook.com
```

前端会自动切到订单号模式，并请求：

```text
/apps/track/api/order-track?order_no=LUK2806&email=doyle.sj@outlook.com
```

## 当前安全与风控

- Shopify App Proxy HMAC 验签
- `ALLOWED_SHOP_DOMAINS` 店铺白名单
- 订单号格式严格校验
- tracking / order 查询模式识别
- 默认只允许本店订单号或本店运单号查询
- IP 限流：
  - 每分钟 `5`
  - 每天 `50`
- 同单号刷新限制：
  - `300` 秒内最多 `3` 次
- 缓存命中时不重复调用 17TRACK

## 当前监控与告警

- 每个请求输出结构化 JSON 日志
- 关键业务事件写入 `system_events`
- `/internal/api/ops/summary` 提供最近 24h 事件汇总
- 5xx、未捕获异常、17TRACK 上游失败会触发 Webhook 告警
- 高频限流、Shopify 验签失败激增、非本店订单查询激增会触发趋势告警
- 支持 Feishu webhook 签名告警

## 本地开发

1. 复制 `.env.example` 为 `.env`
2. 填写 Shopify / 17TRACK / 告警配置
3. 本地调试可临时使用：
   - `BYPASS_PROXY_SIGNATURE=true`
   - `MOCK_WHEN_API_KEY_MISSING=true`
4. 运行后端：

```powershell
py -3.12 -m uvicorn backend.app.main:app --reload
```

## 测试

```powershell
py -3.12 -m unittest backend.tests.test_core
py -3.12 -m compileall backend
```

## 文档索引

- `docs/implementation-plan.md`
  最初实施计划。
- `docs/current-implementation-alignment.md`
  当前实现与原方案对齐情况。
- `docs/order-tracking-validation.md`
  本店订单运单校验方案。
- `docs/internal-ops-console.md`
  内部物流查询台说明。
- `docs/ops-alerting.md`
  日志、事件落库、Webhook 告警与巡检说明。

## 当前状态

已完成：

- Shopify 查询页与 App Proxy 主链路
- 17TRACK 公共 API 查询与注册逻辑
- 17TRACK Shopify 店铺摘要 / 订单模式查询
- 物流状态标准化
- 订单摘要与商品信息渲染
- 本店订单运单校验
- 结构化日志、事件落库、Webhook 告警

持续优化中：

- 与旧版 17TRACK 店铺页的少量交互细节对齐
- 更正式的运维大盘和外部监控接入
- 后续多物流渠道扩展（如 4PX / 云途直连）
