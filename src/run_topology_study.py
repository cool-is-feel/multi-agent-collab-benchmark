# -*- coding: utf-8 -*-
"""Paired topology study for the adaptive router.

Each probe is evaluated with the same model and budget in four arms:
single, forced centralized, forced decentralized, and adaptive.  The paired
design measures routing quality without confusing task mix with topology.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from run_benchmark import benchmark_governance, judge, load_collab, score_math  # noqa: E402


ARMS = ("single", "centralized", "decentralized", "adaptive")
STUDY_VERSION = 5


class InvalidStudyRun(RuntimeError):
    """A provider or judge failure that must not be treated as a wrong answer."""


def score_answer(collab, task: dict, answer: str) -> tuple[bool, str]:
    if task.get("category") == "math":
        correct = bool(score_math(answer, task["expected_output"]))
        return correct, "rule score: numeric tolerance 1%"
    benchmark_task = {
        "task_id": task["task_id"], "category": task["category"],
        "description": task["task"], "input": "",
        "expected_output": task["expected_output"],
    }
    return judge(collab, benchmark_task, answer)


def run_arm(collab, task: dict, arm: str) -> dict:
    started = time.time()
    if arm == "single":
        result = collab.run("single", task["task"], task_type=task["task_type"])
    else:
        governance = benchmark_governance(task)
        governance["risk_level"] = task.get("risk_level", "low")
        if arm in ("centralized", "decentralized"):
            governance["force_topology"] = arm
        result = collab.run(
            "adaptive", task["task"], task_type=task["task_type"], **governance
        )
    answer = str(result.get("final_result", ""))
    termination = result.get("termination") or {}
    if answer.startswith("ERROR:"):
        raise InvalidStudyRun(f"{task['task_id']} {arm}: {answer[:200]}")
    correct, reason = score_answer(collab, task, answer)
    if str(reason).startswith("judge error:"):
        raise InvalidStudyRun(f"{task['task_id']} {arm}: {reason[:200]}")
    return {
        "task_id": task["task_id"], "category": task["category"],
        "difficulty": task.get("difficulty", "medium"), "arm": arm,
        "model": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        "study_version": STUDY_VERSION,
        "expected_topology": task["expected_topology"],
        "selected_topology": (result.get("topology_decision") or {}).get("topology"),
        "final_result": answer, "expected_output": task["expected_output"],
        "correct": correct, "judge_reason": reason,
        "llm_calls": result.get("llm_calls", 0),
        "token_total": result.get("token_total", 0),
        "elapsed": result.get("elapsed", round(time.time() - started, 3)),
        "mechanism": result.get("mechanism"),
        "topology_decision": result.get("topology_decision"),
        "verification_report": result.get("verification_report"),
        "termination": result.get("termination"),
        "provider_degraded": termination.get("status") == "execution_error",
    }


def choose_observed_winner(rows: dict[str, dict]) -> str:
    """Correctness first; use tokens then latency only when correctness ties."""
    candidates = ["centralized", "decentralized"]
    return min(
        candidates,
        key=lambda arm: (
            not bool(rows[arm].get("correct")),
            rows[arm].get("token_total") or float("inf"),
            rows[arm].get("elapsed") or float("inf"),
        ),
    )


def exact_mcnemar_p(adaptive_only: int, single_only: int) -> float:
    """Two-sided exact McNemar/binomial p-value for paired binary outcomes."""
    discordant = adaptive_only + single_only
    if not discordant:
        return 1.0
    tail = sum(math.comb(discordant, k) for k in range(min(adaptive_only, single_only) + 1))
    return min(1.0, 2.0 * tail / (2 ** discordant))


def summarize(rows: list[dict]) -> dict:
    by_run: dict[tuple[str, int], dict[str, dict]] = defaultdict(dict)
    for row in rows:
        by_run[(row["task_id"], int(row["repeat"]))][row["arm"]] = row

    complete = [arms for arms in by_run.values() if all(arm in arms for arm in ARMS)]
    accuracy = {
        arm: sum(bool(x[arm].get("correct")) for x in complete) / len(complete)
        if complete else 0.0
        for arm in ARMS
    }
    winners = [choose_observed_winner(x) for x in complete]
    routing_hits = sum(x["adaptive"].get("selected_topology") == winner
                       for x, winner in zip(complete, winners))
    adaptive_wins = sum(x["adaptive"].get("correct") and not x["single"].get("correct")
                        for x in complete)
    single_wins = sum(x["single"].get("correct") and not x["adaptive"].get("correct")
                      for x in complete)
    routing_mismatches = [
        {
            "task_id": x["adaptive"]["task_id"],
            "repeat": x["adaptive"]["repeat"],
            "selected": x["adaptive"].get("selected_topology"),
            "observed_winner": winner,
        }
        for x, winner in zip(complete, winners)
        if x["adaptive"].get("selected_topology") != winner
    ]
    provider_degraded = {
        arm: sum(bool(x[arm].get("provider_degraded")) for x in complete)
        for arm in ARMS
    }
    # Repeats of one task are correlated. Aggregate each arm by task before
    # significance testing so repeated sampling cannot inflate sample size.
    by_task: dict[str, list[dict[str, dict]]] = defaultdict(list)
    for arms in complete:
        by_task[arms["adaptive"]["task_id"]].append(arms)
    task_adaptive_wins = 0
    task_single_wins = 0
    for task_runs in by_task.values():
        threshold = len(task_runs) // 2 + 1
        adaptive_ok = sum(bool(x["adaptive"].get("correct")) for x in task_runs) >= threshold
        single_ok = sum(bool(x["single"].get("correct")) for x in task_runs) >= threshold
        task_adaptive_wins += int(adaptive_ok and not single_ok)
        task_single_wins += int(single_ok and not adaptive_ok)
    p_value = exact_mcnemar_p(task_adaptive_wins, task_single_wins)
    return {
        "complete_pairs": len(complete),
        "accuracy": {k: round(v, 4) for k, v in accuracy.items()},
        "adaptive_gain_vs_single": round(accuracy["adaptive"] - accuracy["single"], 4),
        "routing_accuracy": round(routing_hits / len(complete), 4) if complete else 0.0,
        "observed_best_topology": dict(Counter(winners)),
        "adaptive_selected_topology": dict(Counter(
            x["adaptive"].get("selected_topology") for x in complete
        )),
        "paired_outcomes": {
            "adaptive_only_correct": adaptive_wins,
            "single_only_correct": single_wins,
            "net_wins": adaptive_wins - single_wins,
            "task_level_adaptive_only_correct": task_adaptive_wins,
            "task_level_single_only_correct": task_single_wins,
            "mcnemar_exact_p": round(p_value, 6),
        },
        "routing_mismatches": routing_mismatches,
        "provider_degraded_runs": provider_degraded,
        "acceptance": {
            "adaptive_beats_single": accuracy["adaptive"] > accuracy["single"],
            "adaptive_gain_significant_5pct": (
                accuracy["adaptive"] > accuracy["single"] and p_value <= 0.05
            ),
            "router_at_least_75pct": (routing_hits / len(complete) >= 0.75)
            if complete else False,
            "both_topologies_observed": len(set(winners)) == 2,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default=str(ROOT / "data" / "TopologyRoutingProbe.json"))
    parser.add_argument("--out", default=str(ROOT / "data" / "topology_study_results.json"))
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--summary", default=str(ROOT / "data" / "topology_study_summary.json"))
    parser.add_argument("--arm-attempts", type=int, default=2)
    args = parser.parse_args()

    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    if args.arm_attempts < 1:
        parser.error("--arm-attempts must be at least 1")
    tasks = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = json.loads(out.read_text(encoding="utf-8")) if out.exists() else []
    reusable = [
        r for r in rows
        if r.get("study_version") == STUDY_VERSION
        and not str(r.get("final_result", "")).startswith("ERROR:")
        and not str(r.get("judge_reason", "")).startswith("judge error:")
    ]
    index = {(r["task_id"], int(r["repeat"]), r["arm"]): r for r in reusable}
    collab = load_collab()

    total = len(tasks) * args.repeats * len(ARMS)
    for repeat in range(1, args.repeats + 1):
        for task in tasks:
            for arm in ARMS:
                key = (task["task_id"], repeat, arm)
                if key in index:
                    continue
                row = None
                for attempt in range(1, args.arm_attempts + 1):
                    try:
                        row = run_arm(collab, task, arm)
                        break
                    except InvalidStudyRun as exc:
                        print(f"invalid run attempt {attempt}/{args.arm_attempts}: {exc}",
                              flush=True)
                        if attempt < args.arm_attempts:
                            time.sleep(15)
                if row is None:
                    raise RuntimeError(
                        f"{task['task_id']} {arm} failed {args.arm_attempts} complete attempts; "
                        "no invalid result was cached, so rerun the same command to resume"
                    )
                row["repeat"] = repeat
                index[key] = row
                out.write_text(json.dumps(list(index.values()), ensure_ascii=False, indent=2),
                               encoding="utf-8")
                print(f"[{len(index)}/{total}] {task['task_id']} r{repeat} {arm} "
                      f"correct={row['correct']} topology={row['selected_topology']}", flush=True)

    final_rows = sorted(index.values(), key=lambda x: (x["task_id"], x["repeat"], x["arm"]))
    out.write_text(json.dumps(final_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    report = summarize(final_rows)
    Path(args.summary).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
