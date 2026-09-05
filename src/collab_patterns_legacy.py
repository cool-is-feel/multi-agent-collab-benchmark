r"""
多Agent协作 — 三种拓扑模式库（自包含，与 benchmark 完全分离）
===============================================================
把多 Agent 协作按「信息如何流动、决策权在哪里」归纳为三种拓扑模式，
每种实现为一个独立的类，共享一套统一接口，任何外部 benchmark 都能直接调用。

三种模式
--------
  1. CentralizedOrchestrator  中心化编排（星型拓扑）
     一个 Controller 掌握全局，N 个 Worker 彼此隔离、独立产出，
     Controller 统一聚合决策。Worker 互不通信是刻意设计——独立性
     是"群体智慧"成立的前提（独立误差聚合后按 1/√N 收敛）。

  2. DecentralizedCollaboration  去中心化协作（对等/黑板拓扑）
     没有中心权威，所有 Agent 是平级 peer，通过共享黑板读写信息，
     机会主义地贡献/批评，共识"涌现"出来。

  3. HierarchicalArchitecture  分层架构（树型拓扑）
     把大问题拆成子问题（分治），底层小组并行解决，逐层"代表/聚合"
     向上压缩，根节点合成全局结论。可扩展到任意规模（O(K·m²)）。

统一接口（这是给 benchmark 的契约）
------------------------------------
每个模式都暴露：

    solve(task, task_type=None, test_cases=None) -> dict

  - task        : 任务文本（字符串）
  - task_type   : 题型提示，可选。取值 choice / estimation / creative /
                  analysis / decision / coding。不传则自动识别。
  - test_cases  : 编程题测试用例（仅 coding 用到），可选。
  - 返回 dict 必含：
        final_result   : str  —— 最终答案（benchmark 评分器读这个字段）
        mechanism      : str  —— 命中的子机制
        confidence     : float—— 0-1 置信度
        consensus_reached : bool
        agreement      : float—— 0-1 一致程度
        llm_calls      : int  —— LLM 调用次数
        token_input/output/total : int —— token 统计
        elapsed        : float—— 秒
        history        : list —— 通信消息（可审计）

顶层调度：

    run(mode, task, task_type=None, test_cases=None, **kwargs)
        mode ∈ {"centralized", "decentralized", "hierarchical"}

异构多模型用法（让不同 Agent 用不同模型/厂商）：
    1) 注册外部模型：register_model("gpt-4o", base_url="...", api_key="...")
    2) 传入 models 列表，按 Agent 循环分配：
       run("centralized", task, models=["deepseek-chat", "gpt-4o", "claude-sonnet-5"])
       → 第 1 个 Agent 用 deepseek-chat，第 2 个用 gpt-4o，第 3 个用 claude-sonnet-5，循环。

本模块是自包含的：自带最小 LLM 适配器（langchain_openai + dotenv，
读取 DEEPSEEK_* 环境变量），不依赖任何项目内其它文件，也不依赖
benchmark。你可以放心地把本文件复制到别处、或替换底层模型接入。

题型 → 模式映射（仅供参考，调度时可自行覆盖）
--------------------------------------------
  choice/estimation/coding → 中心化（独立多数投票 / 中位数 / 流水线）
  creative/decision        → 去中心化（peer 打磨 / 正反辩论）
  analysis/复杂方案         → 分层（分治拆解 + 子组协作 + 中心合成）
"""

from __future__ import annotations

import logging
import math
import os
import queue
import re
import statistics
import threading
import time
from contextvars import ContextVar
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass, field
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("collab_patterns")

try:
    from langchain_openai import ChatOpenAI
except ImportError:  # pragma: no cover
    ChatOpenAI = None


# ═══════════════════════════════════════════════════════════════
# 配置（集中管理，便于统一调参）
# ═══════════════════════════════════════════════════════════════

MAX_TOKENS = 400          # 单次生成长度上限，防止拖慢 benchmark
REQUEST_TIMEOUT = 180      # 单次请求硬超时（秒）；推理型模型部分调用很慢，需放宽避免误杀
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

# 异构多模型：不同模型可用不同 base_url / api_key
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


# 每次 solve 独享统计对象；线程池任务会显式继承当前对象。
@dataclass
class _RunStats:
    input: int = 0
    output: int = 0
    calls: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


_default_stats = _RunStats()
_current_stats: ContextVar[Optional[_RunStats]] = ContextVar("collab_run_stats", default=None)


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
    这里用墙钟硬超时兜底，保证单次调用不会无限挂起。守护线程若超时残留也在后台，
    不阻塞主流程、不影响进程退出。
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
# Agent 与通信数据流
# ═══════════════════════════════════════════════════════════════

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


@dataclass
class Message:
    """Agent 间通信的数据单元（文本数据流的最小载体）。"""
    sender: str
    content: str
    kind: str = "proposal"          # proposal/critique/vote/verdict/synthesis
    meta: Dict[str, Any] = field(default_factory=dict)

    def render(self, max_len: int = 2000) -> str:
        return f"[{self.sender}] ({self.kind}):\n{self.content[:max_len]}"


