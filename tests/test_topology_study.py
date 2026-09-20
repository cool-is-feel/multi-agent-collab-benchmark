import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from run_topology_study import (  # noqa: E402
    InvalidStudyRun,
    choose_observed_winner,
    exact_mcnemar_p,
    run_arm,
    summarize,
)
import collab_patterns as patterns  # noqa: E402


class TopologyStudyTests(unittest.TestCase):
    def test_empty_content_uses_provider_reasoning_without_extra_call(self):
        response = SimpleNamespace(
            content="",
            additional_kwargs={"reasoning_content": "计算后可知结果是 42。"},
            response_metadata={},
        )

        class FakeChatOpenAI:
            def __init__(self, **kwargs):
                pass

            def invoke(self, prompt):
                return response

        with (
            patch.object(patterns, "ChatOpenAI", FakeChatOpenAI),
            patch.object(patterns._REQUEST_RATE_LIMITER, "acquire"),
        ):
            answer = patterns._llm("求 6 * 7", model="test-model", max_retries=1)

        self.assertEqual("计算后可知结果是 42。", answer)

    def test_reasoning_can_be_read_from_message_attribute(self):
        message = SimpleNamespace(
            content="", reasoning="draft", additional_kwargs={}, response_metadata={}
        )
        self.assertEqual("draft", patterns._reasoning_text(message))

    def test_reasoning_can_be_read_from_raw_openai_response(self):
        message = SimpleNamespace(content="", reasoning_content="raw draft")
        response = SimpleNamespace(choices=[SimpleNamespace(message=message)])
        self.assertEqual("raw draft", patterns._reasoning_text(response))

    def test_winner_prefers_correctness_before_cost(self):
        rows = {
            "centralized": {"correct": True, "token_total": 1000, "elapsed": 8},
            "decentralized": {"correct": False, "token_total": 100, "elapsed": 1},
        }
        self.assertEqual("centralized", choose_observed_winner(rows))

    def test_summary_reports_paired_gain_and_routing_accuracy(self):
        rows = []
        for task_id, selected, central_ok, decentral_ok, single_ok in (
            ("c", "centralized", True, False, False),
            ("d", "decentralized", False, True, True),
        ):
            for arm, correct in (
                ("single", single_ok), ("centralized", central_ok),
                ("decentralized", decentral_ok), ("adaptive", True),
            ):
                rows.append({
                    "task_id": task_id, "repeat": 1, "arm": arm,
                    "correct": correct, "token_total": 100, "elapsed": 1,
                    "selected_topology": selected if arm == "adaptive" else arm,
                })
        report = summarize(rows)
        self.assertEqual(1.0, report["routing_accuracy"])
        self.assertEqual(0.5, report["adaptive_gain_vs_single"])
        self.assertTrue(report["acceptance"]["both_topologies_observed"])

    def test_exact_mcnemar_value_is_bounded(self):
        self.assertEqual(1.0, exact_mcnemar_p(0, 0))
        self.assertLessEqual(exact_mcnemar_p(8, 0), 0.05)

    def test_provider_error_is_not_scored_as_wrong_answer(self):
        class FakeCollab:
            @staticmethod
            def run(*args, **kwargs):
                return {"final_result": "ERROR: rate limited", "mechanism": "single"}

        task = {
            "task_id": "bad", "category": "math", "difficulty": "easy",
            "task_type": "computation", "expected_topology": "centralized",
            "task": "1 + 1", "expected_output": "答案：2",
        }
        with self.assertRaises(InvalidStudyRun):
            run_arm(FakeCollab(), task, "single")


if __name__ == "__main__":
    unittest.main()
