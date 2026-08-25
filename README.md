# FlowHub 前后端分离结构

```
flowhub/
├── frontend/   # 前端（Vite + React 19 + TS + Tailwind + shadcn/ui）
└── backend/    # 后端（预留，待接入）
```

## frontend

- 技术栈：Vite 7 / React 19 / TypeScript / Tailwind 3 / shadcn-ui / lucide-react
- 启动：`cd frontend && npm install && npm run dev`（默认 5173，可 `--port` 指定）
- 构建：`cd frontend && npm run build`
- 当前为纯 mock 前端，数据源位于 `frontend/src/data/mock.ts`

## backend（预留）

- 目录已创建，等待接入后端代码（参考 dev-flow 的 FastAPI flowhub_api 结构）。