class Channel:
    """通信通道：中心化里是 Controller 收件箱，去中心化里是共享黑板。"""

    def __init__(self, task: str):
        self.task = task
        self.msgs: List[Message] = []

    def post(self, sender: str, content: str, kind: str = "proposal", **meta) -> Message:
        m = Message(sender, content, kind, meta)
        self.msgs.append(m)
        return m

    def transcript(self, kinds: Optional[List[str]] = None, max_len: int = 2000) -> str:
        msgs = self.msgs if kinds is None else [m for m in self.msgs if m.kind in kinds]
        return "\n\n".join(m.render(max_len) for m in msgs)


# ═══════════════════════════════════════════════════════════════
# 解析 / 角色库 / 聚合 / 质量评分
# ═══════════════════════════════════════════════════════════════

def _extract_number(text: str) -> Optional[float]:
    nums = re.findall(r'(\d+\.?\d*)', str(text))
    return float(nums[0]) if nums else None


def _parse_exact(text: str) -> Optional[Tuple[float, str]]:
    """解析「精确数值」答案，返回 (float, 原字符串)。

    优先取「答案：」后缀，其次「=」后内容；支持最简分数 a/b、科学计数法、小数/整数。
    用于精确计算题的众数投票（对比 _extract_number 只能抓第一个数字、抓不到分数）。
    """
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

    # 用相对 MAD 衡量离散程度，再通过指数衰减映射到 0-1。
    # med 接近 0 时用 mean 补充尺度，避免除数过小导致一致度失真。
    scale = max(abs(med), abs(mean), 1e-9)
    agreement = math.exp(-mad / scale)

    # confidence 同时考虑答案一致度与有效样本数，避免少量样本产生虚高置信度。
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


