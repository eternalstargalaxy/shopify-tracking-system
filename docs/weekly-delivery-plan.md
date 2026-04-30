# 物流查询系统 1 周交付计划

## 目标

用 1 周时间把物流查询系统从当前原型推进到可联调、可上线准备的状态。

整体链路：

```text
Shopify 查询页
-> Shopify App Proxy
-> 自建后端
-> 数据库缓存
-> 17TRACK API
-> 标准化数据
-> 前端展示
```

## 模块拆分

### 1. 前端展示模块

负责 Shopify 店铺内用户看到的物流查询页面。

工作内容：

- 查询输入框
- URL 参数自动查询
- 查询中、成功、失败、空状态
- 物流状态卡片
- 物流轨迹时间线
- 异常和无轨迹提示
- 移动端适配
- 根据最终 UI 方案调整视觉风格

交付物：

- `shopify/templates/page.track.json`
- `shopify/sections/tracking-page.liquid`
- `shopify/assets/tracking.js`
- `shopify/assets/tracking.css`
- 页面效果截图或本地预览地址

### 2. Shopify App Proxy 模块

负责把店铺前台请求安全转发到自建后端。

工作内容：

- 配置 App Proxy 路径
- 前端请求统一走 `/apps/track/api/track`
- 后端校验 Shopify 签名
- 校验店铺域名白名单
- 支持本地开发时跳过签名

交付物：

- App Proxy 配置说明
- `SHOPIFY_APP_SECRET` 配置项
- `ALLOWED_SHOP_DOMAINS` 配置项
- 验签通过 / 失败的测试结果

### 3. 后端查询模块

负责接收查询请求并返回标准化物流数据。

工作内容：

- 提供 `GET /api/track?nums=xxx`
- 校验物流单号格式
- 支持多个单号查询
- 调用缓存层
- 调用 17TRACK 适配层
- 统一返回前端需要的数据结构

交付物：

- `backend/app/main.py`
- `backend/app/services.py`
- 接口响应示例
- 本地 mock 查询通过

### 4. 17TRACK 适配模块

负责和 17TRACK API 通信。

工作内容：

- 注册单号
- 查询轨迹
- 处理上游错误
- 保存原始响应
- 支持没有 API Key 时使用 mock 数据
- 避免重复注册同一单号

交付物：

- `backend/app/seventeen_track.py`
- 17TRACK 请求配置说明
- 注册逻辑说明
- 真实 API 联调记录

### 5. 缓存与数据库模块

负责降低 API 成本，并保存物流状态。

工作内容：

- 建立 `tracking_records` 表
- 保存标准状态、轨迹、原始响应
- 保存是否已注册到 17TRACK
- 设置缓存过期时间
- 已签收、异常、暂无信息使用不同缓存时长

交付物：

- `backend/app/db.py`
- `docs/data-model.md`
- 本地 SQLite 数据库
- 后续 PostgreSQL 迁移建议

### 6. 防滥用与成本控制模块

负责防止恶意请求消耗 17TRACK 额度。

工作内容：

- IP 每分钟限流
- IP 每日限流
- 同一单号短时间刷新限流
- 非法格式直接拒绝
- 非店铺来源请求拒绝
- 异常请求记录

交付物：

- `backend/app/rate_limit.py`
- 限流规则说明
- 限流测试结果

### 7. 状态标准化模块

负责把 17TRACK 原始状态转为系统内部状态。

工作内容：

- 定义内部状态枚举
- 维护原始状态到内部状态的映射
- 生成前端展示文案
- 生成支持提示文案

交付物：

- `backend/app/normalization.py`
- `docs/frontend-rendering.md`
- 状态映射说明

### 8. 文档与部署模块

负责项目交接、部署和后续维护。

工作内容：

- 中文 README
- 环境变量说明
- 本地启动说明
- Shopify 配置说明
- 部署平台建议
- 联调 checklist

交付物：

- `README.md`
- `backend/README.md`
- `.env.example`
- `docs/implementation-plan.md`
- `docs/weekly-delivery-plan.md`

## 1 周每日交付安排

### 第 1 天：需求收口和项目整理

目标：

- 明确系统边界
- 整理目录结构
- 保留最终选定的 UI 方向
- 删除不需要的旧方案

当天工作：

- 根据方案 PDF 和评论整理实施计划
- 明确模块拆分
- 保留 `palette-showcase` 风格
- 清理旧 UI 方案文件
- 将所有 Markdown 文档改为中文

当天交付：

- 整理后的目录结构
- 中文 `README.md`
- `docs/implementation-plan.md`
- `docs/weekly-delivery-plan.md`

验收标准：

- 根目录清晰
- 只保留当前需要的 UI 方案
- 文档能说明项目做什么、怎么做

### 第 2 天：后端基础和数据模型

目标：

- 后端服务能启动
- 数据库结构稳定
- 本地 mock 查询能跑通

当天工作：

- 完成 FastAPI 项目骨架
- 建立 `tracking_records` 表
- 建立 `rate_limit_hits` 表
- 实现配置读取
- 实现健康检查接口
- 实现 mock 数据模式

当天交付：

