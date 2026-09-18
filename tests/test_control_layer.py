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
)


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


if __name__ == "__main__":
    unittest.main()