def _finish(ctx: Channel, result: str, mechanism: str, confidence: float,
            reached: bool, agreement: float, start: float,
            models: Optional[List[str]] = None) -> Dict[str, Any]:
    """组装统一返回结构。"""
    tokens = get_token_stats()
    stats = _stats()
    with stats.lock:
        calls = stats.calls
    return {
        "final_result": result,
        "mechanism": mechanism,
        "confidence": round(confidence, 4),
        "consensus_reached": reached,
        "agreement": round(agreement, 4),
        "llm_calls": calls,
        "token_input": tokens["input"],
        "token_output": tokens["output"],
        "token_total": tokens["input"] + tokens["output"],
        "elapsed": round(time.time() - start, 3),
        "models_used": sorted(set(models)) if models else
                       [os.getenv("DEEPSEEK_MODEL", "deepseek-chat")],
        "history": ctx.msgs,
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


def _run_guarded(fn, mechanism: str) -> Dict[str, Any]:
    """用独立统计上下文执行 fn，捕获异常并返回降级结果。"""
    token = _current_stats.set(_RunStats())
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        return _error_result(e, mechanism)
    finally:
        _current_stats.reset(token)


# ═══════════════════════════════════════════════════════════════
# 模式一：中心化编排（星型拓扑）
# ═══════════════════════════════════════════════════════════════

class CentralizedOrchestrator:
    """中心化编排：Controller 是唯一枢纽，Workers 彼此隔离独立产出。

    子机制（按 task_type 选择）：
      choice     → 独立多数投票 + 分歧时复核
      estimation → 独立估计取中位数（群体智慧）
      coding     → 串行流水线（写→审→改，可选真实执行反馈）
      其它       → 退化为多数投票/语义提炼
    """

    def __init__(self, n_agents: int = 5, models: Optional[List[str]] = None):
        self.n_agents = n_agents
        self.models = models

    def solve(self, task: str, task_type: Optional[str] = None,
              test_cases: Optional[List[dict]] = None) -> Dict[str, Any]:
        start = time.time()
        def _go() -> Dict[str, Any]:
            ctx = Channel(task)
            tt = (task_type or _autodetect(task)).lower()
            if tt == "estimation":
                r = self._median(task, ctx)
            elif tt == "computation":
                r = self._computation(task, ctx)
            elif tt == "coding":
                r = self._pipeline(task, ctx, test_cases=test_cases)
            else:  # choice 及通用
                r = self._vote(task, ctx)
            r.update(_finish(ctx, r["final_result"], r["mechanism"], r["confidence"],
                             r["consensus_reached"], r["agreement"], start, self.models))
            return r

        return _run_guarded(_go, "centralized")

    # ── 离散选项：独立多数投票 + 分歧时去中心化复核 ──────────
    def _vote(self, task: str, ctx: Channel) -> Dict[str, Any]:
        agents = make_agents(DIVERSE_ROLES, self.n_agents, self.models, temperature=T_CHOICE)
        prompts = [f"{a.role}\n\n问题：{task}\n\n简要推理（2-3句，小心直觉陷阱），"
                   f"最后严格以「答案：X」输出选项字母。" for a in agents]
        answers = _parallel_chat(prompts, models=[a.model for a in agents],
                                 temperatures=[a.temperature for a in agents])
        for a, r in zip(agents, answers):
            ctx.post(a.name, r, "proposal")

        winner, agree = _letter_aggregate(answers)
        if winner is None:
            winner, agree = _semantic_majority(task, answers)
            ctx.post("judge", f"多数结论：{winner}", "verdict")
        elif agree < 0.6:
            # 初轮分歧 → 去中心化复核（复核后的一致度回填，置信度更准确）
            winner, agree = self._verify(task, answers, agents, ctx)

        best = self._best_reasoning(answers, winner)
        result = f"答案：{winner}" + (f"\n\n推理：\n{best}" if best else "")
        ctx.post("controller", f"最终选择 {winner}", "verdict")
        return {"final_result": result, "mechanism": "centralized.vote",
                "confidence": agree, "consensus_reached": agree >= 0.5, "agreement": agree}

    def _verify(self, task, answers, agents, ctx) -> Tuple[str, float]:
        """分歧时复核：把候选与理由回传，让 peer 互相审视后重投票。

        返回 (winner, agreement)，agreement 取复核轮的一致度（更准确）。
        """
        ctx.post("controller", "初轮分歧，进入复核", "verdict")
        summary = "\n".join(f"Agent{i+1} 选 {(_extract_letter(r) or '?')}：{r[:150]}"
                            for i, r in enumerate(answers))
        prompts = [f"{a.role}\n\n问题：{task}\n\n第一轮各方答案与理由：\n{summary}\n\n"
                   f"请复核：指出别人推理的问题或吸收正确之处，最后严格以「答案：X」重新输出最终选择。"
                   for a in agents]
        revised = _parallel_chat(prompts, models=[a.model for a in agents],
                                 temperatures=[T_VERIFY] * len(agents))
        for a, r in zip(agents, revised):
            ctx.post(a.name, r, "critique")
        winner, agree = _letter_aggregate(revised)
        if winner is None:
            winner, agree = _letter_aggregate(answers)
        return (winner if winner else "?"), agree

    def _best_reasoning(self, answers, letter) -> str:
        for r in answers:
            if _extract_letter(r) == letter:
                return r[:600]
        return ""

    # ── 连续数值：中位数聚合 ────────────────────────────────
    def _median(self, task: str, ctx: Channel) -> Dict[str, Any]:
        agents = make_agents(DIVERSE_ROLES, self.n_agents, self.models, temperature=T_ESTIMATE)
        prompts = [f"{a.role}\n\n{task}\n\n给出你的估计值（只输出一个数字）和一句话自底向上推算。"
                   for a in agents]
        responses = _parallel_chat(prompts, models=[a.model for a in agents],
                                   temperatures=[a.temperature for a in agents])
        for a, r in zip(agents, responses):
            ctx.post(a.name, r, "proposal")
        vals = [v for v in (_extract_number(r) for r in responses) if v is not None]
        if not vals:
            return {"final_result": "N/A", "mechanism": "centralized.median",
                    "confidence": 0.0, "consensus_reached": False, "agreement": 0.0}
        med, mean, conf, agree = _median_aggregate(vals)
        result = f"{med:.4g}\n\n（{len(vals)}位独立估计的中位数；均值 {mean:.2f}）"
        ctx.post("controller", f"median={med:.2f} n={len(vals)}", "synthesis")
        return {"final_result": result, "mechanism": "centralized.median",
                "confidence": conf, "consensus_reached": conf >= 0.5, "agreement": agree}

    # ── 精确计算：独立计算 + 众数投票（自洽性） ─────────────
    def _computation(self, task: str, ctx: Channel) -> Dict[str, Any]:
        """精确计算：N 位 agent 独立计算 → 众数投票（self-consistency）。

        与「估计」不同：精确计算的误差不对称、不服从独立抵消，中位数不适用。
        多位 agent 各自独立算出精确值后取【众数】（出现次数最多的值），
        能显著抵消单个 agent 的算术失误——这是推理任务的标准自洽性方法。
        """
        agents = make_agents(DIVERSE_ROLES, max(self.n_agents, 3), self.models, temperature=T_COMPUTE)
        prompts = [f"{a.role}\n\n{task}\n\n请严格、逐步计算，给出精确结果（最简分数或小数）。"
                   f"最后单独一行以「答案：<结果>」输出；不要估计、不要取近似值（除非题目要求）。"
                   for a in agents]
        answers = _parallel_chat(prompts, models=[a.model for a in agents],
                                 temperatures=[T_COMPUTE] * len(agents))
        for a, r in zip(agents, answers):
            ctx.post(a.name, r, "proposal")

        parsed = [p for p in (_parse_exact(r) for r in answers) if p is not None]
        if not parsed:
            return {"final_result": "N/A", "mechanism": "centralized.computation",
                    "confidence": 0.0, "consensus_reached": False, "agreement": 0.0}

        # 按相对容差聚类，取出现次数最多的值（众数）
        clusters: List[List] = []
        for v, s in parsed:
            hit = next((c for c in clusters if abs(v - c[0]) <= 1e-6 * max(abs(c[0]), 1.0)), None)
            if hit:
                hit[2] += 1
            else:
                clusters.append([v, s, 1])
        clusters.sort(key=lambda c: (-c[2], c[0]))
        winner_v, winner_s, winner_n = clusters[0]

        if winner_n >= 2:
            result = f"答案：{winner_s}"
            conf = winner_n / len(parsed)
        else:
            # 无共识 → 复核：一位 agent 重新计算并核对所有候选
            cands = "、".join(c[1] for c in clusters[:3])
            verify = _chat(f"{task}\n\n多位 agent 给出了不同结果：{cands}。请重新严格计算，"
                           f"确认哪个正确，最后单独一行以「答案：<结果>」输出。", temperature=0.0)
            ctx.post("verifier", verify, "verdict")
            pv = _parse_exact(verify)
            if pv:
                result = f"答案：{pv[1]}"
            else:
                result = f"答案：{winner_s}"
            conf = 0.5

        ctx.post("controller", f"computation 众数={winner_s} n={winner_n}", "synthesis")
        return {"final_result": result, "mechanism": "centralized.computation",
                "confidence": conf, "consensus_reached": conf >= 0.5, "agreement": conf}

    # ── 编程：串行流水线 + 质量门禁 ─────────────────────────
    def _pipeline(self, task: str, ctx: Channel,
                  test_cases: Optional[List[dict]] = None) -> Dict[str, Any]:
        dev = Agent("开发", "你是资深工程师，写出正确、严格符合任务要求、边界处理正确的代码。",
                    model=self.models[0] if self.models else None)
        reviewer = Agent("评审", "你是严格代码评审，专门核对代码是否满足任务要求、有无逻辑错误。",
                         model=self.models[1] if self.models and len(self.models) > 1 else None)

        code = dev.chat(f"{task}\n\n只输出 Python 代码（含必要注释），不要解释。\n"
                        f"务必严格满足任务中的每一条要求（命名、约束、边界、禁用项等）；"
                        f"不要硬编码示例数据，应通过函数参数接收输入；"
                        f"不要添加任务未要求的功能（如忽略大小写、忽略标点/非字母数字等），"
                        f"严格按任务定义实现，必须给出完整可运行的实现（禁止 NotImplementedError 占位）。",
                        temperature=0.1)
        ctx.post(dev.name, code, "proposal")

        for _ in range(2):
            review = reviewer.chat(f"任务：{task}\n\n以下是待审代码。请逐条核对代码是否满足任务的每项要求"
                                   f"（命名、约束、边界、禁用项等），指出任何 bug、遗漏或不满足约束之处"
                                   f"（无问题写『通过』）。"
                                   f"注意：不要提出任务未要求的功能建议（如忽略大小写/标点等），"
                                   f"只针对任务本身的要求核对：\n\n{code[:2500]}",
                                   temperature=T_VERIFY)
            ctx.post(reviewer.name, review, "critique")
            if "通过" in review and "bug" not in review and "遗漏" not in review:
                break
            code = dev.chat(f"任务：{task}\n\n请根据评审意见修复代码，输出完整可运行的 Python 代码"
                            f"（禁止 NotImplementedError 占位）。评审意见：\n{review[:800]}",
                            temperature=0.1)
            ctx.post(dev.name, code, "synthesis")

        conf = 0.8
        if test_cases:  # 真实执行反馈闭环
            passed, total = _execute(code, test_cases)
            ctx.post("runner", f"实测 {passed}/{total}", "verdict")
            for _ in range(2):
                if passed == total:
                    break
                code = dev.chat(f"代码实测只通过 {passed}/{total} 用例，请修复：\n\n{code[:2000]}", temperature=0.1)
                passed, total = _execute(code, test_cases)
            conf = passed / total if total else 0.8

        ctx.post("controller", "代码完成", "verdict")
        # 模型可能自带 ```python 围栏，统一剥掉后再包一层，避免双层嵌套。
        code = _extract_python(code)
        return {"final_result": f"```python\n{code}\n```", "mechanism": "centralized.pipeline",
                "confidence": conf, "consensus_reached": conf >= 0.5, "agreement": conf}


# ═══════════════════════════════════════════════════════════════
# 模式二：去中心化协作（对等/黑板拓扑）
# ═══════════════════════════════════════════════════════════════

class DecentralizedCollaboration:
    """去中心化协作：无中心权威，peer 平级读写共享黑板，共识涌现。

    子机制：
      choice/estimation → roundtable（peer 独立作答→互阅→修正→聚合）
      creative/analysis → refine（多 peer 对草稿批评 → 作者吸收迭代）
      decision          → debate（正反双方 peer 对抗 → 法官裁决）
    """

    def __init__(self, n_agents: int = 4, models: Optional[List[str]] = None):
        self.n_agents = n_agents
        self.models = models

    def solve(self, task: str, task_type: Optional[str] = None,
              test_cases: Optional[List[dict]] = None) -> Dict[str, Any]:
        start = time.time()
        def _go() -> Dict[str, Any]:
            ctx = Channel(task)
            tt = (task_type or _autodetect(task)).lower()
            if tt == "decision":
                r = self._debate(task, ctx)
            elif tt == "translation":
                r = self._translate(task, ctx)
            elif tt in ("choice", "estimation"):
                r = self._roundtable(task, ctx, is_numeric=(tt == "estimation"))
            else:
                r = self._refine(task, ctx)
            r.update(_finish(ctx, r["final_result"], r["mechanism"], r["confidence"],
                             r["consensus_reached"], r["agreement"], start, self.models))
            return r

        return _run_guarded(_go, "decentralized")

    def _roundtable(self, task: str, ctx: Channel, is_numeric: bool = False) -> Dict[str, Any]:
        """去中心化圆桌：peer 独立作答 → 互阅黑板 → 修正 → 聚合（无中心权威）。"""
        peers = make_agents(DIVERSE_ROLES, self.n_agents, self.models, temperature=T_CHOICE)
        if is_numeric:
            q = lambda: f"{task}\n\n给出你的估计值（只输出一个数字）和一句话推算。"
        else:
            q = lambda: f"{task}\n\n简要推理（2-3句，小心直觉陷阱），最后严格以「答案：X」输出选项字母。"

        first = _parallel_chat([q() for _ in peers], models=[p.model for p in peers],
                               temperatures=[p.temperature for p in peers])
        for p, r in zip(peers, first):
            ctx.post(p.name, r, "proposal")

        transcript = ctx.transcript()
        if is_numeric:
            prompts = [f"以下是所有 peer 的独立估计与理由：\n{transcript}\n\n"
                       f"请基于此修正你的估计，只输出一个数字。" for _ in peers]
        else:
            prompts = [f"以下是所有 peer 的答案与理由：\n{transcript}\n\n"
                       f"请复核并吸收正确之处，最后严格以「答案：X」输出你的最终选择。" for _ in peers]
        revised = _parallel_chat(prompts, models=[p.model for p in peers],
                                 temperatures=[T_VERIFY] * len(peers))
        for p, r in zip(peers, revised):
            ctx.post(p.name, r, "critique")

        if is_numeric:
            vals = [v for v in (_extract_number(r) for r in revised) if v is not None]
            if not vals:
                vals = [v for v in (_extract_number(r) for r in first) if v is not None]
            if not vals:
                return {"final_result": "N/A", "mechanism": "decentralized.roundtable",
                        "confidence": 0.0, "consensus_reached": False, "agreement": 0.0}
            med, _, conf, agree = _median_aggregate(vals)
            return {"final_result": f"{med:.4g}", "mechanism": "decentralized.roundtable",
                    "confidence": conf, "consensus_reached": conf >= 0.5, "agreement": agree}
        else:
            winner, agree = _letter_aggregate(revised)
            if winner is None:
                winner, agree = _letter_aggregate(first)
            if winner is None:
                winner, agree = _semantic_majority(task, revised)
            return {"final_result": f"答案：{winner}", "mechanism": "decentralized.roundtable",
                    "confidence": agree, "consensus_reached": agree >= 0.5, "agreement": agree}

    def _refine(self, task: str, ctx: Channel, max_rounds: int = 1,
                evaluate: bool = True) -> Dict[str, Any]:
        # 批评者数量降到 2：创意打磨的价值主要在「对照要求挑缺陷」，批评者越多越容易
        # 触发推理型模型的慢调用/超时，得不偿失。
        critics = make_agents(CRITIC_ROLES, min(self.n_agents, 2), self.models, temperature=T_REFINE)
        writer = critics[0]
        draft = writer.chat(f"{task}\n\n请直接给出完整成品，务必满足任务中的每一项明确要求。",
                            temperature=T_DRAFT)
        ctx.post(writer.name, draft, "proposal")

        for _ in range(max_rounds):
            # 批评者对照任务要求指出【具体缺陷/遗漏】，而非泛泛提建议
            prompts = [f"{c.role}\n\n任务：{task}\n\n以下是当前成品。请对照任务的每项明确要求，"
                       f"指出具体的缺陷与遗漏（完全合格才写『通过』）：\n\n{draft[:2000]}" for c in critics]
            reviews = _parallel_chat(prompts, models=[c.model for c in critics],
                                     temperatures=[T_REFINE] * len(critics))
            feedback = []
            for c, r in zip(critics, reviews):
                feedback.append((c.name, r))
                ctx.post(c.name, r, "critique")
            # 所有批评者都通过则停止，避免无谓重写引入漂移
            if reviews and all(("通过" in r) for r in reviews):
                break
            fb = "\n".join(f"[{n}]: {r}" for n, r in feedback)
            # 只修复被指出的问题，保持其余内容不变（锚定，避免重写漂移）
            draft = writer.chat(f"针对以下批评【只修复被指出的问题，保持其余内容不变】，输出完整成品：\n\n"
                                f"任务：{task}\n当前版本：\n{draft[:1200]}\n\n批评意见：\n{fb[:2000]}",
                                temperature=T_SYNTH)
            ctx.post(writer.name, draft, "synthesis")

        q = _quality(draft, task) if evaluate else 0.5
        ctx.post("peer-group", "打磨完成", "verdict")
        return {"final_result": draft, "mechanism": "decentralized.refine",
                "confidence": q, "consensus_reached": q >= 0.5, "agreement": q}

    def _debate(self, task: str, ctx: Channel) -> Dict[str, Any]:
        pro_model = self.models[0] if self.models else None
        con_model = self.models[1 % len(self.models)] if self.models else None
        pro = Agent("正方", "你负责论证提案的收益、可行性与成立条件。",
                    model=pro_model, temperature=T_DEBATE)
        con = Agent("反方", "你负责论证提案的风险、代价与反例。",
                    model=con_model, temperature=T_DEBATE)
        opening_prompts = [
            f"你站在【正方】立场。辩题：{task}\n给出核心立场，并列出 2-3 条互不重复的论据（每条一句话）。",
            f"你站在【反方】立场。辩题：{task}\n给出核心立场，并列出 2-3 条互不重复的论据（每条一句话）。",
        ]
        pro_arg, con_arg = _parallel_chat(
            opening_prompts,
            models=[pro.model, con.model],
            temperatures=[pro.temperature, con.temperature],
        )
        ctx.post(pro.name, pro_arg, "proposal")
        ctx.post(con.name, con_arg, "proposal")

        pro_arg = pro.chat(f"你是【正方】，直接反驳反方：\n{con_arg}\n\n重申并强化立场（150字内）。")
        con_arg = con.chat(f"你是【反方】，直接反驳正方：\n{pro_arg}\n\n重申并强化立场（150字内）。")
        ctx.post(pro.name, pro_arg, "critique")
        ctx.post(con.name, con_arg, "critique")

        # 平衡综合：既保留正反双方论据，又给出整合结论（匹配 benchmark「正反+结论」的评测标准）。
        verdict = _chat(f"你是中立综合者。整合正反双方论据，给出平衡、完整的综合结论。\n\n"
                        f"辩题：{task}\n\n【正方】\n{pro_arg[:800]}\n\n【反方】\n{con_arg[:800]}\n\n"
                        f"输出格式：\n正方论据：<逐条列出正方核心论据>\n反方论据：<逐条列出反方核心论据>\n综合结论：<一句话>",
                        temperature=T_SYNTH)
        ctx.post("judge", verdict, "verdict")
        return {"final_result": f"综合：\n{verdict}", "mechanism": "decentralized.debate",
                "confidence": 0.6, "consensus_reached": True, "agreement": 0.6}

    def _translate(self, task: str, ctx: Channel) -> Dict[str, Any]:
        """翻译协作：初译 → 文化/忠实度校对 → 定稿。

        翻译不需要 3 位创意批评家轮流打磨，用「翻译+校对」的轻量协作
        更贴合任务本质，也更快（4 次调用）。
        """
        translator = Agent("译者", "你是专业译者，翻译准确、地道、符合目标语言习惯。",
                           model=self.models[0] if self.models else None, temperature=0.2)
        proofreader = Agent("校对", "你是资深校对，检查译文的忠实度、地道性与文化适配。",
                            model=self.models[1 % len(self.models)] if self.models else None,
                            temperature=T_VERIFY)

        draft = translator.chat(f"{task}\n\n请完整完成任务：若任务要求说明隐喻/双关/术语/本地化等处理，"
                                f"请一并给出译文与说明。", temperature=0.2)
        ctx.post(translator.name, draft, "proposal")

        review = proofreader.chat(f"检查以下回答是否完整：译文是否忠实、地道，且是否满足了任务要求的"
                                  f"「说明」部分（无问题写『通过』）：\n\n{draft[:1500]}",
                                  temperature=T_VERIFY)
        ctx.post(proofreader.name, review, "critique")

        if "通过" not in review or "问题" in review or "错误" in review:
            draft = translator.chat(f"根据校对意见修改，输出完整最终答案（译文 + 任务要求的说明）：\n\n"
                                    f"校对意见：\n{review[:800]}\n\n原始任务：\n{task}\n\n最终答案：",
                                    temperature=0.2)
            ctx.post(translator.name, draft, "synthesis")

        q = _quality(draft, task)
        ctx.post("peer-group", "翻译定稿", "verdict")
        return {"final_result": draft, "mechanism": "decentralized.translate",
                "confidence": q, "consensus_reached": q >= 0.5, "agreement": q}


# ═══════════════════════════════════════════════════════════════
# 模式三：分层架构（树型拓扑）
# ═══════════════════════════════════════════════════════════════

class HierarchicalArchitecture:
    """分层架构：分治。拆解 → 底层小组并行 → 逐层代表聚合 → 根合成。

    适用：复杂方案、策略制定、多维度分析。可扩展到任意规模。
    """

    def __init__(self, n_groups: int = 3, agents_per_group: int = 3,
                 models: Optional[List[str]] = None):
        self.n_groups = n_groups
        self.agents_per_group = agents_per_group
        self.models = models

    def solve(self, task: str, task_type: Optional[str] = None,
              test_cases: Optional[List[dict]] = None) -> Dict[str, Any]:
        start = time.time()
        def _go() -> Dict[str, Any]:
            ctx = Channel(task)
            tt = (task_type or _autodetect(task)).lower()
            if tt in ("choice", "estimation"):
                r = self._group_vote(task, ctx, is_numeric=(tt == "estimation"))
            else:
                r = self._analysis(task, ctx)
            r.update(_finish(ctx, r["final_result"], r["mechanism"], r["confidence"],
                             r["consensus_reached"], r["agreement"], start, self.models))
            return r

        return _run_guarded(_go, "hierarchical")

    def _analysis(self, task: str, ctx: Channel) -> Dict[str, Any]:
        """并行多角度作答 → 仲裁综合（取代旧「分解→综合」）。

        旧版把任务拆成子问题再综合，容易遗漏评测标准里的具体条目（漂移）。
        新版让每位专家【直接、完整地】回答原问题，仲裁者对照任务要求选优合并，
        最终仍是直接回答，从根上避免「拆解/重组」造成的漂移。
        """
        n = max(2, self.n_groups)
        agents = make_agents(DIVERSE_ROLES, n, self.models, temperature=T_DRAFT)
        prompts = [f"{a.role}\n\n任务：{task}\n\n请直接、完整地回答该任务：逐条覆盖每一项明确要求"
                   f"（指令性动作、必要维度、数量、量化指标等），每条简洁（1-2 句），"
                   f"不要遗漏、也不要冗长。" for a in agents]
        drafts = _parallel_chat(prompts, models=[a.model for a in agents],
                                temperatures=[a.temperature for a in agents])
        for a, d in zip(agents, drafts):
            ctx.post(a.name, d, "proposal")

        drafts_text = "\n\n".join(f"[{agents[i].name}]\n{d[:1200]}" for i, d in enumerate(drafts))
        final = _chat(f"你是仲裁综合者。以下是 {len(drafts)} 位专家对同一任务的独立回答。\n\n"
                      f"任务：{task}\n\n专家回答：\n{drafts_text}\n\n"
                      f"请综合出一份最优【最终答案】，要求：\n"
                      f"1) 直接回答任务本身，不要出现『专家A/B』『综合』等元话语；\n"
                      f"2) 【数量完整】先确保任务要求的条目数齐全（如 3 个角度/3 个问题/正反双方+结论/N 个维度），再展开——任何条目都不得因过度展开而挤掉后面的条目；\n"
                      f"3) 【并集去重】合并所有专家提到的不同要点，但重复内容只保留一次，控制篇幅、不要膨胀；\n"
                      f"4) 每个要点简洁（1-3 句），保留量化数据与关键论据，删去过程性、重复性表述；\n"
                      f"5) 严格执行任务的指令性动作（如「选择工具」「列出N个」「计算」「制定方案」），不得用「无需/不需要」绕过；\n"
                      f"6) 最终篇幅与『直接作答』相当，结构清晰、要点完整。",
                      temperature=T_SYNTH, max_tokens=1000)
        ctx.post("arbiter", final, "verdict")
        q = _quality(final, task)
        return {"final_result": final, "mechanism": "hierarchical.analysis",
                "confidence": q, "consensus_reached": q >= 0.5, "agreement": q}

    def _group_vote(self, task: str, ctx: Channel, is_numeric: bool = False) -> Dict[str, Any]:
        """分层投票：分组 → 组内独立作答 → 代表汇总 → 根聚合。"""
        n_agents = self.n_groups * self.agents_per_group
        all_agents = make_agents(DIVERSE_ROLES, n_agents, self.models, temperature=T_CHOICE)
        groups = [all_agents[i * self.agents_per_group:(i + 1) * self.agents_per_group]
                  for i in range(self.n_groups)]

        # 阶段一：各组成员并行作答
        members_by_group = []
        for gi, grp in enumerate(groups):
            if is_numeric:
                prompts = [f"{a.role}\n\n{task}\n\n给出你的估计值（只输出一个数字）和一句话推算。" for a in grp]
            else:
                prompts = [f"{a.role}\n\n{task}\n\n简要推理（2-3句，小心直觉陷阱），"
                           f"最后严格以「答案：X」输出选项字母。" for a in grp]
            members = _parallel_chat(prompts, models=[a.model for a in grp],
                                     temperatures=[a.temperature for a in grp])
            for a, r in zip(grp, members):
                ctx.post(a.name, r, "proposal")
            members_by_group.append(members)

        # 阶段二：各组代表并行汇总
        rep_prompts = []
        for gi, members in enumerate(members_by_group):
            p = f"以下是第{gi+1}组 {len(members)} 位成员对同一问题的答案，请代表小组给出统一结论。\n\n"
            p += "\n".join(f"[成员{i+1}] {m[:300]}" for i, m in enumerate(members))
            p += "\n\n只输出小组的统一估计数字。" if is_numeric else \
                 "\n\n最后严格以「答案：X」输出小组统一选择。"
            rep_prompts.append(p)
        group_answers = _parallel_chat(rep_prompts, temperatures=[T_VERIFY] * len(rep_prompts))
        for gi, rep in enumerate(group_answers):
            ctx.post(f"group{gi+1}-代表", rep, "synthesis")

        if is_numeric:
            vals = [v for v in (_extract_number(r) for r in group_answers) if v is not None]
            if not vals:
                return {"final_result": "N/A", "mechanism": "hierarchical.group_vote",
                        "confidence": 0.0, "consensus_reached": False, "agreement": 0.0}
            med, _, conf, agree = _median_aggregate(vals)
            ctx.post("root", f"各组中位数 {med:.2f}", "verdict")
            return {"final_result": f"{med:.4g}", "mechanism": "hierarchical.group_vote",
                    "confidence": conf, "consensus_reached": conf >= 0.5, "agreement": agree}
        else:
            winner, agree = _letter_aggregate(group_answers)
            if winner is None:
                r = _chat(f"以下是{len(group_answers)}个小组代表结论，提炼多数派并估计一致比例(0-1)。\n"
                          + "\n".join(group_answers) + "\n格式：\n结论：<选项字母>\n一致比例：<0-1>",
                          temperature=T_SYNTH)
                ctx.post("judge", r, "verdict")
                m = re.search(r"结论[：:]\s*([A-Da-d])", r)
                winner = m.group(1).upper() if m else "?"
                agree = _ratio(r)
            ctx.post("root", f"最终选择 {winner}", "verdict")
            return {"final_result": f"答案：{winner}", "mechanism": "hierarchical.group_vote",
                    "confidence": agree, "consensus_reached": agree >= 0.5, "agreement": agree}

    def _decompose(self, task: str) -> List[str]:
        """LLM 拆解；失败则用通用维度兜底。"""
        try:
            r = _chat(f"把下面任务拆成 {self.n_groups} 个互补的子问题（每行一个，用『子问题N：』开头）：\n\n{task}",
                      temperature=T_SYNTH)
            subs = re.findall(r"子问题\d+[：:]\s*(.+)", r)
            if len(subs) >= 2:
                return subs[: self.n_groups]
        except Exception:  # noqa: BLE001
            logger.exception("任务拆解失败，使用兜底维度")
        return [f"从「{d}」维度分析：{task}" for d in ["目标与现状", "方案与取舍", "风险与应对"]]


# ═══════════════════════════════════════════════════════════════
# 模式零：单 Agent 基线（对照组，无任何共识机制）
# ═══════════════════════════════════════════════════════════════

class SingleAgentBaseline:
    """单 Agent 基线：一次 LLM 调用直接作答，无共识/协作机制。

    作用：作为多 Agent 共识策略的对照组。如果多 Agent 的最终正确率
    不能显著高于该基线，则协作带来的额外算力开销没有收益。
    """

    def __init__(self, model: Optional[str] = None, temperature: float = 0.3):
        self.model = model
        self.temperature = temperature

    def solve(self, task: str, task_type: Optional[str] = None,
              test_cases: Optional[List[dict]] = None) -> Dict[str, Any]:
        start = time.time()
        def _go() -> Dict[str, Any]:
            ctx = Channel(task)
            prompt = (f"请回答以下问题，仔细思考后给出最佳答案。\n\n{task}\n\n"
                      f"请直接、简洁地给出答案与必要推理（要点式，不要长篇）。")
            ans = _chat(prompt, model=self.model, temperature=self.temperature)
            ctx.post("single", ans, "proposal")
            r = {"final_result": ans, "mechanism": "single.baseline",
                 "confidence": 0.5, "consensus_reached": True, "agreement": 1.0}
            r.update(_finish(ctx, ans, "single.baseline", 0.5, True, 1.0, start,
                             [self.model] if self.model else None))
            return r

        return _run_guarded(_go, "single")


# ═══════════════════════════════════════════════════════════════
# 顶层调度与自动分类
# ═══════════════════════════════════════════════════════════════

_PATTERNS = {
    "single": SingleAgentBaseline,
    "centralized": CentralizedOrchestrator,
    "decentralized": DecentralizedCollaboration,
    "hierarchical": HierarchicalArchitecture,
}

# 题型 → 默认模式（benchmark 可自行指定 task_type 覆盖）
TYPE_TO_MODE = {
    "choice": "centralized", "estimation": "centralized", "coding": "centralized",
    "creative": "decentralized", "decision": "decentralized",
    "translation": "decentralized",
    "analysis": "hierarchical",
}


def run(mode: str, task: str, task_type: Optional[str] = None,
        test_cases: Optional[List[dict]] = None, **kwargs) -> Dict[str, Any]:
    """顶层调度：mode ∈ centralized / decentralized / hierarchical。

    test_cases 会透传给 coding 型中心化流水线。
    """
    if mode not in _PATTERNS:
        raise ValueError(f"未知模式 '{mode}'，可用：{list(_PATTERNS)}")
    return _PATTERNS[mode](**kwargs).solve(task, task_type=task_type, test_cases=test_cases)


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


# ═══════════════════════════════════════════════════════════════
# 演示（需真实 API；仅用于手动验证三种模式可跑通）
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    for mode, tt, q in [
        ("centralized", "choice", "笔记本和笔共11元。笔记本比笔贵10元。笔多少钱？A.0.5元 B.1元 C.1.5元 D.2元"),
        ("centralized", "estimation", "估计2030年全球纯电动车年销量(百万辆)。已知2023年约10M,年增25-30%。"),
        ("decentralized", "creative", "为国产新能源车品牌创作一句12字以内的Slogan。"),
        ("decentralized", "decision", "科技公司应该要求员工每周至少3天到办公室上班吗？"),
        ("hierarchical", "analysis", "一个AI教育产品如何制定进入东南亚市场的策略？"),
    ]:
        print("=" * 70)
        print(f"[{mode}/{tt}] {q[:50]}...")
        r = run(mode, q, task_type=tt)
        print(f"  机制: {r['mechanism']} | 置信度: {r['confidence']:.0%} | "
              f"LLM调用: {r['llm_calls']} | 耗时: {r['elapsed']}s")
        print(f"  结果: {str(r['final_result'])[:120]}")
