r"""
多Agent协作 —— 基于 LangGraph 的三种拓扑模式库（自包含，与 benchmark 完全分离）
==============================================================================
这是 `collab_patterns(2).py` 的 LangGraph 重写版：协作编排层从「手写 ThreadPool +
纯 Python 类」改为真正的 `langgraph.graph.StateGraph` 状态机（节点 / 边 / 条件路由），
LLM 调用层与解析/聚合工具原样复用（二者已被验证可靠）。

三种拓扑模式（与旧版语义一致）
----------------------------
  1. CentralizedOrchestrator  中心化编排（星型 / Supervisor 拓扑）
     Controller 掌握全局，N 个 Worker 彼此隔离、独立产出，Controller 统一聚合。
     图：START →(按 task_type 路由)→ fanout → aggregate → END
         coding 题走：dev → review ⇄(条件边循环) → code_finalize → END

  2. DecentralizedCollaboration  去中心化协作（对等 / 黑板拓扑）
     无中心权威，peer 平级读写共享黑板，共识「涌现」。
     图：START →(按 task_type 路由)→ {roundtable | refine | debate | translate} → END

  3. HierarchicalArchitecture  分层架构（树型拓扑）
     分治：并行多视角作答 → 仲裁综合 / 分组投票逐层聚合。
     图：START →(按 task_type 路由)→ {analysis | group_vote} → END

统一接口（给 benchmark 的契约，与旧版完全一致）
----------------------------------------------
    run(mode, task, task_type=None, test_cases=None, **kwargs) -> dict
      mode ∈ {single, selfconsistency, centralized, decentralized, hierarchical}
    返回 dict 必含：
        final_result   : str  —— 最终答案（benchmark 评分器读这个字段）
        mechanism      : str  —— 命中的子机制
        confidence     : float—— 0-1 置信度
        consensus_reached : bool
        agreement      : float—— 0-1 一致程度
        llm_calls      : int  —— LLM 调用次数
        token_input/output/total : int
        elapsed        : float—— 秒
        models_used    : list
        history        : list —— 通信消息（可审计）

新增：selfconsistency 公平基线
-----------------------------
    同一 agent（无角色差异）N 次独立采样 → 与 multi 相同聚合（众数/中位数/语义多数派），
    用于把「多采样带来的自洽增益」与「真正的多 Agent 协作增益」分开。

异构多模型：register_model(name, base_url, api_key) + run(..., models=[...]) 循环分配。

本模块自包含：自带最小 LLM 适配器（langchain_openai + dotenv，读取 DEEPSEEK_* 环境变量），
不依赖任何项目内其它文件，也不依赖 benchmark。
"""

from __future__ import annotations

import logging
import math
import operator
import os
import queue
import re
import statistics
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Annotated, Any, Dict, List, Optional, Tuple, TypedDict

from dotenv import load_dotenv
from langgraph.graph import StateGraph, START, END

load_dotenv()

logger = logging.getLogger("collab_langgraph")

try:
    from langchain_openai import ChatOpenAI
except ImportError:  # pragma: no cover
    ChatOpenAI = None


# ═══════════════════════════════════════════════════════════════
# 配置（集中管理，便于统一调参）
# ═══════════════════════════════════════════════════════════════

MAX_TOKENS = 400          # 单次生成长度上限，防止拖慢 benchmark
REQUEST_TIMEOUT = 300      # 单次请求硬超时（秒）；推理型模型部分调用很慢，需放宽避免误杀
MAX_RETRIES = 1            # LLM 调用重试次数（推理型模型的慢调用重试也慢，快速失败更划算）
MAX_CONCURRENT_LLM = 4     # 全局并发上限，避免服务端排队/限流导致反向减速
_LLM_SEMAPHORE = threading.BoundedSemaphore(MAX_CONCURRENT_LLM)

# 各阶段温度（集中定义，便于统一调参）
T_CHOICE = 0.6            # 客观选择题 agent
T_ESTIMATE = 1.1          # 估计题 agent（高方差，群体智慧）
T_VERIFY = 0.2            # 复核/代表汇总（低温，确定性）
T_SYNTH = 0.3             # 综合/融合/法官
T_DRAFT = 0.9             # 创意初稿
T_REFINE = 0.5            # 批评/打磨
T_DEBATE = 0.7            # 辩论双方
T_COMPUTE = 0.3           # 精确计算 agent（低方差，多数投票自洽性）


# ═══════════════════════════════════════════════════════════════
# 最小 LLM 适配器（自包含，可替换为你的模型接入层）
# ═══════════════════════════════════════════════════════════════

MODEL_REGISTRY: Dict[str, Dict[str, Any]] = {
    "deepseek-chat": {
        "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
        "api_key": os.getenv("DEEPSEEK_API_KEY"),
    },
    "deepseek-coder": {
        "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
        "api_key": os.getenv("DEEPSEEK_API_KEY"),
    },
    "deepseek-v4-flash": {
        "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
        "api_key": os.getenv("DEEPSEEK_API_KEY"),
    },
}


def register_model(name: str, base_url: str, api_key: str = None, **kwargs) -> None:
    """注册外部模型（OpenAI 兼容接口），实现异构多模型。"""
    cfg = {"base_url": base_url, "api_key": api_key or os.getenv("DEEPSEEK_API_KEY")}
    cfg.update(kwargs)
    MODEL_REGISTRY[name] = cfg


@dataclass
class _RunStats:
    input: int = 0
    output: int = 0
    calls: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


_default_stats = _RunStats()
_current_stats: ContextVar[Optional[_RunStats]] = ContextVar("collab_lg_run_stats", default=None)


def _stats() -> _RunStats:
    return _current_stats.get() or _default_stats


def get_token_stats() -> Dict[str, int]:
    """获取累计 token 统计（线程安全）。"""
    stats = _stats()
    with stats.lock:
        return {"input": stats.input, "output": stats.output}


def reset_token_stats() -> None:
    """重置 token 统计。"""
    stats = _stats()
    with stats.lock:
        stats.input = 0
        stats.output = 0


def _track_usage(resp) -> None:
    """尽力而为地统计 token（部分 provider 不返回则忽略）。"""
    try:
        usage = (resp.response_metadata or {}).get("token_usage", {}) or {}
        inp = usage.get("prompt_tokens", 0)
        out = usage.get("completion_tokens", 0)
        if inp or out:
            stats = _stats()
            with stats.lock:
                stats.input += inp
                stats.output += out
    except Exception:  # noqa: BLE001
        pass


def _invoke_hard_timeout(llm, prompt: str, timeout: float):
    """在守护线程里调用 llm.invoke，主线程用 queue.get(timeout) 强制硬超时。

    部分兼容端点对 HTTP 层 timeout 不生效（如推理型模型会长时间生成隐藏思维链），
    这里用墙钟硬超时兜底，保证单次调用不会无限挂起。
    """
    q: "queue.Queue" = queue.Queue(maxsize=1)

    def _worker():
        try:
            q.put(("ok", llm.invoke(prompt)))
        except Exception as e:  # noqa: BLE001
            q.put(("err", e))

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    try:
        kind, val = q.get(timeout=timeout)
    except queue.Empty:
        raise TimeoutError(f"LLM 调用硬超时（>{timeout:.0f}s）")
    if kind == "err":
        raise val
    return val


