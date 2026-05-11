# Weaver 文档（简体中文）

<div align="right">
  <a href="../README.md">项目主页</a> |
  <a href="./README.en.md">English</a>
</div>

Weaver 是基于 LangGraph 的全栈 AI 深度研究平台。后端为 FastAPI 单体（`main.py`），前端为 Next.js 14，核心研究流程采用 Supervisor-Workers 多轮 Plan-and-Execute 架构。

---

## 快速入口

- [快速开始（本地运行 / Docker Compose 一键启动）](getting-started.md)
- [系统架构（Research Graph / DeepSearch 流程 / Mermaid 图）](architecture.md)
- [配置说明（350+ 环境变量 / Agent / 触发器 / MCP）](configuration.md)
- [使用指南（Direct / Deep Research / 代码执行 / 浏览器自动化）](usage.md)
- [部署与加固（Docker Compose / 反代鉴权 / 限流 / SSE 注意事项）](deployment.md)
- [开发指南（Makefile / pytest / ruff / 日志）](development.md)

---

## API 与协议

- [API 说明（OpenAPI 合约 + 端点清单）](api.md)
- [SSE 流式协议（事件类型 + 回滚策略）](chat-streaming.md)
- [OpenAPI 合约对齐（后端 ↔ 前端 TS types 自动生成）](openapi-contract.md)
- [工具参考（内置工具分类 + MCP 扩展）](TOOL_REFERENCE.md)
- [MCP 集成指南（stdio / SSE 配置）](mcp.md)

---

## Deep Research 专题

- [Benchmarks（回归基准与样例集）](benchmarks/README.md)
- [完整评测系统（run / judge / summarize）](../eval/deep_research_benchmark/README.md)
- [常见问题（FAQ）](faq.md)
- [路线图与规划](roadmap.md)

---

## 其他

- 安全相关：[SECURITY.md](../SECURITY.md)
- 贡献指南：[CONTRIBUTING.md](../CONTRIBUTING.md)
- 行为准则：[CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md)
