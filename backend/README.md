# 后端服务说明

## 作用

后端负责承接 Shopify App Proxy 请求，并完成以下工作：

- 校验 Shopify App Proxy 签名
- 校验物流单号格式
- 做 IP 限流和单号刷新限流
- 查询本地缓存
- 处理 17TRACK 注册和查询
- 标准化物流状态和轨迹
- 返回前端统一数据结构

## 当前技术选型

- 框架：FastAPI
- 本地存储：SQLite
- 上游接口：17TRACK V2

当前本地使用 SQLite 只是为了快速落地和调试，后续可以平滑换成 PostgreSQL。

## 接口

- `GET /health`
- `GET /api/track?nums=YT2610601001467359`

## 环境变量

根目录下创建 `.env`，常用字段如下：

```env
APP_NAME=parcelpilot-tracking
DATABASE_PATH=backend/data/tracking.sqlite3
SHOPIFY_APP_SECRET=replace_me
ALLOWED_SHOP_DOMAINS=your-store.myshopify.com
SEVENTEEN_TRACK_API_KEY=replace_me
REQUIRE_ORDER_TRACKING_MATCH=false
BYPASS_PROXY_SIGNATURE=true
MOCK_WHEN_API_KEY_MISSING=true
```

## 开发说明

- `BYPASS_PROXY_SIGNATURE=true`
  仅用于本地开发，表示跳过 Shopify App Proxy 签名校验。

- `MOCK_WHEN_API_KEY_MISSING=true`
  在没有真实 17TRACK Key 时返回模拟物流数据，方便前端联调。

- `REQUIRE_ORDER_TRACKING_MATCH=true`
  开启本店订单物流单号校验。开启后，只有存在于 `order_tracking_numbers` 表中的单号才允许进入 17TRACK 注册和查询流程。

- `backend/dev_server.py`
  本地启动入口。如果环境里已经安装 `uvicorn`，可以直接运行它。