def _llm(prompt: str, model: Optional[str] = None, temperature: float = 0.7,
         max_retries: int = MAX_RETRIES, max_tokens: int = MAX_TOKENS) -> str:
    """调用 LLM（带硬超时 + 重试）。model=None 用环境默认模型。"""
    if ChatOpenAI is None:
        raise RuntimeError("缺少 langchain-openai，请 pip install langchain-openai")
    model_name = model or os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    cfg = MODEL_REGISTRY.get(model_name, {})
    extra = {k: v for k, v in cfg.items() if k not in ("base_url", "api_key")}
    llm = ChatOpenAI(
        model=model_name,
        api_key=cfg.get("api_key") or os.getenv("DEEPSEEK_API_KEY"),
        base_url=cfg.get("base_url") or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=REQUEST_TIMEOUT,
        **extra,
    )
    last_err = None
    for attempt in range(1, max_retries + 1):
        call_start = time.perf_counter()
        try:
            with _LLM_SEMAPHORE:
                resp = _invoke_hard_timeout(llm, prompt, REQUEST_TIMEOUT)
            _track_usage(resp)
            content = resp.content.strip()
            if content:
                logger.info("LLM 调用完成 model=%s attempt=%d/%d elapsed=%.3fs",
                            model_name, attempt, max_retries, time.perf_counter() - call_start)
                return content
            raise RuntimeError("模型返回空内容")
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning("LLM 调用失败 model=%s attempt=%d/%d elapsed=%.3fs error=%s",
                           model_name, attempt, max_retries,
                           time.perf_counter() - call_start, e)
            if attempt < max_retries:
                time.sleep(1.0 * attempt)
    raise RuntimeError(f"LLM 调用失败（已重试 {max_retries} 次）：{last_err}")


def _chat(prompt: str, model: Optional[str] = None, temperature: float = 0.7,
          max_tokens: int = MAX_TOKENS) -> str:
    stats = _stats()
    with stats.lock:
        stats.calls += 1
    return _llm(prompt, model=model, temperature=temperature, max_tokens=max_tokens)


def _parallel_chat(prompts: List[str], models: Optional[List[Optional[str]]] = None,
                   temperatures: Optional[List[float]] = None) -> List[str]:
    """并行调用多个 LLM（用于彼此独立、无依赖的 Agent），加速 benchmark。"""
    n = len(prompts)
    ms = models or [None] * n
    ts = temperatures or [0.7] * n
    run_stats = _current_stats.get()

    def call(i: int) -> str:
        token = _current_stats.set(run_stats) if run_stats is not None else None
        try:
            return _chat(prompts[i], model=ms[i], temperature=ts[i])
        finally:
            if token is not None:
                _current_stats.reset(token)

    with ThreadPoolExecutor(max_workers=min(n, 8)) as ex:
        return list(ex.map(call, range(n)))


# ═══════════════════════════════════════════════════════════════
# LangGraph 状态
# ═══════════════════════════════════════════════════════════════

class CollabState(TypedDict, total=False):
    # ── 输入 ──
    task: str
    task_type: str                 # choice/estimation/computation/coding/creative/analysis/decision/translation
    test_cases: List[dict]         # 仅 coding 用到
    n_agents: int                  # 中心化/去中心化 worker 数（默认 5/4）
    n_groups: int                  # 分层组数（默认 3）
    agents_per_group: int          # 分层每组 agent 数（默认 3）
    models: Optional[List[str]]    # 异构多模型列表（循环分配）
    # ── 工作区（节点间流转）──
    agents: list                   # 本轮 Agent 池（list[Agent]）
    answers: list                  # 原始作答文本（fanout 产出）
    draft: str                     # 草稿 / 代码 / 译文
    review: str                    # 评审 / 校对意见
    review_passed: bool
    round: int                     # 循环计数
    just_fixed: bool
    history: Annotated[list, operator.add]   # 通信消息（可审计）
    # ── 输出 ──
    final_result: str
    mechanism: str
    confidence: float
    agreement: float
    consensus_reached: bool


# ═══════════════════════════════════════════════════════════════
# 解析 / 角色库 / 聚合 / 质量评分（与旧版一致）
# ═══════════════════════════════════════════════════════════════

def _extract_number(text: str) -> Optional[float]:
    nums = re.findall(r'(\d+\.?\d*)', str(text))
    return float(nums[0]) if nums else None


def _parse_exact(text: str) -> Optional[Tuple[float, str]]:
    """解析「精确数值」答案，返回 (float, 原字符串)。"""
    s = str(text)
    m = re.search(r'答案\s*[：:]\s*(.+)', s)
    if m:
        s = m.group(1)
    elif "=" in s:
        s = s.split("=")[-1]
    m = re.search(r'(-?\d+)\s*/\s*(-?\d+)', s)          # 分数
    if m and int(m.group(2)) != 0:
        return int(m.group(1)) / int(m.group(2)), f"{m.group(1)}/{m.group(2)}"
    m = re.search(r'(-?\d+\.?\d*)[eE]([+-]?\d+)', s)     # 科学计数法
    if m:
        return float(m.group(0)), m.group(0)
    m = re.search(r'(-?\d+\.?\d*)', s)                   # 小数/整数
    if m:
        return float(m.group(1)), m.group(1)
    return None


def _extract_letter(text: str) -> Optional[str]:
    m = re.search(r'答案\s*[：:]\s*([A-Da-d])', str(text))
    if m:
        return m.group(1).upper()
    m = re.search(r'\b([A-D])\b', str(text))
    return m.group(1).upper() if m else None


@dataclass
class Agent:
    """一个协作参与者。model=None 用默认模型；不同 Agent 可指定不同模型。"""
    name: str
    role: str
    model: Optional[str] = None
    temperature: float = 0.7

    def chat(self, prompt: str, temperature: Optional[float] = None) -> str:
        return _chat(prompt, model=self.model,
                     temperature=temperature if temperature is not None else self.temperature)


DIVERSE_ROLES = [
    ("严谨分析师", "你逻辑严密，注重证据与逐步推理，主动识别直觉陷阱。"),
    ("批判质疑者", "你善于从反面质疑，检查边界条件和易错点。"),
    ("务实验证者", "你从可验证性出发，用具体计算或事实核对答案。"),
    ("全局视角者", "你从整体与长期角度审视，避免只见树木不见森林。"),
    ("经验丰富者", "你依靠领域经验快速定位关键，给出稳健判断。"),
]

CRITIC_ROLES = [
    ("创意专家", "你关注新颖性与吸引力。"),
    ("逻辑编辑", "你关注结构、一致性与表达精准。"),
    ("受众代表", "你关注可读性、共鸣与行动力。"),
    ("质量审稿", "你关注深度、完整性与专业水准。"),
]


def make_agents(roles, n, models=None, temperature=0.7) -> List[Agent]:
    """按角色列表构建 Agent 池；models 非空时循环分配（异构多模型）。"""
    agents = []
    for i in range(n):
        name, role = roles[i % len(roles)]
        model = models[i % len(models)] if models else None
        agents.append(Agent(name=name, role=role, model=model, temperature=temperature))
    return agents


