# 实施计划

## 项目目标

替换现有 17TRACK Shopify 嵌入页，建设自有物流查询系统，完整链路如下：

`Shopify 查询页 -> Shopify App Proxy -> 自建后端 -> 数据库缓存 -> 17TRACK API -> 标准化数据 -> 店铺前端展示`

## 方案范围

1. 在 Shopify 中提供 `/pages/track` 查询页。
2. 通过 App Proxy 转发查询请求，而不是前端直接跨域请求外部后端。
3. 后端校验 Shopify App Proxy 签名。
4. 后端在调用上游前先校验物流单号格式。
5. 后端校验物流单号是否属于本店订单。
6. 后端缓存物流数据，避免重复注册和重复刷新。
7. 后端统一标准化物流状态，前端只依赖内部状态。
8. 增加 IP 限流和单号刷新限流，控制 API 成本。
9. 17TRACK Key 只保存在后端。

## 评论落实点

1. 数据表字段和状态枚举需要明确说明。
2. 缓存策略先保持简单，活跃运单按小时级别控制。
3. 不主动高频刷新，只有再次查询时才触发刷新。
4. 重点拦截非店铺来源和非本店订单单号导致的无效注册。
5. 前端需要明确不同状态下的展示方法。
6. 维护原始状态到标准状态的映射逻辑。
7. 后端返回中保留 `provider_status` 和原始描述，方便后续排查和补映射。

## 实施顺序