- `backend/app/main.py`
- `backend/app/config.py`
- `backend/app/db.py`
- `backend/README.md`
- `docs/data-model.md`

验收标准：

- `/health` 返回正常
- `/api/track?nums=xxx` 在 mock 模式下返回物流数据
- 数据库能写入查询记录

### 第 3 天：查询流程和状态标准化

目标：

- 查询流程完整
- 前端不依赖 17TRACK 原始状态

当天工作：

- 实现物流单号解析和格式校验
- 实现查询主流程
- 实现状态标准化
- 实现支持提示文案
- 实现缓存判断
- 补充基础单元测试

当天交付：

- `backend/app/services.py`
- `backend/app/normalization.py`
- `backend/tests/test_core.py`
- `docs/frontend-rendering.md`

验收标准：

- 支持单号去重
- 非法格式不会请求上游
- 查询结果包含 `normalizedStatus`
- 单元测试通过

### 第 4 天：17TRACK 接入和成本控制

目标：

- 接上 17TRACK 适配层
- 控制注册和查询成本

当天工作：

- 实现 17TRACK 注册逻辑
- 实现 17TRACK 查询逻辑
- 保存原始响应
- 避免重复注册
- 按状态设置不同缓存时长
- 处理上游错误

当天交付：

- `backend/app/seventeen_track.py`
- 真实 / mock 模式切换能力
- 17TRACK 接口配置说明

验收标准：

- 没有 API Key 时可用 mock 数据
- 有 API Key 时可切换真实请求
- 缓存有效期内不重复调用 17TRACK
- 已注册单号不会重复注册

### 第 5 天：Shopify 页面和 App Proxy

目标：

- Shopify 查询页能通过 App Proxy 查询后端

当天工作：

- 完成 Shopify Liquid 页面
- 完成前端查询脚本
- 支持 `/pages/track?nums=xxx` 自动查询
- 完成 App Proxy 请求路径配置
- 完成 Shopify 签名校验
- 完成店铺域名白名单

当天交付：

- `shopify/templates/page.track.json`
- `shopify/sections/tracking-page.liquid`
- `shopify/assets/tracking.js`
- `shopify/assets/tracking.css`
- `backend/app/shopify_proxy.py`

验收标准：

- 前端不直接请求 17TRACK
- 前端请求走 `/apps/track/api/track`
- 签名错误会被后端拒绝
- URL 带 `nums` 时页面自动查询

### 第 6 天：UI 精修和异常体验

目标：

- 查询页接近可上线视觉效果
- 各种异常状态都有清楚反馈

当天工作：

- 根据 `palette-showcase` 方向精修 Shopify 页面
- 优化移动端布局
- 补充 loading 状态
- 补充空状态
- 补充失败状态
- 补充无轨迹状态
- 补充异常状态样式

当天交付：

- 精修后的 `tracking.css`
- 精修后的 `tracking.js`
- 页面截图或预览

验收标准：

- 手机端和桌面端都可读
- 查询失败不会页面空白
- 多单号部分失败时，成功结果仍能展示
- 不同 `normalizedStatus` 有不同视觉提示

### 第 7 天：联调、测试和上线准备

目标：

- 完成真实环境联调
- 形成上线 checklist

当天工作：

- 配置真实 Shopify App Proxy
- 配置真实 17TRACK API Key
- 联调真实物流单号
- 验证缓存和限流
- 验证签名校验
- 整理部署说明
- 整理上线前检查清单

当天交付：

- 联调记录
- 上线 checklist
- 最终 `.env` 配置说明
- 最终项目 README

验收标准：

- Shopify 店铺页面能查真实物流
- 后端能正常缓存
- 非法请求被拒绝
- 17TRACK Key 不出现在前端
- 文档足够交接给后续维护者

## 每天固定检查项

- 是否有可演示的页面或接口
- 是否有可读的中文说明
- 是否有明确的下一步
- 是否避免把 17TRACK Key 暴露到前端
- 是否避免无效请求消耗 API 成本

## 当前已完成情况

已完成：

- 需求方案阅读
- 多套 UI 方案探索
- 保留 `palette-showcase` 方向
- 后端基础骨架
- 数据库模型
- 状态标准化
- mock 查询流程
- Shopify 查询页初版
- 中文文档初版

待继续：

- 删除旧 UI 方案文件
- 将 `palette-showcase` 风格正式融合到 Shopify 查询页
- 接入真实 Shopify App Proxy
- 接入真实 17TRACK API Key
- 真实环境联调

## 新版方案补充项

新版 PDF 在原计划基础上新增或强调了以下交付项：

- 增加 `order_tracking_numbers` 表，用于校验物流单号是否属于本店订单。
- 增加 `REQUIRE_ORDER_TRACKING_MATCH` 配置，生产环境建议开启。
- 增加 CSV 导入工具：`backend/tools/import_order_trackings.py`。
- 后端响应保留 `provider_status` 和 `provider_status_description`，方便排查和持续维护状态映射。
- 前端仍只依赖 `normalized_status` 渲染 UI，不能用 provider 原始状态决定展示逻辑。
- 防滥用策略重点从“重限流”调整为“阻止非本店订单单号进入 17TRACK 注册流程”。