def _quality(content: str, task: str) -> float:
    """中立 LLM 打 0-1 质量分（主观题置信度用）。"""
    try:
        r = _chat(f"对以下输出打质量分（0-1，只输出数字）。\n任务：{task}\n输出：\n{content[:1500]}\n分数：",
                  temperature=0.0)
        n = _extract_number(r)
        if n is not None:
            return max(0.0, min(1.0, n / 10 if n > 1 else n))
    except Exception:  # noqa: BLE001
        logger.exception("质量评分失败")
    return 0.5


def _median_aggregate(values: List[float]) -> Tuple[float, float, float, float]:
    """数值聚合。返回 (median, mean, confidence, agreement)。"""
    med = statistics.median(values)
    mean = statistics.mean(values)
    mad = statistics.median(abs(v - med) for v in values)
    scale = max(abs(med), abs(mean), 1e-9)
    agreement = math.exp(-mad / scale)
    sample_factor = 1.0 - math.exp(-len(values) / 3.0)
    confidence = agreement * sample_factor
    return med, mean, confidence, agreement


def _letter_aggregate(answers: List[str]) -> Tuple[Optional[str], float]:
    """从答案列表提取字母并多数投票。返回 (winner, agreement)。"""
    letters = [l for l in (_extract_letter(r) for r in answers) if l]
    if not letters:
        return None, 0.0
    cnt = Counter(letters)
    winner, n = cnt.most_common(1)[0]
    return winner, n / len(letters)


def _ratio(text: str) -> float:
    """从文本中解析 0-1 一致比例，失败返回 0.5。"""
    n = _extract_number(text.split("一致比例")[-1]) if "一致比例" in text else None
    if n is None:
        return 0.5
    return max(0.0, min(1.0, n if n <= 1 else n / 100))


def _semantic_majority(task: str, answers: List[str]) -> Tuple[str, float]:
    """无字母/数字时，让中立裁判提炼多数派结论。返回 (结论, 一致度)。"""
    r = _chat(f"以下是{len(answers)}个回答，提炼多数派核心结论并估计一致比例(0-1)。\n"
              f"问题：{task}\n" + "\n".join(f"[{i+1}] {a[:300]}" for i, a in enumerate(answers)) +
              "\n格式：\n结论：<一句话>\n一致比例：<0-1>", temperature=T_SYNTH)
    m = re.search(r"结论[：:]\s*(.+)", r)
    conclusion = m.group(1).strip() if m else (answers[0][:200] if answers else "")
    return conclusion, _ratio(r)


def _synthesize_union(task: str, drafts: List[Tuple[str, str]], max_tokens: int = 1000) -> str:
    """把多个独立完整作答综合成一份最优答案（并集去重 + 条目齐全 + 简洁）。

    供分层仲裁（hierarchical.analysis）与自洽基线（selfconsistency）复用同一套综合逻辑——
    自洽基线只去掉「角色差异」，综合方式与 multi 完全一致，保证「协作增益」对比公平。
    """
    drafts_text = "\n\n".join(f"[{label}]\n{text[:1200]}" for label, text in drafts)
    return _chat(f"你是仲裁综合者。以下是 {len(drafts)} 个对同一任务的独立回答。\n\n"
                 f"任务：{task}\n\n各回答：\n{drafts_text}\n\n"
                 f"请综合出一份最优【最终答案】，要求：\n"
                 f"1) 直接回答任务本身，不要出现『来源/编号/某回答』等元话语；\n"
                 f"2) 【数量完整】先确保任务要求的条目数齐全（如 3 个角度/3 个问题/正反双方+结论/N 个维度），再展开——任何条目都不得因过度展开而挤掉后面的条目；\n"
                 f"3) 【并集去重】合并所有回答提到的不同要点，但重复内容只保留一次，控制篇幅、不要膨胀；\n"
                 f"4) 每个要点简洁（1-3 句），保留量化数据与关键论据，删去过程性、重复性表述；\n"
                 f"5) 严格执行任务的指令性动作（如「选择工具」「列出N个」「计算」「制定方案」），不得用「无需/不需要」绕过；\n"
                 f"6) 最终篇幅与『直接作答』相当，结构清晰、要点完整。",
                 temperature=T_SYNTH, max_tokens=max_tokens)


# ═══════════════════════════════════════════════════════════════
# 结果组装
# ═══════════════════════════════════════════════════════════════

def _assemble(state: Dict, start: float, models: Optional[List[str]]) -> Dict[str, Any]:
    """从图最终状态 + 全局统计组装统一返回结构（契约与旧版一致）。"""
    tokens = get_token_stats()
    stats = _stats()
    with stats.lock:
        calls = stats.calls
    return {
        "final_result": state.get("final_result", ""),
        "mechanism": state.get("mechanism", ""),
        "confidence": round(state.get("confidence", 0.0), 4),
        "consensus_reached": bool(state.get("consensus_reached", False)),
        "agreement": round(state.get("agreement", 0.0), 4),
        "llm_calls": calls,
        "token_input": tokens["input"],
        "token_output": tokens["output"],
        "token_total": tokens["input"] + tokens["output"],
        "elapsed": round(time.time() - start, 3),
        "models_used": sorted(set(models)) if models else
                       [os.getenv("DEEPSEEK_MODEL", "deepseek-chat")],
        "history": state.get("history", []),
    }


def _error_result(e: Exception, mechanism: str) -> Dict[str, Any]:
    """统一的降级返回：模式执行异常时不崩溃，返回带 error 信息的结果。"""
    logger.exception(f"[{mechanism}] 执行失败")
    tokens = get_token_stats()
    stats = _stats()
    with stats.lock:
        calls = stats.calls
    return {"final_result": f"ERROR: {e}", "mechanism": mechanism,
            "confidence": 0.0, "consensus_reached": False, "agreement": 0.0,
            "llm_calls": calls, "token_input": tokens["input"],
            "token_output": tokens["output"], "token_total": tokens["input"] + tokens["output"],
            "elapsed": 0.0, "models_used": [], "history": []}


def _post(sender: str, content: str, kind: str = "proposal") -> Dict[str, list]:
    """构造一条历史消息的增量（配合 operator.add 累加）。"""
    return {"history": [{"sender": sender, "content": content, "kind": kind}]}


# ═══════════════════════════════════════════════════════════════
# 模式一：中心化编排（星型 / Supervisor 拓扑）
# ═══════════════════════════════════════════════════════════════

def _central_fanout(state: CollabState) -> Dict[str, Any]:
    """Worker 并行独立作答（彼此隔离）。按 task_type 选 prompt 与温度。"""
    task = state["task"]
    tt = state["task_type"]
    n = state.get("n_agents", 5)
    models = state.get("models")

    if tt == "estimation":
        agents = make_agents(DIVERSE_ROLES, n, models, temperature=T_ESTIMATE)
        prompts = [f"{a.role}\n\n{task}\n\n给出你的估计值（只输出一个数字）和一句话自底向上推算。"
                   for a in agents]
    elif tt == "computation":
        agents = make_agents(DIVERSE_ROLES, max(n, 3), models, temperature=T_COMPUTE)
        prompts = [f"{a.role}\n\n{task}\n\n请严格、逐步计算，给出精确结果（最简分数或小数）。"
                   f"最后单独一行以「答案：<结果>」输出；不要估计、不要取近似值（除非题目要求）。"
                   for a in agents]
    else:  # choice 及通用
        agents = make_agents(DIVERSE_ROLES, n, models, temperature=T_CHOICE)
        prompts = [f"{a.role}\n\n问题：{task}\n\n简要推理（2-3句，小心直觉陷阱），"
                   f"最后严格以「答案：X」输出选项字母。" for a in agents]

    answers = _parallel_chat(prompts, models=[a.model for a in agents],
                             temperatures=[a.temperature for a in agents])
    hist = [{"sender": a.name, "content": r, "kind": "proposal"} for a, r in zip(agents, answers)]
    return {"agents": agents, "answers": answers, "history": hist}


