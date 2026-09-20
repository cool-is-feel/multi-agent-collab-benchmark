import sys
import json
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import collab_patterns as patterns  # noqa: E402
from control_layer import (  # noqa: E402
    CollaborationControlLayer,
    CommunicationPolicy,
    GovernanceLimit,
    NullPlatformAdapter,
    ResourceBudget,
    RuntimeGovernor,
    TaskRequest,
    VerificationCheck,
    VerificationReport,
)
from run_benchmark import benchmark_governance, score_math  # noqa: E402


class TopologySelectionTests(unittest.TestCase):
    def setUp(self):
        self.layer = CollaborationControlLayer()

    def test_coding_uses_centralized_pipeline(self):
        request = TaskRequest(task="编写 Python 函数并处理边界条件", task_type="coding")
        profile = self.layer.profile_task(request)
        decision = self.layer.select_topology(profile, request.budget)
        self.assertEqual("centralized", decision.topology)
        self.assertTrue(any(role.independent for role in decision.roles))

    def test_complex_analysis_uses_layered_centralization(self):
        request = TaskRequest(
            task="从多个模块、风险、步骤和量化指标分别制定系统迁移方案",
            task_type="analysis", risk_level="high",
        )
        profile = self.layer.profile_task(request)
        decision = self.layer.select_topology(profile, request.budget)
        self.assertEqual("centralized", decision.topology)
        self.assertEqual("layered_tree", decision.orchestration_style)
        self.assertGreaterEqual(decision.layers, 2)
        self.assertEqual({"centralized", "decentralized"}, set(decision.alternatives))

    def test_centralized_depth_increases_with_structure_need(self):
        simple = TaskRequest(task="分析这个观点", task_type="analysis")
        complex_task = TaskRequest(
            task="从多个系统模块、实施步骤、风险维度和验收指标分别制定复杂生产迁移架构方案",
            task_type="analysis", risk_level="high",
        )
        simple_decision = self.layer.select_topology(
            self.layer.profile_task(simple), simple.budget
        )
        complex_decision = self.layer.select_topology(
            self.layer.profile_task(complex_task), complex_task.budget
        )
        self.assertGreaterEqual(complex_decision.layers, simple_decision.layers)

    def test_tight_budget_prefers_centralized(self):
        request = TaskRequest(
            task="提出多个创意方案并讨论取舍", task_type="creative",
            budget=ResourceBudget(max_calls=3, max_total_tokens=3000,
                                  max_input_tokens=2000, max_output_tokens=2000),
        )
        profile = self.layer.profile_task(request)
        decision = self.layer.select_topology(profile, request.budget)
        self.assertEqual("centralized", decision.topology)

    def test_creative_and_debate_tasks_use_decentralized_topology(self):
        for request in (
            TaskRequest(task="为年轻受众提出三个不同风格的创意口号", task_type="creative"),
            TaskRequest(task="就远程办公给出正反观点并权衡结论", task_type="decision"),
        ):
            decision = self.layer.select_topology(
                self.layer.profile_task(request), request.budget
            )
            self.assertEqual("decentralized", decision.topology)

    def test_difficulty_only_changes_capacity_not_risk(self):
        governance = benchmark_governance({
            "task_id": "creative-hard", "category": "creative_writing",
            "difficulty": "hard",
        })
        self.assertNotIn("risk_level", governance)
        self.assertEqual(12, governance["budget"]["max_calls"])

    def test_force_topology_supports_paired_experiments(self):
        request = TaskRequest(task="提出多个创意方案", task_type="creative")
        profile = self.layer.profile_task(request)
        decision = self.layer.select_topology(
            profile, request.budget, force_topology="centralized"
        )
        self.assertEqual("centralized", decision.topology)
        self.assertIn("forced centralized", decision.reason)
        coding = TaskRequest(task="实现一个排序函数", task_type="coding")
        forced_peer = self.layer.select_topology(
            self.layer.profile_task(coding), coding.budget,
            force_topology="decentralized",
        )
        self.assertEqual("decentralized", forced_peer.topology)

    def test_balanced_probe_routes_to_expected_topologies(self):
        probes = json.loads(
            (ROOT / "data" / "TopologyRoutingProbe.json").read_text(encoding="utf-8")
        )
        expected_counts = {"centralized": 0, "decentralized": 0}
        for probe in probes:
            expected_counts[probe["expected_topology"]] += 1
            request = TaskRequest(
                task=probe["task"], task_type=probe["task_type"],
                risk_level=probe.get("risk_level"),
            )
            decision = self.layer.select_topology(
                self.layer.profile_task(request), request.budget
            )
            self.assertEqual(
                probe["expected_topology"], decision.topology, probe["task_id"]
            )
        self.assertEqual(
            expected_counts["centralized"], expected_counts["decentralized"]
        )

    def test_math_scoring_prefers_labelled_final_result(self):
        answer = "1. 个位计算：8 + 4 = 12\n2. 十位计算完成\n### 结果\n42"
        self.assertTrue(score_math(answer, "答案：42"))
        self.assertTrue(score_math("6/36 = 1/6", "答案：1/6"))


