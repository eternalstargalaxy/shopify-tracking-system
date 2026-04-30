# ParcelPilot 物流查询系统

这是一个基于 Shopify 店铺页、App Proxy、自建后端和 17TRACK API 的物流查询系统工作区。

当前目录已经整理为 4 个主要部分：

- `prototype/`：早期静态原型页
- `designs/`：保留中的视觉方案页
- `shopify/`：Shopify 主题页面与前端脚本
- `backend/`：查询后端、缓存、限流、状态标准化

## 当前保留的视觉方案

目前只保留 `palette-showcase` 这一套选色方案：

- `designs/palette-showcase.html`
- `designs/palette-showcase.css`
- `designs/palette-showcase.js`

## 主要代码目录

- `backend/app/`
  后端主逻辑，包含配置、数据库、限流、Shopify 代理验签、17TRACK 适配和状态标准化。

- `backend/tests/`
  基础单元测试。

- `shopify/templates/page.track.json`
  Shopify 页面模板入口。

- `shopify/sections/tracking-page.liquid`
  Shopify 物流查询页面主体。

- `shopify/assets/tracking.js`
  前端查询逻辑和渲染逻辑。

- `shopify/assets/tracking.css`
  店铺前端样式。

- `docs/`
  中文文档，包括实施计划、1 周交付计划、数据模型、前端展示规则和本店订单单号校验说明。

- `backend/tools/`
  后端辅助工具，目前包含本店订单物流单号 CSV 导入脚本。

## 本地开发说明

1. 复制 `.env.example` 为 `.env`
2. 填写 Shopify 和 17TRACK 配置
3. 本地开发时可临时使用：
   `BYPASS_PROXY_SIGNATURE=true`
   `MOCK_WHEN_API_KEY_MISSING=true`
4. 后端入口：
   `backend/app/main.py`
5. Shopify 页面入口：
   `/pages/track`

## 当前状态

已完成：

- 后端查询主链路骨架
- SQLite 本地缓存表
- Shopify App Proxy 验签逻辑
- IP 限流与单号刷新限流
- 17TRACK 状态标准化
- Shopify 查询页基础实现

待继续：

- 接入真实 17TRACK Key 联调
- 接入真实 Shopify App Proxy 验签参数
- 根据最终选定视觉方案替换店铺页样式
