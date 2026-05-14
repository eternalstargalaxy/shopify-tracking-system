# 日志、告警与运维汇总

## 目标

在不引入完整 APM 平台的前提下，先把这套物流查询系统补到“最小可上线监控”。

当前方案覆盖：

- 结构化请求日志
- 关键业务事件落库
- Webhook 告警
- Feishu 签名告警
- 内部运维汇总接口

## 一、结构化日志

每个请求都会通过中间件输出一条结构化 JSON 日志，包含：

- `request_id`
- `method`
- `path`
- `query`
- `client_ip`
- `status_code`
- `duration_ms`

如果请求抛出未捕获异常，还会额外记录：

- `error_type`
- `error`

## 二、关键业务事件

除了请求级日志，系统还会记录关键业务事件，例如：

- `tracking_query_success`
- `tracking_query_error`
- `tracking_no_updates`
- `tracking_not_store_order`
- `order_lookup_not_found`
- `ip_rate_limited`
- `ip_daily_rate_limited`
- `tracking_refresh_limited`
- `proxy_signature_missing`
- `proxy_signature_invalid`
- `proxy_shop_not_allowed`
- `storefront_lookup_failed`
- `storefront_tracking_failed`
- `seventeen_track_http_error`
- `seventeen_track_network_error`

这些事件会同时：

1. 输出到 stdout 结构化日志
2. 写入本地 `system_events` 表

## 三、system_events 表

表结构：

- `event`
- `level`
- `message`
- `context_json`
- `created_at`

作用：

- 保留关键事件，不依赖容器 stdout 历史
- 支持内部巡检接口
- 便于后续接入监控平台

## 四、Webhook 告警

当前会主动发告警的情况包括：

- 请求返回 `5xx`
- 未捕获异常
- 17TRACK 公共 API HTTP 错误
- 17TRACK 公共 API 网络错误
- 17TRACK Shopify 店铺摘要接口失败
- 17TRACK Shopify 店铺详情接口失败
- 订单号查询失败

告警默认支持通用 webhook，也支持 Feishu 机器人。

### Feishu 配置

```env
ALERT_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/...
ALERT_WEBHOOK_SECRET=your-feishu-secret
ALERT_MIN_INTERVAL_SECONDS=900
```

如果是飞书地址，系统会自动：

- 识别为 Feishu webhook
- 生成 `timestamp`
- 生成 `sign`
- 按 Feishu 文本消息格式发送

### 告警节流

相同 `event` 在一个节流窗口内只发一次，默认：

```env
ALERT_MIN_INTERVAL_SECONDS=900
```

也就是 15 分钟内同类错误不会无限刷屏。

## 五、内部运维汇总接口

接口：

```text
GET /internal/api/ops/summary
```

需要内部令牌：

```text
x-internal-token: <INTERNAL_DASHBOARD_TOKEN>
```

返回内容包括：

- 最近 24 小时事件总数
- 最近 24 小时 error 数
- 最近 24 小时 warning 数
- 最近若干条关键事件明细

适用场景：

- 每日人工巡检
- 快速判断近期是否有上游失败激增
- 配合内部查询台做简单运维面板

## 六、当前边界

这套方案已经比“只看控制台输出”稳很多，但它仍然是最小可上线监控，不是完整 APM：

- 还没有图形化 dashboard
- 还没有外部探活 / SLA 报表
- 还没有按错误类型自动聚类的告警看板

## 七、推荐上线动作

1. 在 `.env` 中配置 Feishu webhook 与 secret
2. 确认 `INTERNAL_DASHBOARD_TOKEN` 已配置
3. 部署后验证：
   - `/health`
   - `/internal/api/ops/summary`
4. 人工触发一次测试告警，确认飞书群能收到消息