class GovernanceTests(unittest.TestCase):
    def make_governor(self, **overrides):
        budget = ResourceBudget(**overrides)
        return RuntimeGovernor(budget, CommunicationPolicy(), NullPlatformAdapter(), "test")

    def test_call_budget_is_checked_before_external_work(self):
        governor = self.make_governor(max_calls=1)
        reservation = governor.before_call("hello", None, 20)
        governor.after_call(reservation, "world")
        with self.assertRaises(GovernanceLimit):
            governor.before_call("second", None, 20)
        self.assertEqual("call_budget", governor.stop_reason)

    def test_duplicate_and_decided_item_reentry_are_rejected(self):
        governor = self.make_governor()
        first = patterns.Message("a", "批准方案 A", "verdict", {"decision_key": "plan"})
        self.assertTrue(governor.evaluate_message(first)["accepted"])

        duplicate = patterns.Message("b", "批准方案 A", "proposal", {})
        self.assertFalse(governor.evaluate_message(duplicate)["accepted"])

        reopen = patterns.Message("c", "改为方案 B", "verdict", {"decision_key": "plan"})
        verdict = governor.evaluate_message(reopen)
        self.assertFalse(verdict["accepted"])
        self.assertEqual("decided_item_locked", verdict["reason"])


class EndToEndControlTests(unittest.TestCase):
    @staticmethod
    def fake_llm(prompt, **kwargs):
        if "打质量分" in prompt:
            return "0.9"
        if "独立验收员" in prompt:
            return '{"passed": true, "reason": "覆盖要求且无明显错误", "evidence": ["独立复核"]}'
        if "仲裁综合者" in prompt:
            return "最终方案：先盘点依赖，再灰度迁移，并设置回滚门槛。"
        if "根控制器" in prompt:
            return "最终迁移方案：先盘点依赖，再灰度迁移，并设置回滚门槛和验收指标。"
        if "逻辑严密" in prompt:
            return "方案一：盘点依赖和验收指标。"
        if "反面质疑" in prompt:
            return "方案二：补充回滚条件和故障演练。"
        if "可验证性" in prompt:
            return "方案三：用灰度流量和监控指标验证。"
        if "整体与长期" in prompt:
            return "方案四：安排分阶段迁移和业务连续性检查。"
        return "可验证的候选结果"

    def test_complete_run_has_verification_termination_and_audit(self):
        request = {
            "task_id": "migration-1",
            "task": "为客户数据系统制定迁移方案，分别覆盖步骤、风险与验收指标。",
            "task_type": "analysis",
            "risk_level": "medium",
            "budget": {"max_calls": 10, "max_steps": 40, "max_total_tokens": 12000,
                       "max_input_tokens": 9000, "max_output_tokens": 6000,
                       "max_cost_usd": 0.2},
            "completion_criteria": [{
                "name": "mentions-migration",
                "kind": "rule",
                "config": {"required_terms": ["迁移"]},
            }],
        }
        with patch.object(patterns, "_llm", side_effect=self.fake_llm):
            result = CollaborationControlLayer().execute(request)

        self.assertEqual("centralized", result["topology_decision"]["topology"])
        self.assertGreaterEqual(result["topology_decision"]["layers"], 2)
        self.assertEqual("completed", result["termination"]["status"])
        self.assertTrue(result["verification_report"]["passed"])
        self.assertGreater(result["budget_usage"]["calls"], 0)
        event_types = {event["event_type"] for event in result["audit_trail"]}
        self.assertIn("topology.selected", event_types)
        self.assertIn("verification.completed", event_types)
        self.assertIn("run.terminated", event_types)
        json.dumps(result, ensure_ascii=False)

    def test_adaptive_falls_back_to_alternate_topology_after_failed_gate(self):
        layer = CollaborationControlLayer()
        primary = {"final_result": "incomplete primary", "history": []}
        alternate = {"final_result": "complete alternate", "history": []}
        failed = VerificationReport(False, "failed", [
            VerificationCheck("independent_review", "independent_review",
                              False, True, "missing requirements")
        ], 1)
        passed = VerificationReport(True, "passed", [
            VerificationCheck("non_empty", "rule", True, True, "answer is present")
        ], 1)
        request = {
            "task": "提出一个包含多个步骤和验收指标的方案",
            "task_type": "analysis",
            "budget": {
                "max_calls": 10, "max_steps": 40,
                "max_input_tokens": 12000, "max_output_tokens": 8000,
                "max_total_tokens": 18000, "max_revisions": 1,
            },
        }

        with patch.object(layer, "_run_topology", side_effect=[primary, alternate]) as run, \
             patch.object(layer, "_verify", side_effect=[failed, passed]):
            result = layer.execute(request)

        self.assertEqual(2, run.call_count)
        self.assertEqual("complete alternate", result["final_result"])
        self.assertEqual("decentralized", result["topology_decision"]["topology"])
        self.assertTrue(result["verification_report"]["passed"])
        self.assertIn("topology.fallback", {
            event["event_type"] for event in result["audit_trail"]
        })


if __name__ == "__main__":
    unittest.main()
