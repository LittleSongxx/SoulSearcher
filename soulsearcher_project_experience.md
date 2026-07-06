# SoulSearcher 项目经历

## 项目经历

SoulSearcher 是面向复杂深度研究任务的 AI Agent 平台，覆盖需求澄清、研究规划、并行检索、证据汇总、报告生成与质量校验。项目采用 **Orchestrator-Workers** 多智能体模式，结合 **ReAct + Tool Calling** 完成多源证据提炼，并通过 **A2A Protocol** 对外提供标准化研究 Agent 服务，适用于行业分析、技术调研、竞品研究和政策解读等场景。

## 技术栈

Python / LangGraph / FastAPI / Evidence-grounded RAG / PostgreSQL + pgvector / SSE / A2A Protocol

## 核心亮点

1. **多智能体研究编排**：设计 **Orchestrator-Workers** 架构，将复杂问题拆解为并行研究任务；主控 Agent 负责任务分发、过程反思和结果收敛，研究 Agent 基于 **ReAct** 循环完成检索、阅读与总结，提升复杂任务覆盖度。

2. **可信检索与证据增强生成**：构建 **Evidence-grounded RAG** 链路，统一接入网页、学术资料、私有文档和外部系统；通过来源策略、读取深度和权限控制沉淀可追溯证据，降低幻觉引用和来源不可验证问题。

3. **报告质量评估闭环**：引入 **Quality Gate / Eval** 机制，评估引用覆盖、主张-证据一致性、结构完整性和内容相关性；质量不达标时自动触发补充研究与重写，提升报告交付可靠性。

4. **A2A 标准化能力开放**：基于 **A2A Protocol** 暴露远程 Agent 能力，支持 **Agent Card** 能力发现、`task` 生命周期管理、`message` 流式交互、`artifact` 结果返回，以及暂停续跑、幂等请求和回调通知。

5. **长任务稳定性与可观测性**：构建 **Observability / Tracing** 能力，记录任务进度、工具调用、研究树、质量结果和 Token 消耗；结合上下文裁剪、异常兜底、循环检测和长期记忆召回，提升长链路 Agent 的稳定性与可控性。
