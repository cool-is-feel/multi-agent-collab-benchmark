"""Embeddable multi-agent collaboration control layer.

This module governs the existing collaboration patterns without coupling them to a
particular host.  A host such as WorkBuddy only needs to submit a JSON-compatible
request and, optionally, consume audit events through ``PlatformAdapter``.
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from typing import Any, Callable, Dict, List, Optional, Protocol

import collab_patterns as patterns


class GovernanceLimit(RuntimeError):
    """Raised before work that would cross a configured hard boundary."""


class PlatformAdapter(Protocol):
    def emit_event(self, event: Dict[str, Any]) -> None: ...


class NullPlatformAdapter:
    def emit_event(self, event: Dict[str, Any]) -> None:
        return None


class CallbackPlatformAdapter:
    """Small adapter suitable for message buses, webhooks, or platform callbacks."""

    def __init__(self, callback: Callable[[Dict[str, Any]], None]):
        self.callback = callback

    def emit_event(self, event: Dict[str, Any]) -> None:
        self.callback(event)


@dataclass
class ResourceBudget:
    max_steps: int = 40
    max_calls: int = 12
    max_input_tokens: int = 12000
    max_output_tokens: int = 8000
    max_total_tokens: int = 18000
    max_cost_usd: float = 0.10
    max_seconds: float = 300.0
    max_stagnant_messages: int = 3
    max_revisions: int = 1
    input_cost_per_million: float = 1.0
    output_cost_per_million: float = 2.0

    @classmethod
    def from_value(cls, value: Optional[Dict[str, Any] | "ResourceBudget"]):
        if isinstance(value, cls):
            return value
        return cls(**(value or {}))


@dataclass
class CommunicationPolicy:
    duplicate_threshold: float = 0.92
    minimum_novelty: float = 0.08
    lock_decisions: bool = True
    require_revision_reference: bool = True


@dataclass
class CompletionCriterion:
    name: str
    kind: str = "rule"
    required: bool = True
    config: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_value(cls, value: Dict[str, Any] | "CompletionCriterion"):
        return value if isinstance(value, cls) else cls(**value)


@dataclass
class TaskRequest:
    task: str
    task_type: Optional[str] = None
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    risk_level: Optional[str] = None
    constraints: List[str] = field(default_factory=list)
    test_cases: List[dict] = field(default_factory=list)
    completion_criteria: List[CompletionCriterion] = field(default_factory=list)
    budget: ResourceBudget = field(default_factory=ResourceBudget)
    models: Optional[List[str]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    force_topology: Optional[str] = None

    @classmethod
    def from_value(cls, value: Dict[str, Any] | "TaskRequest"):
        if isinstance(value, cls):
            return value
        data = dict(value)
        data["budget"] = ResourceBudget.from_value(data.get("budget"))
        data["completion_criteria"] = [
            CompletionCriterion.from_value(x) for x in data.get("completion_criteria", [])
        ]
        return cls(**data)


@dataclass
class TaskProfile:
    task_type: str
    complexity: float
    ambiguity: float
    decomposability: float
    risk_level: str
    risk_score: float
    verification_modes: List[str]
    answer_objectivity: float = 0.5
    diversity_benefit: float = 0.5
    verification_strength: float = 0.5
    coordination_need: float = 0.5


@dataclass
class RoleSpec:
    name: str
    responsibility: str
    independent: bool = False


@dataclass
class TopologyDecision:
    topology: str
    reason: str
    team_size: int
    roles: List[RoleSpec]
    scorecard: Dict[str, float]
    orchestration_style: str = "flat"
    layers: int = 1
    selection_confidence: float = 0.0
    alternatives: Dict[str, Dict[str, float]] = field(default_factory=dict)
    decision_margin: float = 0.0
    needs_exploration: bool = False


@dataclass
class VerificationCheck:
    name: str
    mode: str
    passed: Optional[bool]
    required: bool
    detail: str


@dataclass
class VerificationReport:
    passed: bool
    status: str
    checks: List[VerificationCheck]
    attempt: int
    evidence: List[str] = field(default_factory=list)


@dataclass
class AuditEvent:
    sequence: int
    timestamp: float
    event_type: str
    actor: str
    data: Dict[str, Any]


class RuntimeGovernor:
    """Thread-safe hard budgets, communication gate, and append-only audit log."""

    def __init__(self, budget: ResourceBudget, policy: CommunicationPolicy,
                 adapter: PlatformAdapter, task_id: str):
        self.budget = budget
        self.policy = policy
        self.adapter = adapter
        self.task_id = task_id
        self.started_at = time.monotonic()
        self.lock = threading.RLock()
        self.calls = 0
        self.steps = 0
        self.revisions = 0
        self.estimated_input_tokens = 0
        self.estimated_output_tokens = 0
        self.reserved_output_tokens = 0
        self.provider_input_tokens = 0
        self.provider_output_tokens = 0
        self.cost_usd = 0.0
        self.messages = 0
        self.rejected_messages = 0
        self.stagnant_messages = 0
        self.stop_reason: Optional[str] = None
        self._contents: List[str] = []
        self._decisions: Dict[str, str] = {}
        self._phase_rank = -1
        self.events: List[AuditEvent] = []
        self.emit("run.started", "control-layer", {"budget": asdict(budget)})

    @staticmethod
    def _token_estimate(text: str) -> int:
        return max(1, math.ceil(len(text) / 4))

    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def remaining_seconds(self) -> float:
        return max(0.1, self.budget.max_seconds - self.elapsed())

    def emit(self, event_type: str, actor: str, data: Dict[str, Any]) -> None:
        with self.lock:
            event = AuditEvent(len(self.events) + 1, time.time(), event_type, actor, data)
            self.events.append(event)
        try:
            self.adapter.emit_event({"task_id": self.task_id, **asdict(event)})
        except Exception as exc:  # host observability must not break task execution
            with self.lock:
                self.events.append(AuditEvent(
                    len(self.events) + 1, time.time(), "adapter.error", "control-layer",
                    {"error": str(exc)},
                ))

    def _fail(self, reason: str) -> None:
        self.stop_reason = self.stop_reason or reason
        self.emit("boundary.reached", "governor", {"reason": self.stop_reason})
        raise GovernanceLimit(self.stop_reason)

    def before_call(self, prompt: str, model: Optional[str], max_tokens: int) -> Dict[str, Any]:
        with self.lock:
            if self.stop_reason:
                raise GovernanceLimit(self.stop_reason)
            if self.elapsed() >= self.budget.max_seconds:
                self._fail("time_budget")
            if self.calls + 1 > self.budget.max_calls:
                self._fail("call_budget")
            if self.steps + 1 > self.budget.max_steps:
                self._fail("step_budget")
            prompt_tokens = self._token_estimate(prompt)
            projected_in = self.estimated_input_tokens + prompt_tokens
            projected_out = self.estimated_output_tokens + self.reserved_output_tokens + max_tokens
            if projected_in > self.budget.max_input_tokens:
                self._fail("input_token_budget")
            if projected_out > self.budget.max_output_tokens:
                self._fail("output_token_budget")
            if projected_in + projected_out > self.budget.max_total_tokens:
                self._fail("total_token_budget")
            projected_cost = (
                projected_in * self.budget.input_cost_per_million
                + projected_out * self.budget.output_cost_per_million
            ) / 1_000_000
            if projected_cost > self.budget.max_cost_usd:
                self._fail("cost_budget")
            self.calls += 1
            self.steps += 1
            self.estimated_input_tokens += prompt_tokens
            self.reserved_output_tokens += max_tokens
            reservation = {"prompt_tokens": prompt_tokens, "max_tokens": max_tokens,
                           "model": model or "default", "started": time.monotonic()}
            self.emit("model.call.started", "governor", {
                "call": self.calls, "model": reservation["model"],
                "prompt_tokens_estimate": prompt_tokens, "max_tokens": max_tokens,
            })
            return reservation

    def after_call(self, reservation: Optional[Dict[str, Any]], content: str = "",
                   error: Optional[Exception] = None) -> None:
        if not reservation:
            return
        with self.lock:
            self.reserved_output_tokens = max(
                0, self.reserved_output_tokens - reservation["max_tokens"]
            )
            output_tokens = self._token_estimate(content) if content else 0
            self.estimated_output_tokens += output_tokens
            self.cost_usd = (
                self.estimated_input_tokens * self.budget.input_cost_per_million
                + self.estimated_output_tokens * self.budget.output_cost_per_million
            ) / 1_000_000
            self.emit("model.call.finished", "governor", {
                "model": reservation["model"], "output_tokens_estimate": output_tokens,
                "elapsed": round(time.monotonic() - reservation["started"], 4),
                "error": str(error) if error else None,
            })

    def record_provider_usage(self, input_tokens: int, output_tokens: int) -> None:
        with self.lock:
            self.provider_input_tokens += input_tokens
            self.provider_output_tokens += output_tokens

    @staticmethod
    def _normalize(content: str) -> str:
        return re.sub(r"[^\w\u4e00-\u9fff]+", "", content.lower())

    def evaluate_message(self, message: Any) -> Dict[str, Any]:
        normalized = self._normalize(message.content)
        with self.lock:
            self.messages += 1
            if self.steps + 1 > self.budget.max_steps:
                accepted, reason, novelty = False, "step_budget", 0.0
            else:
                self.steps += 1
                similarities = [SequenceMatcher(None, normalized, old).ratio()
                                for old in self._contents[-20:]] if normalized else [1.0]
                max_similarity = max(similarities, default=0.0)
                novelty = 1.0 - max_similarity
                accepted = (max_similarity < self.policy.duplicate_threshold
                            and novelty >= self.policy.minimum_novelty)
                reason = None if accepted else "duplicate_or_low_novelty"

                phase = int(message.meta.get("phase", self._phase_rank))
                if accepted and phase < self._phase_rank and not message.meta.get("allow_reopen"):
                    accepted, reason = False, "state_regression"
                decision_key = message.meta.get("decision_key")
                if accepted and decision_key and decision_key in self._decisions:
                    revision_ref = message.meta.get("revises_decision")
                    if self.policy.lock_decisions and revision_ref != self._decisions[decision_key]:
                        accepted, reason = False, "decided_item_locked"

                if accepted:
                    self._contents.append(normalized)
                    self._phase_rank = max(self._phase_rank, phase)
                    if message.kind == "verdict" and decision_key:
                        self._decisions[decision_key] = message.message_id
                    self.stagnant_messages = 0 if novelty >= self.policy.minimum_novelty else self.stagnant_messages + 1
                else:
                    self.rejected_messages += 1
                    self.stagnant_messages += 1
                    if self.stagnant_messages >= self.budget.max_stagnant_messages:
                        self.stop_reason = self.stop_reason or "semantic_stagnation"

            self.emit("message.accepted" if accepted else "message.rejected", message.sender, {
                "message_id": message.message_id, "kind": message.kind,
                "novelty": round(novelty, 4), "reason": reason,
            })
            return {"accepted": accepted, "reason": reason, "novelty": novelty}

    def can_continue(self, calls_needed: int = 1, require_revision: bool = False) -> bool:
        with self.lock:
            return (not self.stop_reason and self.elapsed() < self.budget.max_seconds
                    and self.calls + calls_needed <= self.budget.max_calls
                    and (not require_revision or self.revisions < self.budget.max_revisions))

    def mark_revision(self, mode: str) -> None:
        with self.lock:
            if self.revisions >= self.budget.max_revisions:
                self._fail("revision_budget")
            self.revisions += 1
            self.emit("result.revision.started", "control-layer", {
                "revision": self.revisions, "mode": mode,
            })

    def usage(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "calls": self.calls, "steps": self.steps, "revisions": self.revisions,
                "estimated_input_tokens": self.estimated_input_tokens,
                "estimated_output_tokens": self.estimated_output_tokens,
                "provider_input_tokens": self.provider_input_tokens,
                "provider_output_tokens": self.provider_output_tokens,
                "estimated_cost_usd": round(self.cost_usd, 6),
                "elapsed_seconds": round(self.elapsed(), 3),
                "messages": self.messages, "rejected_messages": self.rejected_messages,
            }


class CollaborationControlLayer:
    """Profile -> select topology/team -> govern -> verify -> terminate."""

    RISK_WORDS = {
        "high": ("医疗", "法律", "合规", "财务", "生产", "安全", "隐私", "支付"),
        "medium": ("客户", "上线", "合同", "决策", "迁移", "部署", "数据"),
    }
    DIVERSITY_WORDS = (
        "创意", "创作", "头脑风暴", "多个观点", "不同观点", "正反", "辩论",
        "权衡", "争议", "伦理", "受众", "风格", "本地化", "隐喻", "双关",
    )
    OBJECTIVE_WORDS = (
        "计算", "求解", "证明", "代码", "函数", "算法", "调试", "唯一",
        "准确", "事实", "来源", "步骤", "约束", "验收", "测试", "API",
    )

    def __init__(self, adapter: Optional[PlatformAdapter] = None,
                 communication_policy: Optional[CommunicationPolicy] = None):
        self.adapter = adapter or NullPlatformAdapter()
        self.communication_policy = communication_policy or CommunicationPolicy()

    def profile_task(self, request: TaskRequest) -> TaskProfile:
        text = request.task
        task_type = (request.task_type or patterns._autodetect(text)).lower()
        constraint_count = len(request.constraints) + len(re.findall(
            r"(?:必须|不得|至少|至多|要求|需要|\d+[\.、])", text
        ))
        complexity = min(1.0, 0.15 + len(text) / 1200 + constraint_count * 0.08
                         + (0.15 if request.test_cases else 0))
        type_ambiguity = {
            "creative": 0.75, "decision": 0.72, "translation": 0.55,
            "analysis": 0.48, "choice": 0.25, "estimation": 0.25,
            "computation": 0.08, "coding": 0.15,
        }.get(task_type, 0.4)
        ambiguity = min(1.0, type_ambiguity + min(0.15, text.count("或") * 0.04))
        decomposition_words = ("分别", "多个", "方案", "步骤", "模块", "维度", "系统", "架构")
        decomposability = min(1.0, 0.15 + sum(w in text for w in decomposition_words) * 0.12
                              + complexity * 0.35)
        if request.risk_level:
            risk_level = request.risk_level.lower()
        elif any(word in text for word in self.RISK_WORDS["high"]):
            risk_level = "high"
        elif any(word in text for word in self.RISK_WORDS["medium"]):
            risk_level = "medium"
        else:
            risk_level = "low"
        if risk_level not in ("low", "medium", "high"):
            risk_level = "medium"
        risk_score = {"low": 0.25, "medium": 0.6, "high": 0.9}.get(risk_level, 0.6)

        objectivity = {
            "computation": 0.98, "coding": 0.92, "choice": 0.88,
            "estimation": 0.78, "translation": 0.52, "analysis": 0.58,
            "decision": 0.32, "creative": 0.12,
        }.get(task_type, 0.5)
        diversity = {
            "creative": 0.95, "decision": 0.9, "translation": 0.72,
            "analysis": 0.42, "estimation": 0.38, "choice": 0.2,
            "coding": 0.15, "computation": 0.1,
        }.get(task_type, 0.4)
        diversity_hits = sum(word in text for word in self.DIVERSITY_WORDS)
        objective_hits = sum(word in text for word in self.OBJECTIVE_WORDS)
        diversity = min(1.0, diversity + min(0.24, diversity_hits * 0.06))
        objectivity = min(1.0, objectivity + min(0.18, objective_hits * 0.03))
        if diversity_hits and task_type not in ("computation", "coding"):
            objectivity = max(0.05, objectivity - min(0.12, diversity_hits * 0.03))

        verification_strength = min(
            1.0,
            0.18 + 0.50 * objectivity + 0.12 * bool(request.test_cases)
            + 0.04 * min(3, constraint_count)
            + 0.08 * any(word in text for word in ("来源", "证据", "测试", "验收")),
        )
        coordination_need = min(
            1.0, 0.12 + 0.42 * complexity + 0.34 * decomposability
            + 0.10 * (task_type in ("coding", "analysis"))
        )
        modes = ["rule"]
        if request.test_cases:
            modes.append("test")
        if risk_level in ("medium", "high"):
            modes.append("independent_review")
        if "证据" in text or "来源" in text or "事实" in text:
            modes.append("evidence")
        return TaskProfile(
            task_type, round(complexity, 3), round(ambiguity, 3),
            round(decomposability, 3), risk_level, risk_score, modes,
            round(objectivity, 3), round(diversity, 3),
            round(verification_strength, 3), round(coordination_need, 3),
        )

    def select_topology(self, profile: TaskProfile, budget: ResourceBudget,
                        force_topology: Optional[str] = None) -> TopologyDecision:
        """Choose where authority lives, then size the centralized hierarchy if selected.

        The score is expected utility, not a claim of universal optimality.  Every
        component is returned for audit and can later be calibrated from run data.
        """
        tt = profile.task_type
        objective = tt in ("choice", "estimation", "computation", "coding")
        tight_budget = budget.max_calls <= 4 or budget.max_total_tokens <= 4000

        central_fit = min(1.0, 0.20 + 0.46 * profile.answer_objectivity
                          + 0.20 * profile.coordination_need
                          + 0.12 * profile.decomposability
                          - 0.10 * profile.diversity_benefit)
        decentralized_fit = min(1.0, 0.20 + 0.48 * profile.diversity_benefit
                                + 0.16 * profile.ambiguity
                                + 0.08 * (1.0 - profile.coordination_need)
                                - 0.10 * profile.answer_objectivity)

        # Risk controls verification intensity. It is not evidence that one authority
        # topology is universally safer, so it is not baked into either capability.
        central_verification = min(1.0, 0.38 + 0.48 * profile.verification_strength
                                   + 0.08 * profile.answer_objectivity)
        decentralized_verification = min(
            1.0, 0.38 + 0.30 * profile.verification_strength
            + 0.20 * profile.diversity_benefit
            + 0.06 * profile.ambiguity,
        )

        # Estimated resource ratios are deliberately simple and observable.  They
        # will be replaced/calibrated by historical p(success), cost and latency.
        central_calls = min(budget.max_calls, 3 + round(4 * profile.complexity))
        decentralized_calls = min(budget.max_calls, 5 + round(4 * profile.ambiguity))
        central_cost = central_calls / max(1, budget.max_calls)
        decentralized_cost = decentralized_calls / max(1, budget.max_calls)
        central_latency = min(1.0, (2 + 2 * profile.complexity) / 6)
        decentralized_latency = min(1.0, (3 + 3 * profile.ambiguity) / 6)
        central_risk = profile.risk_score * (
            0.10 + 0.08 * profile.diversity_benefit
        )
        decentralized_risk = profile.risk_score * (
            0.10 + 0.08 * profile.answer_objectivity
        )

        def components(fit, verification, cost, latency, risk):
            utility = (0.55 * fit + 0.25 * verification - 0.10 * cost
                       - 0.05 * latency - 0.05 * risk)
            return {
                "task_fit": round(fit, 4),
                "verification_capability": round(verification, 4),
                "estimated_cost_ratio": round(cost, 4),
                "estimated_latency_ratio": round(latency, 4),
                "risk_penalty": round(risk, 4),
                "expected_utility": round(utility, 4),
            }

        alternatives = {
            "centralized": components(central_fit, central_verification, central_cost,
                                      central_latency, central_risk),
            "decentralized": components(decentralized_fit, decentralized_verification,
                                        decentralized_cost, decentralized_latency,
                                        decentralized_risk),
        }
        scores = {name: values["expected_utility"] for name, values in alternatives.items()}
        topology = max(scores, key=scores.get)
        if force_topology not in (None, "centralized", "decentralized"):
            raise ValueError("force_topology must be centralized or decentralized")
        if not force_topology:
            if budget.max_calls <= 3 or budget.max_total_tokens <= 3000:
                topology = "centralized"
            if tt == "coding":
                topology = "centralized"
        else:
            # Explicit forcing exists only for paired evaluation and operator
            # overrides; normal adaptive execution still applies safety defaults.
            topology = force_topology
        ranked = sorted(scores.values(), reverse=True)
        margin = ranked[0] - ranked[1]
        confidence = min(0.99, 0.5 + 2.5 * margin)
        needs_exploration = not force_topology and margin < 0.06

        available_workers = max(1, min(6, budget.max_calls - 2))
        if topology == "decentralized":
            layers = 1
            orchestration_style = "peer_mesh"
            team_size = min(4, available_workers)
            roles = [
                RoleSpec("author", "own the current candidate"),
                RoleSpec("peer-critic", "contribute only novel defects or evidence", True),
                RoleSpec("domain-peer", "check audience and domain fit", True),
                RoleSpec("consensus-peer", "record convergence"),
            ][:team_size]
        else:
            structure_need = (0.45 * profile.complexity + 0.40 * profile.decomposability
                              + 0.15 * profile.risk_score)
            # layers includes the worker layer and all coordinating layers.
            affordable_layers = 2 if tight_budget else (3 if budget.max_calls < 9 else 4)
            desired_layers = 2 + int(structure_need >= 0.50) + int(structure_need >= 0.80)
            layers = min(desired_layers, affordable_layers)
            orchestration_style = "specialized_pipeline" if objective else (
                "flat_star" if layers == 2 else "layered_tree"
            )
            team_size = min(4, available_workers)
            roles = [
                RoleSpec("controller", "allocate work and lock decisions"),
                RoleSpec("solver", "produce an independent candidate", True),
                RoleSpec("critic", "check errors and constraints", True),
                RoleSpec("verifier", "run rules or tests", True),
                RoleSpec("synthesizer", "produce the bounded final result"),
            ][:team_size]
        selection_kind = "forced" if force_topology else "selected"
        reason = (f"{selection_kind} {topology} with utility={scores[topology]:.4f}; "
                  f"runner_up_margin={margin:.4f}; task_type={tt}; complexity={profile.complexity}; "
                  f"ambiguity={profile.ambiguity}; decomposability={profile.decomposability}; "
                  f"objectivity={profile.answer_objectivity}; diversity={profile.diversity_benefit}; "
                  f"risk={profile.risk_level}; layers={layers}; call_budget={budget.max_calls}")
        return TopologyDecision(topology, reason, team_size, roles,
                                {k: round(v, 4) for k, v in scores.items()},
                                orchestration_style, layers, round(confidence, 4), alternatives,
                                round(margin, 4), needs_exploration)

    def _run_layered_centralized(self, request: TaskRequest,
                                 decision: TopologyDecision) -> Dict[str, Any]:
        """Execute a budget-bounded central tree with a variable aggregation depth."""
        started = time.time()
        ctx = patterns.Channel(request.task)
        leaf_count = max(2, min(decision.team_size, 4))
        agents = patterns.make_agents(patterns.DIVERSE_ROLES, leaf_count, request.models,
                                      temperature=patterns.T_DRAFT)
        prompts = [
            f"{agent.role}\n\n任务：{request.task}\n约束：{request.constraints}\n\n"
            "独立给出完整候选结果，列明关键依据、假设和可验证条件。"
            for agent in agents
        ]
        current = patterns._parallel_chat(
            prompts, models=[a.model for a in agents], temperatures=[a.temperature for a in agents]
        )
        for agent, content in zip(agents, current):
            ctx.post(agent.name, content, "proposal", phase=0,
                     claims=["complete_candidate"], evidence=[])

        # Each non-root layer compresses bounded groups; the root performs final arbitration.
        for level in range(1, decision.layers):
            is_root = level == decision.layers - 1
            group_size = len(current) if is_root else 2
            groups = [current[i:i + group_size] for i in range(0, len(current), group_size)]
            next_level = []
            for group_index, group in enumerate(groups):
                material = "\n\n".join(f"[候选{i + 1}] {x[:1400]}" for i, x in enumerate(group))
                instruction = ("作为根控制器，仲裁冲突并输出直接面向用户的完整最终结果。"
                               if is_root else
                               "作为中层负责人，去重、保留证据并形成给上一级的结构化摘要。")
                output = patterns._chat(
                    f"{instruction}\n任务：{request.task}\n约束：{request.constraints}\n\n{material}",
                    temperature=patterns.T_SYNTH, max_tokens=1000 if is_root else 600,
                )
                sender = "root-controller" if is_root else f"level-{level}-lead-{group_index + 1}"
                ctx.post(sender, output, "verdict" if is_root else "synthesis", phase=level,
                         claims=["final_decision" if is_root else "bounded_summary"],
                         decision_key="final_result" if is_root else None)
                next_level.append(output)
            current = next_level

        final = current[0] if len(current) == 1 else patterns._chat(
            f"作为根控制器，将以下候选合成最终答案：\n" + "\n\n".join(current),
            temperature=patterns.T_SYNTH, max_tokens=1000,
        )
        quality = patterns._quality(final, request.task)
        result = patterns._finish(ctx, final, "centralized.layered", quality,
                                  quality >= 0.5, quality, started, request.models)
        return result

    def _run_topology(self, request: TaskRequest, decision: TopologyDecision) -> Dict[str, Any]:
        common = {"models": request.models}
        if decision.topology == "decentralized":
            solver = patterns.DecentralizedCollaboration(
                n_agents=max(2, decision.team_size), **common
            )
        elif decision.orchestration_style == "layered_tree":
            return self._run_layered_centralized(request, decision)
        elif decision.orchestration_style == "specialized_pipeline":
            solver = patterns.CentralizedOrchestrator(
                n_agents=max(1, decision.team_size - 1), **common
            )
        else:
            # A low-complexity open task still needs a controller to merge worker answers.
            solver = patterns.HierarchicalArchitecture(
                n_groups=max(2, decision.team_size - 1), agents_per_group=1, **common
            )
        return solver.solve(request.task, task_type=request.task_type,
                            test_cases=request.test_cases or None)

    @staticmethod
    def _criterion_check(criterion: CompletionCriterion, answer: str) -> VerificationCheck:
        cfg = criterion.config
        passed: Optional[bool]
        detail = ""
        if criterion.kind == "rule":
            required_terms = cfg.get("required_terms", [])
            forbidden_terms = cfg.get("forbidden_terms", [])
            min_length = int(cfg.get("min_length", 1))
            max_length = int(cfg.get("max_length", 10**9))
            missing = [term for term in required_terms if term not in answer]
            forbidden = [term for term in forbidden_terms if term in answer]
            passed = min_length <= len(answer) <= max_length and not missing and not forbidden
            detail = f"missing={missing}, forbidden={forbidden}, length={len(answer)}"
        elif criterion.kind == "evidence":
            markers = cfg.get("markers", ["来源", "依据", "证据", "http"])
            passed = any(marker in answer for marker in markers)
            detail = "evidence marker found" if passed else "no evidence marker found"
        else:
            passed = None
            detail = f"unsupported declarative criterion kind: {criterion.kind}"
        return VerificationCheck(criterion.name, criterion.kind, passed,
                                 criterion.required, detail)

    def _verify(self, request: TaskRequest, profile: TaskProfile, answer: str,
                result: Dict[str, Any], governor: RuntimeGovernor, attempt: int) -> VerificationReport:
        checks = [
            VerificationCheck("non_empty", "rule", bool(answer.strip()), True,
                              "answer is present" if answer.strip() else "answer is empty"),
            VerificationCheck("no_runtime_error", "rule", not answer.startswith("ERROR:"), True,
                              "no runtime error" if not answer.startswith("ERROR:") else answer[:200]),
        ]
        checks.extend(self._criterion_check(c, answer) for c in request.completion_criteria)
        runner_messages = [m for m in result.get("history", [])
                           if getattr(m, "sender", "") == "runner"]
        if request.test_cases:
            test_passed = False
            for message in runner_messages:
                match = re.search(r"实测\s+(\d+)/(\d+)", getattr(message, "content", ""))
                if match and int(match.group(1)) == int(match.group(2)):
                    test_passed = True
                    break
            checks.append(VerificationCheck(
                "executable_tests", "test", test_passed, True,
                "all pipeline tests passed" if test_passed else "no all-pass runner evidence",
            ))

        # Adaptive runs need a quality gate even for low-risk prompts.  Risk is
        # about consequence; it is not a proxy for whether a candidate covers
        # every requested requirement.  Forced topology runs retain the cheaper
        # historical policy for paired benchmarking.
        review_required = (
            profile.risk_level in ("medium", "high")
            or request.metadata.get("adaptive_quality_gate", False)
            or request.force_topology is None
        )
        if review_required and governor.can_continue():
            prompt = (
                "你是独立验收员，不参与原协作。严格检查候选结果是否完成任务、满足约束、"
                "没有关键事实或逻辑错误。只输出 JSON："
                '{"passed": true/false, "reason": "一句话", "evidence": ["依据"]}。\n\n'
                f"任务：{request.task}\n约束：{request.constraints}\n候选结果：\n{answer[:3000]}"
            )
            raw = patterns._chat(prompt, temperature=0.0, max_tokens=300)
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            try:
                review = json.loads(match.group(0) if match else raw)
                review_passed = bool(review.get("passed"))
                detail = str(review.get("reason", ""))[:500]
                evidence = [str(x) for x in review.get("evidence", [])][:10]
            except (ValueError, AttributeError):
                review_passed = bool(re.search(r'passed["\s:：]+(?:true|是|通过)', raw, re.I))
                detail, evidence = raw[:500], []
            checks.append(VerificationCheck("independent_review", "independent_review",
                                            review_passed, True, detail))
        elif review_required:
            evidence = []
            checks.append(VerificationCheck("independent_review", "independent_review",
                                            None, profile.risk_level == "high",
                                            "skipped: insufficient remaining budget"))
        else:
            evidence = []

        failed = [c for c in checks if c.required and c.passed is not True]
        report = VerificationReport(not failed, "passed" if not failed else "failed",
                                    checks, attempt, evidence)
        governor.emit("verification.completed", "independent-verifier", {
            "attempt": attempt, "passed": report.passed,
            "failed_checks": [c.name for c in failed],
        })
        return report

    def _revise(self, request: TaskRequest, profile: TaskProfile, answer: str,
                report: VerificationReport, governor: RuntimeGovernor) -> str:
        mode = "arbitration" if profile.risk_level == "high" else "local_reconstruction"
        governor.mark_revision(mode)
        failures = "\n".join(f"- {c.name}: {c.detail}" for c in report.checks
                             if c.required and c.passed is not True)
        prompt = (
            f"你负责{('独立仲裁' if mode == 'arbitration' else '局部重构')}。"
            "只修复验收失败点，保留候选中已经正确的部分；不得扩大任务范围。\n\n"
            f"任务：{request.task}\n约束：{request.constraints}\n失败点：\n{failures}\n\n"
            f"当前候选：\n{answer[:3500]}\n\n输出修复后的完整最终结果。"
        )
        revised = patterns._chat(prompt, temperature=0.1, max_tokens=1000)
        governor.emit("result.revised", "arbiter" if mode == "arbitration" else "repairer",
                      {"mode": mode, "changed": revised.strip() != answer.strip()})
        return revised

    @staticmethod
    def _failed_required_checks(report: VerificationReport) -> List[VerificationCheck]:
        return [check for check in report.checks
                if check.required and check.passed is not True]

    def _should_try_alternate(self, request: TaskRequest, decision: TopologyDecision,
                              answer: str, report: VerificationReport) -> bool:
        """Require objective evidence before paying for a second topology.

        Independent LLM review is useful but noisy.  A single subjective rejection
        must not override a high-confidence routing decision.  Empty/error outputs,
        deterministic checks, and genuinely uncertain routes are stronger signals.
        """
        override = request.metadata.get("topology_fallback")
        if override is False:
            return False
        if override is True:
            return True
        if not answer.strip() or answer.startswith("ERROR:"):
            return True
        failed = self._failed_required_checks(report)
        deterministic_failure = any(
            check.mode in ("rule", "test", "evidence") for check in failed
        )
        return deterministic_failure or decision.needs_exploration

    @staticmethod
    def _fallback_call_reserve(profile: TaskProfile, budget: ResourceBudget,
                               topology: str) -> int:
        """Reserve a full alternate run plus its independent quality check."""
        if topology == "centralized":
            solver_calls = 3 + round(4 * profile.complexity)
        else:
            solver_calls = 5 + round(4 * profile.ambiguity)
        return min(budget.max_calls, solver_calls) + 1

    def _prefer_revision(self, original: VerificationReport,
                         revised: VerificationReport) -> bool:
        if revised.passed:
            return True
        if original.passed:
            return False
        return (len(self._failed_required_checks(revised))
                < len(self._failed_required_checks(original)))

    def execute(self, value: Dict[str, Any] | TaskRequest) -> Dict[str, Any]:
        request = TaskRequest.from_value(value)
        governor = RuntimeGovernor(request.budget, self.communication_policy,
                                   self.adapter, request.task_id)
        profile = self.profile_task(request)
        decision = self.select_topology(profile, request.budget, request.force_topology)
        governor.emit("topology.selected", "control-layer", {
            **asdict(decision), "profile": asdict(profile),
        })
        token = patterns.set_governor(governor)
        result: Dict[str, Any] = {}
        answer = ""
        report = VerificationReport(False, "not_run", [], 0)
        try:
            result = self._run_topology(request, decision)
            answer = str(result.get("final_result", ""))
            report = self._verify(request, profile, answer, result, governor, 1)
            # A topology decision is a hypothesis, not a guarantee.  For an
            # adaptive request, test the other topology before spending the
            # remaining revision budget.  This reserves room for a genuinely
            # different solution instead of repeatedly polishing a failed one.
            alternate = (
                "decentralized" if decision.topology == "centralized" else "centralized"
            )
            fallback_calls = self._fallback_call_reserve(
                profile, request.budget, alternate
            )
            if (
                not request.force_topology
                and not report.passed
                and self._should_try_alternate(request, decision, answer, report)
                and governor.can_continue(calls_needed=fallback_calls)
            ):
                alternate_decision = self.select_topology(profile, request.budget, alternate)
                governor.emit("topology.fallback", "control-layer", {
                    "from": decision.topology, "to": alternate,
                    "reason": "objective failure or uncertain routing decision",
                    "reserved_calls": fallback_calls,
                })
                alternate_result = self._run_topology(request, alternate_decision)
                alternate_answer = str(alternate_result.get("final_result", ""))
                alternate_report = self._verify(
                    request, profile, alternate_answer, alternate_result, governor, 1
                )
                if alternate_report.passed or not answer.strip():
                    decision = alternate_decision
                    result, answer, report = (
                        alternate_result, alternate_answer, alternate_report
                    )
            if not report.passed and governor.can_continue(require_revision=True):
                revised_answer = self._revise(
                    request, profile, answer, report, governor
                )
                revised_report = self._verify(
                    request, profile, revised_answer, result, governor, 2
                )
                if self._prefer_revision(report, revised_report):
                    answer, report = revised_answer, revised_report
        except GovernanceLimit as exc:
            governor.stop_reason = governor.stop_reason or str(exc)
            if not answer:
                answer = str(result.get("final_result", ""))
        except Exception as exc:  # keep platform integrations deterministic and auditable
            governor.stop_reason = governor.stop_reason or "execution_error"
            governor.emit("run.error", "control-layer", {"error": repr(exc)})
            if not answer:
                answer = f"ERROR: {exc}"
        finally:
            patterns.reset_governor(token)

        if governor.stop_reason:
            status = "budget_exhausted" if governor.stop_reason.endswith("budget") else governor.stop_reason
        elif report.passed:
            status = "completed"
        else:
            status = "verification_failed"
        governor.emit("run.terminated", "control-layer", {
            "status": status, "reason": governor.stop_reason or report.status,
        })
        history = [m.as_dict() if hasattr(m, "as_dict") else m
                   for m in result.get("history", [])]
        usage = governor.usage()
        measured_input = usage["provider_input_tokens"] or usage["estimated_input_tokens"]
        measured_output = usage["provider_output_tokens"] or usage["estimated_output_tokens"]
        return {
            **{k: v for k, v in result.items() if k != "history"},
            "final_result": answer,
            "mechanism": f"adaptive.{decision.topology}",
            "llm_calls": usage["calls"],
            "token_input": measured_input,
            "token_output": measured_output,
            "token_total": measured_input + measured_output,
            "elapsed": usage["elapsed_seconds"],
            "task_profile": asdict(profile),
            "topology_decision": asdict(decision),
            "team": [asdict(role) for role in decision.roles],
            "verification_report": asdict(report),
            "termination": {"status": status,
                            "reason": governor.stop_reason or report.status},
            "budget_usage": usage,
            "history": history,
            "audit_trail": [asdict(event) for event in governor.events],
        }


def execute(request: Dict[str, Any] | TaskRequest,
            adapter: Optional[PlatformAdapter] = None) -> Dict[str, Any]:
    """Stateless convenience entry point for platform embedding."""
    return CollaborationControlLayer(adapter=adapter).execute(request)
