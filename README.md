# Multi-Agent Collaboration Benchmark (LangGraph)

基于 **LangGraph** 的多智能体（Multi-Agent）协作框架与评测基准。

研究的核心问题：**多 Agent 协作的增益，到底来自「真协作」，还是仅仅来自「多算了几遍」？协作模式应该对所有任务一刀切，还是按任务类型自动匹配？**

## 亮点

1. **任务感知自适应路由（核心特色）**——按任务类型自动匹配最优协作拓扑，WorkBuddy 式任务切换：
   - `math / code` → 中心化（星型 Supervisor）
   - `creative / translation` → 去中心化（对等黑板）
   - `debate / open_reasoning / fact_checking / planning / science / tool_use` → 分层（树型）
2. **三种协作拓扑（LangGraph `StateGraph` 实现）**
   - 中心化：众数投票 / 中位数 / 代码流水线（评审 + 实测）
   - 去中心化：圆桌互评 / 辩论 / 翻译校对
   - 分层：多角色并行作答 + 仲裁综合（并集去重 + 条目齐全）
3. **公平评测方法论**——新增「单 Agent 自洽基线」（同一 agent N=5 次独立采样 + 聚合），把「多采样增益」与「协作增益」拆开，这是业界多 Agent benchmark 常忽略的混淆变量。
4. **200 题 × 10 类别**基准，`math` 用规则评分（数值 1% 相对容差），其余用 LLM-as-Judge。

## 结果摘要

模型：`deepseek-v4-flash`（OpenAI 兼容接口，内部模型）。

| 指标 | 单 Agent | 自洽基线 | 多 Agent |
|---|---|---|---|
| 总体正确率（200 题） | **92.0%** | **97.0%** | **94.0%** |

分类分化（多 Agent − 单 Agent）：

| 类型 | 类别 | 变化 |
|---|---|---|
| 明显增益 | 翻译 +15%（80→95）、数学 +5%、开放推理 +5%、规划 +5% | ✅ |
| 持平 | 创意写作 / 辩论 / 科学 / 工具使用 | ➖ |
| 略低 | 事实核验 −5%、代码 −5% | ⚠️ |

**结论**：协作在「综合 / 推理 / 本地化」类任务上带来真实增益；价值在于**任务感知路由**把协作用在正确的地方，而非无差别堆叠 agent。

完整逐题报告见 [`reports/benchmark_lg_report.html`](reports/benchmark_lg_report.html)。

## 目录结构

```
multi-agent-collab-benchmark/
├── src/
│   ├── collab_langgraph.py        # 核心：LangGraph 协作库（三种拓扑 + 单/自洽基线）
│   ├── collab_patterns_legacy.py  # 旧手写版（ThreadPool 编排，非 LangGraph，供 A/B 对照）
│   ├── run_benchmark.py           # benchmark 运行器（single / selfconsistency / multi 三列）
│   ├── make_report.py             # 自包含 HTML 报告生成器
│   ├── test_collab_langgraph.py   # 离线冒烟测试（验证图编译 + 契约字段）
│   ├── generate_benchmark.py      # 生成 MultiAgentCollabBench 数据集（200 题）
│   ├── clean_partial_results.py   # 清理 ERROR/脏数据，供断点续跑
│   └── run_until_done.py          # 被系统杀掉后自动重启，直到跑完
├── data/
│   ├── MultiAgentCollabBench.json # 基准题目（输入）
│   └── benchmark_lg_results.json  # 全量结果（输出，600 行：200 题 × 3 模式）
├── reports/
│   └── benchmark_lg_report.html   # 结果汇总报告
├── requirements.txt
├── .env.example                   # 环境变量模板（复制为 .env 使用）
└── .gitignore
```

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 API Key
cp .env.example .env
#   编辑 .env，填入 DEEPSEEK_API_KEY（或任意 OpenAI 兼容接口的 key）

# 3. 离线冒烟测试（不联网，验证图能编译 + 返回契约字段）
python src/test_collab_langgraph.py
#    加 --live 会真实调用一次模型验证连通性

# 4. 端到端跑少量题（先验证全链路）
python src/run_benchmark.py --limit 4 --out data/benchmark_smoke.json

# 5. 生成 HTML 报告
python src/make_report.py data/benchmark_smoke.json reports/benchmark_smoke.html
```

## 全量运行（200 题）

```bash
python src/run_benchmark.py --out data/benchmark_lg_results.json
```

- 结果按 `(task_id, mode)` **增量落盘**，支持断点续跑；中断后重跑同一命令即可续上。
- 常用参数：`--limit N`（只跑前 N 题）、`--category math,code`（只跑某类）、`--force`（忽略缓存重跑）、`--no-judge` / `--judge-only`（答案与评分分开）。
- 用旧手写版对照：`python src/run_benchmark.py --lib collab_patterns_legacy --out data/benchmark_legacy.json`。

> 注：推理型模型（如 `deepseek-v4-flash`）复杂题延迟高，库内置硬超时与优雅降级（multi 失败自动回退单 Agent）。本机跑 200 题约需数小时，若中途进程被杀，可用 `python src/run_until_done.py data/benchmark_lg_results.json` 自动续跑。

## 安全提示

- `.env` 含 API Key，**已被 `.gitignore` 忽略，切勿提交到公开仓库**。
- 提交前请确认仓库内不含任何 `sk-` 开头的密钥。

## 依赖

- `langgraph`、`langchain-openai`、`python-dotenv`（见 `requirements.txt`）
- Python 3.10+