def _central_aggregate(state: CollabState) -> Dict[str, Any]:
    """按 task_type 路由到不同聚合器。"""
    tt = state["task_type"]
    task = state["task"]
    agents = state.get("agents", [])
    answers = state.get("answers", [])
    if tt == "estimation":
        return _agg_median(answers)
    if tt == "computation":
        return _agg_computation(task, answers)
    return _agg_vote(task, answers, agents)


def _best_reasoning(answers: List[str], letter: str) -> str:
    for r in answers:
        if _extract_letter(r) == letter:
            return r[:600]
    return ""


def _verify(task, answers, agents) -> Tuple[str, float, list]:
    """分歧时复核：把候选与理由回传，让 peer 互相审视后重投票。"""
    summary = "\n".join(f"Agent{i+1} 选 {(_extract_letter(r) or '?')}：{r[:150]}"
                        for i, r in enumerate(answers))
    prompts = [f"{a.role}\n\n问题：{task}\n\n第一轮各方答案与理由：\n{summary}\n\n"
               f"请复核：指出别人推理的问题或吸收正确之处，最后严格以「答案：X」重新输出最终选择。"
               for a in agents]
    revised = _parallel_chat(prompts, models=[a.model for a in agents],
                             temperatures=[T_VERIFY] * len(agents))
    hist = [{"sender": "controller", "content": "初轮分歧，进入复核", "kind": "verdict"}] + \
           [{"sender": a.name, "content": r, "kind": "critique"} for a, r in zip(agents, revised)]
    winner, agree = _letter_aggregate(revised)
    if winner is None:
        winner, agree = _letter_aggregate(answers)
    return (winner if winner else "?"), agree, hist


def _agg_vote(task, answers, agents) -> Dict[str, Any]:
    """离散选项：独立多数投票 + 分歧时去中心化复核。"""
    winner, agree = _letter_aggregate(answers)
    hist = []
    if winner is None:
        winner, agree = _semantic_majority(task, answers)
        hist.append({"sender": "judge", "content": f"多数结论：{winner}", "kind": "verdict"})
    elif agree < 0.6:
        winner, agree, vhist = _verify(task, answers, agents)
        hist += vhist

    best = _best_reasoning(answers, winner)
    result = f"答案：{winner}" + (f"\n\n推理：\n{best}" if best else "")
    hist.append({"sender": "controller", "content": f"最终选择 {winner}", "kind": "verdict"})
    return {"final_result": result, "mechanism": "centralized.vote",
            "confidence": agree, "consensus_reached": agree >= 0.5, "agreement": agree,
            "history": hist}


def _agg_median(answers) -> Dict[str, Any]:
    """连续数值：中位数聚合（群体智慧，误差对称抵消）。"""
    vals = [v for v in (_extract_number(r) for r in answers) if v is not None]
    if not vals:
        return {"final_result": "N/A", "mechanism": "centralized.median",
                "confidence": 0.0, "consensus_reached": False, "agreement": 0.0}
    med, mean, conf, agree = _median_aggregate(vals)
    result = f"{med:.4g}\n\n（{len(vals)}位独立估计的中位数；均值 {mean:.2f}）"
    hist = [{"sender": "controller", "content": f"median={med:.2f} n={len(vals)}", "kind": "synthesis"}]
    return {"final_result": result, "mechanism": "centralized.median",
            "confidence": conf, "consensus_reached": conf >= 0.5, "agreement": agree,
            "history": hist}


def _agg_computation(task, answers) -> Dict[str, Any]:
    """精确计算：独立计算 + 众数投票（self-consistency，误差不对称须用众数）。"""
    parsed = [p for p in (_parse_exact(r) for r in answers) if p is not None]
    if not parsed:
        return {"final_result": "N/A", "mechanism": "centralized.computation",
                "confidence": 0.0, "consensus_reached": False, "agreement": 0.0}

    clusters: List[List] = []
    for v, s in parsed:
        hit = next((c for c in clusters if abs(v - c[0]) <= 1e-6 * max(abs(c[0]), 1.0)), None)
        if hit:
            hit[2] += 1
        else:
            clusters.append([v, s, 1])
    clusters.sort(key=lambda c: (-c[2], c[0]))
    winner_v, winner_s, winner_n = clusters[0]

    hist = []
    if winner_n >= 2:
        result = f"答案：{winner_s}"
        conf = winner_n / len(parsed)
    else:
        # 无共识 → 复核：一位 agent 重新计算并核对所有候选
        cands = "、".join(c[1] for c in clusters[:3])
        verify = _chat(f"{task}\n\n多位 agent 给出了不同结果：{cands}。请重新严格计算，"
                       f"确认哪个正确，最后单独一行以「答案：<结果>」输出。", temperature=0.0)
        hist.append({"sender": "verifier", "content": verify, "kind": "verdict"})
        pv = _parse_exact(verify)
        result = f"答案：{pv[1]}" if pv else f"答案：{winner_s}"
        conf = 0.5

    hist.append({"sender": "controller",
                 "content": f"computation 众数={winner_s} n={winner_n}", "kind": "synthesis"})
    return {"final_result": result, "mechanism": "centralized.computation",
            "confidence": conf, "consensus_reached": conf >= 0.5, "agreement": conf,
            "history": hist}


def _dev_node(state: CollabState) -> Dict[str, Any]:
    """开发节点：无草稿时初写，有草稿时按评审意见修复。"""
    task = state["task"]
    if not state.get("draft"):
        code = _chat(f"{task}\n\n只输出 Python 代码（含必要注释），不要解释。\n"
                     f"务必严格满足任务中的每一条要求（命名、约束、边界、禁用项等）；"
                     f"不要硬编码示例数据，应通过函数参数接收输入；"
                     f"不要添加任务未要求的功能（如忽略大小写、忽略标点/非字母数字等），"
                     f"严格按任务定义实现，必须给出完整可运行的实现（禁止 NotImplementedError 占位）。",
                     temperature=0.1)
        return {"draft": code, "just_fixed": False, "round": 0,
                "history": [{"sender": "开发", "content": code, "kind": "proposal"}]}
    else:
        review = state.get("review", "")
        code = _chat(f"任务：{task}\n\n请根据评审意见修复代码，输出完整可运行的 Python 代码"
                     f"（禁止 NotImplementedError 占位）。评审意见：\n{review[:800]}",
                     temperature=0.1)
        return {"draft": code, "just_fixed": True, "round": state.get("round", 0) + 1,
                "history": [{"sender": "开发", "content": code, "kind": "synthesis"}]}


