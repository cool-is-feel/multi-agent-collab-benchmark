# Multi-Agent Collaboration Control Layer

这是一套可嵌入 WorkBuddy 等现有平台的多智能体协作控制层，同时保留原有 benchmark：

- **动态拓扑与团队**：一级拓扑只区分中心化和去中心化；中心化根据任务复杂度、可分解性、风险与预算进一步选择扁平星型、专用流水线或 2-4 层树型编排，并生成本次任务的角色配置。
- **受控通信与有限终止**：所有模型调用受步骤、时间、调用、Token、成本和修订次数硬边界约束；消息通道拦截重复内容、低语义增量、状态回退和无引用的已决事项重开。
- **可验证完成**：通过声明式规则、真实测试、证据要求和独立评审形成验证报告；失败时只在剩余预算内执行一次局部重构或高风险仲裁。
- **完整审计**：输出任务画像、拓扑评分、团队、结构化消息、预算用量、验证报告、终止原因和追加式事件轨迹。

原来的固定拓扑 API 仍然可用；推荐新接入使用 `solve_controlled` 或 `run("adaptive", ...)`。

控制层不声称能够先验知道“绝对最优”拓扑。它对两个候选计算期望效用：

```text
U = 0.50 × 任务适配度 + 0.30 × 验证能力
    - 0.10 × 预计成本 - 0.05 × 预计时延 - 0.25 × 风险惩罚
```

每次结果的 `topology_decision.alternatives` 保存两个候选的全部分项，`scorecard` 保存最终效用，
`selection_confidence` 由第一、第二名的效用差计算。该解释用于事前合理性审计；真正证明路由有效，仍需
对同一批任务分别运行两个候选拓扑，比较验证通过率、成本和时延，并计算选择准确率与策略后悔值。
`layers` 统计执行树总层数（叶子执行者也算一层）：`2` 表示扁平星型，`3-4` 表示增加中层汇总。

一个「多智能体协作」能力评测套件：用三种协作拓扑实现库跑一个 200 题 / 10 类别的基准数据集，与「单 Agent 基线」逐题对比，量化「协作是否真的带来增益」。

- **协作库** `src/collab_patterns.py`：三种拓扑（中心化 / 去中心化 / 分层），自包含、与 benchmark 解耦，可独立复用。
- **控制层** `src/control_layer.py`：任务画像、动态拓扑、预算、通信门禁、验证/仲裁、审计和平台适配器。
- **评测器** `src/run_benchmark.py`：对每题同时跑 `single`（基线）与 `multi`（协作），做正确性评分。
- **报告** `src/make_report.py`：把结果 JSON 生成自包含 HTML 汇报。
- **数据集** `data/MultiAgentCollabBench.json`：200 题，10 类别 × 各 20 题，难度易/中/难。

## 目录结构

```
.
├── src/
│   ├── collab_patterns.py   # 多 Agent 协作库（三种拓扑，统一接口）
│   ├── run_benchmark.py     # benchmark 驱动 + 评分（单 vs 多）
│   └── make_report.py       # 生成 HTML 汇报
├── data/
│   └── MultiAgentCollabBench.json   # 基准数据集（200 题）
├── requirements.txt
├── .env.example             # 环境变量模板（复制为 .env 填密钥）
└── README.md
```

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置模型（复制模板，填入你的 API Key）
cp .env.example .env   # Windows 用：copy .env.example .env

# 3. 跑 benchmark（默认跑全部 200 题，自动断点续跑）
python src/run_benchmark.py

# 4. 生成 HTML 汇报
python src/make_report.py data/benchmark_v2_results.json reports/benchmark_v2_report.html
```

## 嵌入现有平台

最小调用只需要一个可 JSON 序列化的请求：

```python
from collab_patterns import solve_controlled

result = solve_controlled(
    "为客户数据系统制定迁移方案，覆盖步骤、风险和验收指标",
    task_type="analysis",
    task_id="workbuddy-task-42",
    risk_level="high",
    budget={
        "max_calls": 12,
        "max_steps": 40,
        "max_total_tokens": 18000,
        "max_seconds": 300,
        "max_cost_usd": 0.10,
        "max_revisions": 1,
    },
    completion_criteria=[{
        "name": "required-sections",
        "kind": "rule",
        "config": {"required_terms": ["步骤", "风险", "验收"]},
    }],
)
```

平台若需要实时接收审计事件，可传入适配器：

```python
from control_layer import CallbackPlatformAdapter, CollaborationControlLayer

