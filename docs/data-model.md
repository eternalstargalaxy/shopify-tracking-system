# 数据模型说明

## 主表

表名：`tracking_records`

## 字段说明

- `id`
  主键。

- `tracking_number`
  统一转大写后的物流单号。

- `carrier_code`
  上游承运商编码。空字符串表示自动识别。

- `carrier_name`
  上游返回的承运商展示名称。

- `shop_domain`
  发起查询的 Shopify 店铺域名。

- `is_registered`
  是否已经向 17TRACK 注册过该单号。
  可选值：`0 | 1`

- `normalized_status`
  系统内部统一状态枚举。
  可选值：
  `info_received | in_transit | out_for_delivery | delivered | exception | failed_attempt | not_found | expired | unknown`

- `status_text`
  上游返回的最新可读状态文本。

- `provider_status`
  物流服务商原始状态，例如 17TRACK 返回的 `Transit`、`Delivered` 等。

- `provider_status_description`
  物流服务商原始状态描述，用于排查和补充映射表。

- `origin_country`
  起运国家或地区。

- `destination_country`
  目的国家或地区。

- `last_event_time`
  最新轨迹事件时间。

- `last_fetched_at`
  后端最近一次从上游拉取数据的时间。

- `cache_expires_at`
  当前缓存失效时间。未到这个时间时，不重复请求 17TRACK。

- `fetch_count`
  该单号累计刷新次数。

- `events_json`
  标准化后的轨迹事件 JSON。

- `raw_response`
  17TRACK 原始响应快照。

- `last_error_code`
  最近一次错误码。
  可选值：
  `empty_tracking_number | invalid_tracking_number | invalid_shopify_signature | expired_shopify_signature | shop_not_allowed | not_store_order | rate_limited | tracking_refresh_limited | upstream_register_failed | upstream_query_failed | query_error`

- `last_error_message`
  最近一次错误信息。

- `created_at`
  记录创建时间。

- `updated_at`
  记录最后更新时间。

## 限流表

表名：`rate_limit_hits`

## 字段说明

- `bucket_key`
  限流对象，例如 IP 或 `tracking_number:carrier_code`。

- `bucket_type`
  限流桶类型。
  可选值：
  `ip_minute | ip_day | tracking_refresh`

- `window_start`
  当前限流窗口起始 Unix 时间戳。

- `count`
  当前窗口内累计次数。

## 本店订单物流单号表

表名：`order_tracking_numbers`

用于阻止非本店订单物流单号进入 17TRACK 注册流程。

- `shop_domain`
  Shopify 店铺域名。

- `order_name`
  Shopify 订单号或订单名称。

- `tracking_number`
  本店订单对应的物流单号。

- `carrier_code`
  承运商编码。空字符串表示任意承运商。

- `source`
  来源，例如 `csv`、`shopify_webhook`、`manual`。