def _review_node(state: CollabState) -> Dict[str, Any]:
    """评审节点：逐条核对代码是否满足任务要求。"""
    task = state["task"]
    code = state.get("draft", "")
    review = _chat(f"任务：{task}\n\n以下是待审代码。请逐条核对代码是否满足任务的每项要求"
                   f"（命名、约束、边界、禁用项等），指出任何 bug、遗漏或不满足约束之处"
                   f"（无问题写『通过』）。"
                   f"注意：不要提出任务未要求的功能建议（如忽略大小写/标点等），"
                   f"只针对任务本身的要求核对：\n\n{code[:2500]}",
                   temperature=T_VERIFY)
    passed = "通过" in review and "bug" not in review and "遗漏" not in review
    return {"review": review, "review_passed": passed,
            "history": [{"sender": "评审", "content": review, "kind": "critique"}]}


def _code_finalize(state: CollabState) -> Dict[str, Any]:
    """定稿节点：可选真实执行反馈闭环，剥围栏后输出。"""
    code = state.get("draft", "")
    test_cases = state.get("test_cases") or []
    hist = []
    conf = 0.8
    if test_cases:
        passed, total = _execute(code, test_cases)
        hist.append({"sender": "runner", "content": f"实测 {passed}/{total}", "kind": "verdict"})
        for _ in range(2):
            if passed == total:
                break
            code = _chat(f"代码实测只通过 {passed}/{total} 用例，请修复：\n\n{code[:2000]}",
                         temperature=0.1)
            passed, total = _execute(code, test_cases)
        conf = passed / total if total else 0.8

    code = _extract_python(code)
    hist.append({"sender": "controller", "content": "代码完成", "kind": "verdict"})
    return {"final_result": f"```python\n{code}\n```", "mechanism": "centralized.pipeline",
            "confidence": conf, "consensus_reached": conf >= 0.5, "agreement": conf,
            "history": hist}


def _central_route(state: CollabState) -> str:
    return "dev" if state.get("task_type") == "coding" else "fanout"


def _route_after_review(state: CollabState) -> str:
    if state.get("review_passed") or state.get("round", 0) >= 2:
        return "code_finalize"
    return "dev"


def _route_after_dev(state: CollabState) -> str:
    if not state.get("just_fixed"):
        return "review"
    return "review" if state.get("round", 0) < 2 else "code_finalize"


def build_centralized():
    """中心化拓扑图：非代码走 fanout→aggregate，代码走 dev⇄review 循环。"""
    g = StateGraph(CollabState)
    g.add_node("fanout", _central_fanout)
    g.add_node("aggregate", _central_aggregate)
    g.add_node("dev", _dev_node)
    g.add_node("review", _review_node)
    g.add_node("code_finalize", _code_finalize)

    g.add_conditional_edges(START, _central_route, ["fanout", "dev"])
    g.add_edge("fanout", "aggregate")
    g.add_edge("aggregate", END)
    g.add_edge("dev", "review")
    g.add_conditional_edges("review", _route_after_review, ["dev", "code_finalize"])
    g.add_conditional_edges("dev", _route_after_dev, ["review", "code_finalize"])
    g.add_edge("code_finalize", END)
    return g.compile()


# ═══════════════════════════════════════════════════════════════
# 模式二：去中心化协作（对等 / 黑板拓扑）
# ═══════════════════════════════════════════════════════════════

def _dec_route(state: CollabState) -> str:
    tt = state.get("task_type")
    if tt == "decision":
        return "debate"
    if tt == "translation":
        return "translate"
    if tt in ("choice", "estimation"):
        return "roundtable"
    return "refine"


def _dec_roundtable(state: CollabState) -> Dict[str, Any]:
    """圆桌：peer 独立作答 → 互阅黑板 → 修正 → 聚合（无中心权威）。"""
    task = state["task"]
    tt = state["task_type"]
    n = state.get("n_agents", 4)
    models = state.get("models")
    peers = make_agents(DIVERSE_ROLES, n, models, temperature=T_CHOICE)
    is_numeric = tt == "estimation"

    if is_numeric:
        q = lambda: f"{task}\n\n给出你的估计值（只输出一个数字）和一句话推算。"  # noqa: E731
    else:
        q = lambda: f"{task}\n\n简要推理（2-3句，小心直觉陷阱），最后严格以「答案：X」输出选项字母。"  # noqa: E731

    first = _parallel_chat([q() for _ in peers], models=[p.model for p in peers],
                           temperatures=[p.temperature for p in peers])
    hist = [{"sender": p.name, "content": r, "kind": "proposal"} for p, r in zip(peers, first)]

    transcript = "\n\n".join(f"[{p.name}]\n{r[:2000]}" for p, r in zip(peers, first))
    if is_numeric:
        prompts = [f"以下是所有 peer 的独立估计与理由：\n{transcript}\n\n"
                   f"请基于此修正你的估计，只输出一个数字。" for _ in peers]
    else:
        prompts = [f"以下是所有 peer 的答案与理由：\n{transcript}\n\n"
                   f"请复核并吸收正确之处，最后严格以「答案：X」输出你的最终选择。" for _ in peers]
    revised = _parallel_chat(prompts, models=[p.model for p in peers],
                             temperatures=[T_VERIFY] * len(peers))
    hist += [{"sender": p.name, "content": r, "kind": "critique"} for p, r in zip(peers, revised)]

    if is_numeric:
        vals = [v for v in (_extract_number(r) for r in revised) if v is not None]
        if not vals:
            vals = [v for v in (_extract_number(r) for r in first) if v is not None]
        if not vals:
            return {"final_result": "N/A", "mechanism": "decentralized.roundtable",
                    "confidence": 0.0, "consensus_reached": False, "agreement": 0.0,
                    "history": hist}
        med, _, conf, agree = _median_aggregate(vals)
        return {"final_result": f"{med:.4g}", "mechanism": "decentralized.roundtable",
                "confidence": conf, "consensus_reached": conf >= 0.5, "agreement": agree,
                "history": hist}
    else:
        winner, agree = _letter_aggregate(revised)
        if winner is None:
            winner, agree = _letter_aggregate(first)
        if winner is None:
            winner, agree = _semantic_majority(task, revised)
        return {"final_result": f"答案：{winner}", "mechanism": "decentralized.roundtable",
                "confidence": agree, "consensus_reached": agree >= 0.5, "agreement": agree,
                "history": hist}


def _dec_refine(state: CollabState) -> Dict[str, Any]:
    """打磨：草稿 → 多批评者并行挑缺陷 → 作者只修被指出的问题。"""
    task = state["task"]
    n = state.get("n_agents", 4)
    models = state.get("models")
    critics = make_agents(CRITIC_ROLES, min(n, 2), models, temperature=T_REFINE)
    writer = critics[0]
    draft = writer.chat(f"{task}\n\n请直接给出完整成品，务必满足任务中的每一项明确要求。",
                        temperature=T_DRAFT)
    hist = [{"sender": writer.name, "content": draft, "kind": "proposal"}]

    for _ in range(1):
        prompts = [f"{c.role}\n\n任务：{task}\n\n以下是当前成品。请对照任务的每项明确要求，"
                   f"指出具体的缺陷与遗漏（完全合格才写『通过』）：\n\n{draft[:2000]}" for c in critics]
        reviews = _parallel_chat(prompts, models=[c.model for c in critics],
                                 temperatures=[T_REFINE] * len(critics))
        hist += [{"sender": c.name, "content": r, "kind": "critique"} for c, r in zip(critics, reviews)]
        if reviews and all(("通过" in r) for r in reviews):
            break
        fb = "\n".join(f"[{c.name}]: {r}" for c, r in zip(critics, reviews))
        draft = writer.chat(f"针对以下批评【只修复被指出的问题，保持其余内容不变】，输出完整成品：\n\n"
                            f"任务：{task}\n当前版本：\n{draft[:1200]}\n\n批评意见：\n{fb[:2000]}",
                            temperature=T_SYNTH)
        hist.append({"sender": writer.name, "content": draft, "kind": "synthesis"})

    q = _quality(draft, task)
    hist.append({"sender": "peer-group", "content": "打磨完成", "kind": "verdict"})
    return {"final_result": draft, "mechanism": "decentralized.refine",
            "confidence": q, "consensus_reached": q >= 0.5, "agreement": q, "history": hist}


