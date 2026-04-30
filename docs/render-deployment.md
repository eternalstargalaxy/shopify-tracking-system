# Render 后端部署说明

本文档用于把当前 FastAPI 后端部署到 Render，并拿到 Shopify App Proxy 要填写的后端域名。

## 当前已准备好的部署文件

- `requirements.txt`：Render 构建 Python 环境时安装的依赖。
- `render.yaml`：Render Blueprint 配置，包含 Web Service、启动命令、健康检查、环境变量和持久化磁盘。
- 后端启动入口：`backend.app.main:app`
- 健康检查接口：`GET /health`

## Render 服务配置

使用 Blueprint 创建服务时，Render 会读取根目录的 `render.yaml`。

核心配置如下：

- 服务名：`parcelpilot-tracking-api`
- 区域：`singapore`
- 实例类型：`starter`
- 构建命令：`pip install -r requirements.txt`
- 启动命令：`uvicorn backend.app.main:app --host 0.0.0.0 --port $PORT`
- 健康检查：`/health`
- 数据库文件：`/var/data/tracking.sqlite3`
- 持久化磁盘：`/var/data`

如果服务名没有被占用，Render 通常会生成这个后端域名：

```text
https://parcelpilot-tracking-api.onrender.com
```

最终域名以 Render Dashboard 中服务页面显示的 URL 为准。

## 必填环境变量

在 Render 创建 Blueprint 或 Web Service 时，需要填写这些密钥变量：

```env
SHOPIFY_APP_SECRET=你的 Shopify App Secret
ALLOWED_SHOP_DOMAINS=your-store.myshopify.com
SEVENTEEN_TRACK_API_KEY=你的 17TRACK API Key
```

生产环境建议保持：

```env
BYPASS_PROXY_SIGNATURE=false
MOCK_WHEN_API_KEY_MISSING=false
REQUIRE_ORDER_TRACKING_MATCH=true
```

含义：

- `BYPASS_PROXY_SIGNATURE=false`：强制校验 Shopify App Proxy 签名。
- `MOCK_WHEN_API_KEY_MISSING=false`：禁止上线环境返回模拟物流数据。
- `REQUIRE_ORDER_TRACKING_MATCH=true`：只允许查询本店订单中存在的物流单号，降低接口被刷风险。

## 部署步骤

1. 把项目推到 GitHub、GitLab 或 Bitbucket 仓库。
2. 登录 Render，选择 New > Blueprint。
3. 连接这个仓库，并选择根目录的 `render.yaml`。
4. Render 提示填写 `SHOPIFY_APP_SECRET`、`ALLOWED_SHOP_DOMAINS`、`SEVENTEEN_TRACK_API_KEY` 时填入真实值。
5. 点击 Apply，等待首次部署完成。
6. 打开 Render 服务页面，复制服务 URL。
7. 访问 `https://你的后端域名/health`，看到 `{"status":"ok"}` 表示后端启动成功。

## Shopify App Proxy 对接

Render 部署完成后，在 Shopify App 的 App Proxy 中填写：

- Subpath prefix：`apps`
- Subpath：`track`
- Proxy URL：`https://你的后端域名/api/track`

店铺前端请求仍然使用：

```text
/apps/track/api/track?nums=物流单号
```

Shopify 会把请求代理到 Render 后端，并附带签名参数，后端负责验签。

## 上线后检查清单

- `GET /health` 返回 `{"status":"ok"}`。
- Shopify App Proxy 请求没有返回签名错误。
- 真实 17TRACK Key 可以返回物流轨迹。
- 本店订单物流单号已导入 `order_tracking_numbers` 表。
- 非本店物流单号返回 `not_store_order`。

## 后续建议

当前部署使用 SQLite 加 Render 持久化磁盘，适合第一阶段上线和小流量验证。Render 免费 Web Service 不支持持久化磁盘，所以 `render.yaml` 默认使用 `starter` 实例。后续如果查询量上升，建议把缓存和本店单号表迁移到 Render Postgres，再保留同一套接口给 Shopify 前端使用。
