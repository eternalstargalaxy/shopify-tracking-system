# 本店订单物流单号校验

## 目标

新版方案强调：真正需要拦住的是“非本店订单的物流单号”进入 17TRACK 注册流程。

因此后端新增 `order_tracking_numbers` 表，用来保存本店订单对应的物流单号。开启强校验后，只有命中这张表的单号才允许继续走 17TRACK `/register` 和 `/gettrackinfo`。

## 配置项

```env
REQUIRE_ORDER_TRACKING_MATCH=true
```

本地开发可以保持：

```env
REQUIRE_ORDER_TRACKING_MATCH=false
```

## 表结构

表名：`order_tracking_numbers`

- `shop_domain`
  Shopify 店铺域名。

- `order_name`
  Shopify 订单号或订单名称。

- `tracking_number`
  物流单号，统一大写。

- `carrier_code`
  承运商编码。空字符串表示任意承运商。

- `source`
  数据来源，例如 `csv`、`shopify_webhook`、`manual`。

## CSV 导入

CSV 字段建议：

```csv
shop_domain,order_name,tracking_number,carrier_code
demo.myshopify.com,#1001,YT2610601001467359,yunexpress
```

导入命令：

```powershell
py -3.12 backend/tools/import_order_trackings.py .\orders.csv --shop-domain demo.myshopify.com
```

## 后续扩展

上线后更推荐通过 Shopify Webhook 或订单同步任务自动写入 `order_tracking_numbers`，避免人工导入遗漏。