def _dec_debate(state: CollabState) -> Dict[str, Any]:
    """辩论：正反双方对抗 + 中立综合（保留双方论据 + 给出结论）。"""
    task = state["task"]
    models = state.get("models")
    pro_model = models[0] if models else None
    con_model = models[1 % len(models)] if models else None
    pro = Agent("正方", "你负责论证提案的收益、可行性与成立条件。", model=pro_model, temperature=T_DEBATE)
    con = Agent("反方", "你负责论证提案的风险、代价与反例。", model=con_model, temperature=T_DEBATE)

    opening = [f"你站在【正方】立场。辩题：{task}\n给出核心立场，并列出 2-3 条互不重复的论据（每条一句话）。",
               f"你站在【反方】立场。辩题：{task}\n给出核心立场，并列出 2-3 条互不重复的论据（每条一句话）。"]
    pro_arg, con_arg = _parallel_chat(opening, models=[pro.model, con.model],
                                      temperatures=[pro.temperature, con.temperature])
    hist = [{"sender": pro.name, "content": pro_arg, "kind": "proposal"},
            {"sender": con.name, "content": con_arg, "kind": "proposal"}]

    pro_arg = pro.chat(f"你是【正方】，直接反驳反方：\n{con_arg}\n\n重申并强化立场（150字内）。")
    con_arg = con.chat(f"你是【反方】，直接反驳正方：\n{pro_arg}\n\n重申并强化立场（150字内）。")
    hist += [{"sender": pro.name, "content": pro_arg, "kind": "critique"},
             {"sender": con.name, "content": con_arg, "kind": "critique"}]

    verdict = _chat(f"你是中立综合者。整合正反双方论据，给出平衡、完整的综合结论。\n\n"
                    f"辩题：{task}\n\n【正方】\n{pro_arg[:800]}\n\n【反方】\n{con_arg[:800]}\n\n"
                    f"输出格式：\n正方论据：<逐条列出正方核心论据>\n反方论据：<逐条列出反方核心论据>\n综合结论：<一句话>",
                    temperature=T_SYNTH)
    hist.append({"sender": "judge", "content": verdict, "kind": "verdict"})
    return {"final_result": f"综合：\n{verdict}", "mechanism": "decentralized.debate",
            "confidence": 0.6, "consensus_reached": True, "agreement": 0.6, "history": hist}


def _dec_translate(state: CollabState) -> Dict[str, Any]:
    """翻译协作：初译 → 忠实度/文化校对 → 定稿（轻量 4 次调用）。"""
    task = state["task"]
    models = state.get("models")
    translator = Agent("译者", "你是专业译者，翻译准确、地道、符合目标语言习惯。",
                       model=models[0] if models else None, temperature=0.2)
    proofreader = Agent("校对", "你是资深校对，检查译文的忠实度、地道性与文化适配。",
                        model=models[1 % len(models)] if models else None, temperature=T_VERIFY)

    draft = translator.chat(f"{task}\n\n请完整完成任务：\n"
                            f"- 若任务提供了具体源文本，请翻译，并按任务要求一并说明隐喻/双关/术语/本地化等处理；\n"
                            f"- 若任务只描述了内容类型而未给出具体源文本（例如只说「一段含幽默与地域梗的英文内容」），"
                            f"请不要拒绝、不要索取原文，而是直接给出该场景下的翻译/本地化【策略与注意事项】，"
                            f"逐条覆盖任务要求的每一点（识别禁忌/替换/替代方案/理由等）。",
                            temperature=0.2)
    hist = [{"sender": translator.name, "content": draft, "kind": "proposal"}]

    review = proofreader.chat(f"检查以下回答是否完整：若任务提供了源文本，译文是否忠实、地道；"
                              f"若任务未提供源文本，是否已直接给出完整策略（而非拒绝/索取原文）。"
                              f"并核对是否满足了任务要求的每一项（无问题写『通过』）：\n\n{draft[:1500]}",
                              temperature=T_VERIFY)
    hist.append({"sender": proofreader.name, "content": review, "kind": "critique"})

    if "通过" not in review or "问题" in review or "错误" in review:
        draft = translator.chat(f"根据校对意见修改，输出完整最终答案（译文或策略 + 任务要求的说明）：\n\n"
                                f"校对意见：\n{review[:800]}\n\n原始任务：\n{task}\n\n最终答案：",
                                temperature=0.2)
        hist.append({"sender": translator.name, "content": draft, "kind": "synthesis"})

    q = _quality(draft, task)
    hist.append({"sender": "peer-group", "content": "翻译定稿", "kind": "verdict"})
    return {"final_result": draft, "mechanism": "decentralized.translate",
            "confidence": q, "consensus_reached": q >= 0.5, "agreement": q, "history": hist}


def build_decentralized():
    """去中心化拓扑图：按 task_type 路由到四种 peer 协作机制。"""
    g = StateGraph(CollabState)
    g.add_node("roundtable", _dec_roundtable)
    g.add_node("refine", _dec_refine)
    g.add_node("debate", _dec_debate)
    g.add_node("translate", _dec_translate)

    g.add_conditional_edges(START, _dec_route, ["roundtable", "refine", "debate", "translate"])
    for n in ("roundtable", "refine", "debate", "translate"):
        g.add_edge(n, END)
    return g.compile()


# ═══════════════════════════════════════════════════════════════
# 模式三：分层架构（树型拓扑）
# ═══════════════════════════════════════════════════════════════

def _hier_route(state: CollabState) -> str:
    return "group_vote" if state.get("task_type") in ("choice", "estimation") else "analysis"


def _hier_analysis(state: CollabState) -> Dict[str, Any]:
    """并行多视角直接作答 → 仲裁综合（并集去重 + 条目齐全，避免拆解漂移）。"""
    task = state["task"]
    n = max(2, state.get("n_groups", 3))
    models = state.get("models")
    agents = make_agents(DIVERSE_ROLES, n, models, temperature=T_DRAFT)
    prompts = [f"{a.role}\n\n任务：{task}\n\n请直接、完整地回答该任务：逐条覆盖每一项明确要求"
               f"（指令性动作、必要维度、数量、量化指标等），每条简洁（1-2 句），"
               f"不要遗漏、也不要冗长。" for a in agents]
    drafts = _parallel_chat(prompts, models=[a.model for a in agents],
                            temperatures=[a.temperature for a in agents])
    hist = [{"sender": a.name, "content": d, "kind": "proposal"} for a, d in zip(agents, drafts)]

    final = _synthesize_union(task, [(a.name, d) for a, d in zip(agents, drafts)])
    hist.append({"sender": "arbiter", "content": final, "kind": "verdict"})
    q = _quality(final, task)
    return {"final_result": final, "mechanism": "hierarchical.analysis",
            "confidence": q, "consensus_reached": q >= 0.5, "agreement": q, "history": hist}