adapter = CallbackPlatformAdapter(lambda event: platform_event_bus.publish(event))
result = CollaborationControlLayer(adapter=adapter).execute(request)
```

控制层返回的关键字段为 `final_result`、`topology_decision`、`team`、
`verification_report`、`termination`、`budget_usage` 和 `audit_trail`。平台不需要理解内部
Agent 实现，只需持久化请求/结果并转发事件。

Agent 消息统一序列化为协议 `1.0` 信封，包含 `message_id`、`sender`、`kind`、`phase`、
`claims`、`evidence`、`decision_refs`、`decision_key`、`revises_decision`、`accepted`、
`rejection_reason` 和 `novelty_score`。平台可用 `decision_key` 锁定已决事项；只有显式携带
原决定 ID 的 `revises_decision` 才允许进入修订流程，普通消息不能把状态退回旧阶段。

### 常用参数（`run_benchmark.py`）

| 参数 | 作用 |
|---|---|
| `--limit N` | 只跑前 N 题（冒烟测试） |
| `--category math,code` | 只跑指定类别（逗号分隔） |
| `--per-category 3 --balanced` | 每类按难度均衡取 3 题 |
| `--no-judge` | 只生成答案、不评分 |
| `--judge-only` | 只对已有答案评分（复用其它框架产出的答案） |
| `--force` | 忽略缓存，全部重跑 |
| `--out <path>` | 结果输出路径 |
| `--fixed-topology` | 复现实验旧行为：按类别固定拓扑，关闭动态控制层 |

结果按 `(task_id, mode)` 增量落盘，再次运行自动跳过已完成部分。

## 我们实测的结果

在 `deepseek-v4-flash` 模型上跑完 200 题：

| | 单 Agent | 多 Agent | 增益 |
|---|---|---|---|
| **总体正确率** | 95.0%（190/200） | **98.5%（197/200）** | **+3.5pp** |
| 数学求解 | 85% | 100% | +15% |
| 代码生成与调试 | 90% | 100% | +10% |
| 规划与决策 | 90% | 100% | +10% |
| 翻译与本地化 | 95% | 100% | +5% |

多 Agent 的价值来自「独立计算 + 众数投票」（数学精确题）、「多视角并行 + 对照要求仲裁」（主观题）、「代码流水线逐条核对约束」（代码题），而非「拆解重组」。

## 统一接口：怎么接入你自己的框架

协作库的每个模式暴露统一契约：

```python
from collab_patterns import run   # 或 centralized/decentralized/hierarchical 类

result = run("centralized", task, task_type="coding")
# result 必含字段：
#   final_result  : str   —— 最终答案（评分器只读这个字段）
#   mechanism     : str   —— 命中的子机制
#   confidence    : float —— 0-1 置信度
#   llm_calls / token_total / elapsed —— 成本统计
```

**评测任何其它多 Agent 框架，只需让它对每道题产出一个 `final_result`**，把答案写进与 `data/benchmark_v2_results.json` 相同的结构（含 `task_id / category / expected_output / final_result`，`mode` 字段填你的框架名），然后：

```bash
python src/run_benchmark.py --judge-only --out 你的结果.json
```

即可复用整套评分（数学规则评分 + LLM-as-Judge）与报告，无需改动评分代码。详见 `run_benchmark.py` 的 `judge()` / `score_math()`。

## 评分机制

- **数学题**：规则评分 —— 数值结果与期望值做 1% 相对容差比较（支持分数、科学计数法、`=` 后答案），客观精确。
- **其余 9 类**：LLM-as-Judge —— 对照 `expected_output` 逐条提取关键要点核对，处理「或」关系、不罚简洁、代码只看功能正确性。
- **公平性**：单 / 多 Agent 用同一套评分标准。

## 配置说明

通过环境变量接入任意 OpenAI 兼容端点（`.env` 文件）：

```bash
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-chat
```

也支持异构多模型：用 `collab_patterns.register_model(name, base_url, api_key)` 注册，再给 `run(...)` 传 `models=[...]` 让不同 Agent 用不同模型/厂商。

## 许可证

[MIT](LICENSE)