1. 搭后端基础骨架。
2. 实现查询主流程和缓存策略。
3. 增加本店订单物流单号校验。
4. 接 Shopify 主题页和前端脚本。
5. 补文档和测试。
6. 做本地联调和真实环境联调。
   
   
   
   
   
   ## 总体你要做什么
   
   
   
   你的方案文档里也是这个架构：Shopify Tracking 页面 → Shopify App Proxy → 自建后端 → 17TRACK API → 数据库缓存。
   
   ---
   
   # 第 1 步：先不要碰 Shopify，先准备账号和信息
   
   你先确认你手上有没有这几个东西：
   
   ```
   1. Shopify 店铺后台权限2. Shopify Partner / Dev Dashboard 权限3. 17TRACK API Key4. 一个后端部署平台账号，比如 Render / Railway / Vercel / Fly.io5. 一个数据库，推荐 PostgreSQL
   ```
   
   你截图里现在在 Shopify 后台的：
   
   ```
   Settings → Apps
   ```
   
   这个位置是对的，但现在看到的是已经安装的第三方 App。你后面要新建的是自己的 App，不是选 ParcelWILL Returns 或 Judge.me Reviews。
   
   Shopify 官方现在推荐从店铺后台的 **Settings → Apps → Develop apps → Build apps in Dev Dashboard** 进入 Dev Dashboard 创建/安装自定义 App。
   
   ---
   
   # 第 2 步：先创建自己的 Shopify App
   
   你的 App 可以叫：
   
   ```
   LINTICO Tracking App
   ```
   
   操作路径大概是：
   
   ```
   Shopify Admin→ Settings→ Apps→ Develop apps→ Build apps in Dev Dashboard→ Create app
   ```
   
   创建后，先不用写复杂功能。这个 App 目前主要负责两件事：
   
   ```
   1. 提供 SHOPIFY_APP_SECRET 给后端验签2. 配置 App Proxy，把 /apps/track/... 转发到你的后端
   ```
   
   Shopify 官方文档说明，App Proxy 可以把 Shopify 店铺前台 URL 请求代理到外部 App URL，用来在店铺前台显示外部动态数据。
   
   ---
   
   # 第 3 步：先部署一个最简单的后端
   
   这里建议你找开发同事，或者你自己用最简单版本先跑通。
   
   后端先只做一个测试接口：
   
   ```
   GET /api/track
   ```
   
   先不接 17TRACK，先返回假数据：
   
   ```
   {  "success": true,  "tracking_number": "YT2610601001467359",  "normalized_status": "in_transit",  "status_text": "运输中",  "carrier_name": "YunExpress",  "events": []}
   ```
   
   部署成功后，你应该能在浏览器打开：
   
   ```
   https://tracking-api.yourdomain.com/api/track?nums=YT2610601001467359
   ```
   
   看到返回 JSON。
   
   这一步的目标只有一个：**证明你的后端地址是公网可访问的**。
   
   ---
   
   # 第 4 步：配置 Shopify App Proxy
   
   等后端地址可以访问后，再回 Shopify App 配置 App Proxy。
   
   建议配置：
   
   ```
   Subpath prefix: appsSubpath: trackProxy URL: https://tracking-api.yourdomain.com
   ```
   
   如果 Shopify 要你填完整 URL，可以填：
   
   ```
   https://tracking-api.yourdomain.com
   ```
   
   或者根据它的输入要求填：
   
   ```
   https://tracking-api.yourdomain.com/
   ```
   
   配置好后，测试这个地址：
   
   ```
   https://linticoshop.com/apps/track/api/track?nums=YT2610601001467359
   ```
   
   如果成功，它应该会转发到：
   
   ```
   https://tracking-api.yourdomain.com/api/track?nums=YT2610601001467359
   ```
   
   这一步跑通后，说明 Shopify App Proxy 成功了。
   
   ---
   
   # 第 5 步：拿到这些参数，填到后端环境变量
   
   你需要让后端配置这些值：
   
   ```
   SHOPIFY_APP_SECRET=从 Shopify App 里复制的 Client secretSHOPIFY_SHOP_DOMAIN=你的 xxx.myshopify.com 域名SHOPIFY_PUBLIC_DOMAIN=linticoshop.comAPP_PROXY_PREFIX=appsAPP_PROXY_SUBPATH=trackSEVENTEENTRACK_API_KEY=你的 17TRACK API KeyDATABASE_URL=PostgreSQL 数据库连接地址
   ```
   
   店铺域名建议去这里找：
   
   ```
   Shopify Admin→ Settings→ Domains
   ```
   
   你截图里看到的 `linticoshop.com` 是前台域名，但后端最好还保存原始的 `xxx.myshopify.com` 域名，用来校验 Shopify 请求里的 `shop` 参数。
   
   ---
   
   # 第 6 步：让后端做 Shopify 验签
   
   这一步是安全关键。
   
   Shopify App Proxy 请求会带类似这些参数：
   
   ```
   shop=xxx.myshopify.comtimestamp=...signature=...
   ```
   
   后端用：
   
   ```
   SHOPIFY_APP_SECRET
   ```
   
   校验 `signature` 是否正确。
   
   你的文档里明确写了，后端需要验证 Shopify App Proxy 签名；签名失败、非本店订单单号、非法格式，都应该直接拒绝，不能继续调用 17TRACK。
   
   这一步建议让开发来做，不建议你手动处理。
   
   ---
   
   # 第 7 步：接入 17TRACK
   
   等 App Proxy 已经能通，再接 17TRACK。
   
   后端逻辑按你的方案来：
   
   ```
   收到 tracking number↓检查格式↓检查是不是本店订单的物流单号↓查数据库缓存↓缓存有效：直接返回↓缓存无效：检查是否已注册到 17TRACK↓未注册：调用 17TRACK /register↓调用 17TRACK /gettrackinfo↓标准化状态↓写入数据库↓返回前端
   ```
   
   这一步重点是：**不要让任意人输入任意单号就去注册 17TRACK**，否则会浪费额度。你的方案文档里也强调，真正要拦住的是“非本店订单的 tracking number”。
   
   ---
   
   # 第 8 步：最后再做 Shopify 前台页面
   
   最后才做这个页面：
   
   ```
   /pages/track
   ```
   
   页面里有：
   
   ```
   物流单号输入框Track 查询按钮物流状态展示物流轨迹列表异常提示
   ```
   
   前端 JS 请求：
   
   ```
   /apps/track/api/track?nums=用户输入的单号
   ```
   
   你文档里推荐的页面结构是：
   
   ```
   templates/page.track.jsonsections/tracking-page.liquidassets/tracking.jsassets/tracking.css
   ```