def _hier_group_vote(state: CollabState) -> Dict[str, Any]:
    """分层投票：分组 → 组内独立作答 → 代表汇总 → 根聚合。"""
    task = state["task"]
    tt = state["task_type"]
    n_groups = state.get("n_groups", 3)
    per_group = state.get("agents_per_group", 3)
    models = state.get("models")
    is_numeric = tt == "estimation"

    n_agents = n_groups * per_group
    all_agents = make_agents(DIVERSE_ROLES, n_agents, models, temperature=T_CHOICE)
    groups = [all_agents[i * per_group:(i + 1) * per_group] for i in range(n_groups)]
    hist = []

    members_by_group = []
    for gi, grp in enumerate(groups):
        if is_numeric:
            prompts = [f"{a.role}\n\n{task}\n\n给出你的估计值（只输出一个数字）和一句话推算。" for a in grp]
        else:
            prompts = [f"{a.role}\n\n{task}\n\n简要推理（2-3句，小心直觉陷阱），"
                       f"最后严格以「答案：X」输出选项字母。" for a in grp]
        members = _parallel_chat(prompts, models=[a.model for a in grp],
                                 temperatures=[a.temperature for a in grp])
        hist += [{"sender": a.name, "content": r, "kind": "proposal"} for a, r in zip(grp, members)]
        members_by_group.append(members)

    rep_prompts = []
    for gi, members in enumerate(members_by_group):
        p = f"以下是第{gi+1}组 {len(members)} 位成员对同一问题的答案，请代表小组给出统一结论。\n\n"
        p += "\n".join(f"[成员{i+1}] {m[:300]}" for i, m in enumerate(members))
        p += "\n\n只输出小组的统一估计数字。" if is_numeric else \
             "\n\n最后严格以「答案：X」输出小组统一选择。"
        rep_prompts.append(p)
    group_answers = _parallel_chat(rep_prompts, temperatures=[T_VERIFY] * len(rep_prompts))
    hist += [{"sender": f"group{gi+1}-代表", "content": rep, "kind": "synthesis"}
             for gi, rep in enumerate(group_answers)]

    if is_numeric:
        vals = [v for v in (_extract_number(r) for r in group_answers) if v is not None]
        if not vals:
            return {"final_result": "N/A", "mechanism": "hierarchical.group_vote",
                    "confidence": 0.0, "consensus_reached": False, "agreement": 0.0, "history": hist}
        med, _, conf, agree = _median_aggregate(vals)
        hist.append({"sender": "root", "content": f"各组中位数 {med:.2f}", "kind": "verdict"})
        return {"final_result": f"{med:.4g}", "mechanism": "hierarchical.group_vote",
                "confidence": conf, "consensus_reached": conf >= 0.5, "agreement": agree,
                "history": hist}
    else:
        winner, agree = _letter_aggregate(group_answers)
        if winner is None:
            r = _chat(f"以下是{len(group_answers)}个小组代表结论，提炼多数派并估计一致比例(0-1)。\n"
                      + "\n".join(group_answers) + "\n格式：\n结论：<选项字母>\n一致比例：<0-1>",
                      temperature=T_SYNTH)
            hist.append({"sender": "judge", "content": r, "kind": "verdict"})
            m = re.search(r"结论[：:]\s*([A-Da-d])", r)
            winner = m.group(1).upper() if m else "?"
            agree = _ratio(r)
        hist.append({"sender": "root", "content": f"最终选择 {winner}", "kind": "verdict"})
        return {"final_result": f"答案：{winner}", "mechanism": "hierarchical.group_vote",
                "confidence": agree, "consensus_reached": agree >= 0.5, "agreement": agree,
                "history": hist}


def build_hierarchical():
    """分层拓扑图：按 task_type 路由到 analysis 或 group_vote。"""
    g = StateGraph(CollabState)
    g.add_node("analysis", _hier_analysis)
    g.add_node("group_vote", _hier_group_vote)
    g.add_conditional_edges(START, _hier_route, ["analysis", "group_vote"])
    g.add_edge("analysis", END)
    g.add_edge("group_vote", END)
    return g.compile()


# ═══════════════════════════════════════════════════════════════
# 模式零：单 Agent 基线（对照组，无共识机制）
# ═══════════════════════════════════════════════════════════════

def _single_node(state: CollabState) -> Dict[str, Any]:
    task = state["task"]
    models = state.get("models")
    model = models[0] if models else None
    ans = _chat(f"请回答以下问题，仔细思考后给出最佳答案。\n\n{task}\n\n"
                f"请直接、简洁地给出答案与必要推理（要点式，不要长篇）。",
                model=model, temperature=0.3)
    return {"final_result": ans, "mechanism": "single.baseline",
            "confidence": 0.5, "consensus_reached": True, "agreement": 1.0,
            "history": [{"sender": "single", "content": ans, "kind": "proposal"}]}


def build_single():
    g = StateGraph(CollabState)
    g.add_node("answer", _single_node)
    g.add_edge(START, "answer")
    g.add_edge("answer", END)
    return g.compile()


# ═══════════════════════════════════════════════════════════════
# 模式：单 Agent 自洽基线（公平对照组）
# ═══════════════════════════════════════════════════════════════
# 同一 agent（无角色差异）N 次独立采样 → 与 multi 相同的聚合。用于区分
# 「多采样带来的自洽增益」与「真正的多 Agent 协作增益」。

def _sc_prompt(task: str, tt: str) -> str:
    if tt == "computation":
        return f"{task}\n\n请严格、逐步计算，给出精确结果（最简分数或小数）。最后单独一行以「答案：<结果>」输出。"
    if tt == "estimation":
        return f"{task}\n\n给出你的估计值（只输出一个数字）和一句话自底向上推算。"
    if tt == "choice":
        return f"{task}\n\n简要推理（2-3句，小心直觉陷阱），最后严格以「答案：X」输出选项字母。"
    return f"请回答以下问题，仔细思考后给出最佳答案。\n\n{task}\n\n请直接、完整地回答：逐条覆盖每一项明确要求（指令性动作、必要维度、数量、量化指标等），每条简洁（1-2 句），不要遗漏、也不要冗长。"


def _sc_node(state: CollabState) -> Dict[str, Any]:
    task = state["task"]
    tt = state["task_type"]
    n = state.get("n_agents", 5)
    models = state.get("models")
    model = models[0] if models else None

    prompts = [_sc_prompt(task, tt) for _ in range(n)]
    answers = _parallel_chat(prompts, models=[model] * n, temperatures=[T_CHOICE] * n)
    hist = [{"sender": f"sample{i+1}", "content": r, "kind": "proposal"} for i, r in enumerate(answers)]

    # 聚合（与 multi 同一套解析/投票，仅去掉角色差异）
    if tt == "computation":
        parsed = [p for p in (_parse_exact(r) for r in answers) if p is not None]
        if not parsed:
            return {"final_result": "N/A", "mechanism": "selfconsistency",
                    "confidence": 0.0, "consensus_reached": False, "agreement": 0.0, "history": hist}
        clusters: List[List] = []
        for v, s in parsed:
            hit = next((c for c in clusters if abs(v - c[0]) <= 1e-6 * max(abs(c[0]), 1.0)), None)
            if hit:
                hit[2] += 1
            else:
                clusters.append([v, s, 1])
        clusters.sort(key=lambda c: (-c[2], c[0]))
        winner_s, winner_n = clusters[0][1], clusters[0][2]
        conf = winner_n / len(parsed)
        return {"final_result": f"答案：{winner_s}", "mechanism": "selfconsistency",
                "confidence": conf, "consensus_reached": conf >= 0.5, "agreement": conf,
                "history": hist}

    if tt == "estimation":
        vals = [v for v in (_extract_number(r) for r in answers) if v is not None]
        if not vals:
            return {"final_result": "N/A", "mechanism": "selfconsistency",
                    "confidence": 0.0, "consensus_reached": False, "agreement": 0.0, "history": hist}
        med, _, conf, agree = _median_aggregate(vals)
        return {"final_result": f"{med:.4g}", "mechanism": "selfconsistency",
                "confidence": conf, "consensus_reached": conf >= 0.5, "agreement": agree,
                "history": hist}

    if tt == "choice":
        winner, agree = _letter_aggregate(answers)
        if winner is None:
            winner, agree = _semantic_majority(task, answers)
        return {"final_result": f"答案：{winner}", "mechanism": "selfconsistency",
                "confidence": agree, "consensus_reached": agree >= 0.5, "agreement": agree,
                "history": hist}

    # 开放式题（analysis/creative/translation/coding…）：N 次独立采样 → 用与 multi 相同的
    # 「并集去重 + 条目齐全」综合，而非一句话提炼——否则会稀释要点，导致基线失真偏低。
    winner = _synthesize_union(task, [(f"sample{i+1}", r) for i, r in enumerate(answers)])
    q = _quality(winner, task)
    return {"final_result": winner, "mechanism": "selfconsistency",
            "confidence": q, "consensus_reached": q >= 0.5, "agreement": q,
            "history": hist}


def build_selfconsistency():
    g = StateGraph(CollabState)
    g.add_node("samples", _sc_node)
    g.add_edge(START, "samples")
    g.add_edge("samples", END)
    return g.compile()


# ═══════════════════════════════════════════════════════════════
# 顶层调度与自动分类
# ═══════════════════════════════════════════════════════════════

_BUILDERS = {
    "single": build_single,
    "selfconsistency": build_selfconsistency,
    "centralized": build_centralized,
    "decentralized": build_decentralized,
    "hierarchical": build_hierarchical,
}

_GRAPHS: Dict[str, Any] = {}


def _get_graph(mode: str):
    """惰性构建 + 缓存编译图（图结构固定，配置经 state 传入）。"""
    if mode not in _GRAPHS:
        _GRAPHS[mode] = _BUILDERS[mode]()
    return _GRAPHS[mode]


# 题型 → 默认模式（benchmark 可自行指定 task_type 覆盖）
TYPE_TO_MODE = {
    "choice": "centralized", "estimation": "centralized", "coding": "centralized",
    "creative": "decentralized", "decision": "decentralized",
    "translation": "decentralized",
    "analysis": "hierarchical",
}


def run(mode: str, task: str, task_type: Optional[str] = None,
        test_cases: Optional[List[dict]] = None, **kwargs) -> Dict[str, Any]:
    """顶层调度：mode ∈ single / selfconsistency / centralized / decentralized / hierarchical。

    返回 dict 契约与旧版 `collab_patterns(2).py` 完全一致。
    """
    if mode not in _BUILDERS:
        raise ValueError(f"未知模式 '{mode}'，可用：{list(_BUILDERS)}")

    tt = (task_type or _autodetect(task)).lower()
    graph = _get_graph(mode)
    initial: CollabState = {
        "task": task,
        "task_type": tt,
        "test_cases": test_cases or [],
        "n_agents": kwargs.get("n_agents", 5),
        "n_groups": kwargs.get("n_groups", 3),
        "agents_per_group": kwargs.get("agents_per_group", 3),
        "models": kwargs.get("models"),
        "history": [],
    }

    start = time.time()
    token = _current_stats.set(_RunStats())
    try:
        final = graph.invoke(initial)
        return _assemble(final, start, kwargs.get("models"))
    except Exception as e:  # noqa: BLE001
        return _error_result(e, mode)
    finally:
        _current_stats.reset(token)


def solve_auto(task: str, n_agents: int = 5, models: Optional[List[str]] = None,
               test_cases: Optional[List[dict]] = None) -> Dict[str, Any]:
    """按 task_type 自动选择最合适的模式。"""
    tt = _autodetect(task)
    mode = TYPE_TO_MODE.get(tt, "hierarchical")
    if mode == "hierarchical":
        n_groups = min(3, max(1, n_agents))
        agents_per_group = max(1, math.ceil(n_agents / n_groups))
        return run(mode, task, task_type=tt, test_cases=test_cases,
                   n_groups=n_groups, agents_per_group=agents_per_group, models=models)
    return run(mode, task, task_type=tt, test_cases=test_cases,
               n_agents=n_agents, models=models)


def _autodetect(task: str) -> str:
    kw = {
        "estimation": ["估计", "预测", "多少", "哪一年", "市场规模", "销量", "增长率"],
        "coding": ["编写", "函数", "代码", "Python函数", "实现"],
        "translation": ["翻译", "译成", "本地化", "译文"],
        "creative": ["写", "创作", "文案", "故事", "诗", "Slogan"],
        "decision": ["是否", "该不该", "要不要", "应不应该", "支持还是反对"],
        "choice": ["A.", "B.", "C.", "以下哪个", "选出"],
    }
    hits = {t: sum(1 for k in ks if k in task) for t, ks in kw.items()}
    top = max(hits, key=hits.get)
    return top if hits[top] > 0 else "analysis"


# ═══════════════════════════════════════════════════════════════
# 工具：代码真实执行（coding 自检，可选）
# ═══════════════════════════════════════════════════════════════

def _extract_python(text: str) -> str:
    m = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
    return (m.group(1) if m else text).strip()


def _execute(code: str, test_cases: List[dict]) -> Tuple[int, int]:
    """真实执行代码并跑测试用例。返回 (passed, total)。"""
    code = _extract_python(code)
    code = re.sub(r':\s*(?:List|Dict|Tuple|Optional|Union)\[.*?\]', '', code)
    code = re.sub(r'(from typing import.*\n|import typing.*\n)', '', code)
    if "def " not in code:
        return 0, len(test_cases)
    passed = 0
    reserved = {"List", "Dict", "Tuple", "Optional", "Union"}
    for tc in test_cases:
        try:
            ns = {"List": list, "Dict": dict, "Tuple": tuple}
            exec(code, ns)
            func = next((o for n, o in ns.items()
                         if callable(o) and n not in reserved and not n.startswith("_")), None)
            if func is None:
                return 0, len(test_cases)
            inp, exp = tc["input"], tc["expected"]
            got = func(**inp) if isinstance(inp, dict) else func(inp)
            if isinstance(got, list) and isinstance(exp, list):
                if sorted(got) == sorted(exp):
                    passed += 1
            elif got == exp:
                passed += 1
        except Exception:  # noqa: BLE001
            pass
    return passed, len(test_cases)
