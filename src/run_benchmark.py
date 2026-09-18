# -*- coding: utf-8 -*-
"""用 collab_patterns.py 跑 MultiAgentCollabBench：多 Agent 共识 vs 单 Agent 基线。

对每一题同时跑两种模式：
  - single      单 Agent 基线（对照组，一次 LLM 调用）
  - multi       动态控制层（按任务结构、风险、预算选择协作拓扑）

然后对每个答案做「正确性」评分：
  - math         规则评分（数值 1% 相对容差，客观精确）
  - 其它类别     LLM-as-Judge（对照 expected_output / 评估标准打 是/否）

缓存 / 复用：结果按 (task_id, mode) 增量落盘到 --out 文件；再次运行默认自动
跳过已完成的「答案」与「评分」，只补跑缺失部分，无需整轮重跑。

用法:
    python run_benchmark.py                          # 跑全部 200 题（自动续跑）
    python run_benchmark.py --limit 8                # 冒烟：只跑前 8 题
    python run_benchmark.py --category math          # 只跑某一类
    python run_benchmark.py --category math,code     # 多类（逗号分隔）
    python run_benchmark.py --per-category 3 --balanced   # 每类按难度均衡取 3 题
    python run_benchmark.py --force                  # 忽略缓存，全部重跑
    python run_benchmark.py --no-judge               # 只生成答案，不评分
    python run_benchmark.py --judge-only             # 只对已有答案评分
默认输出: ../data/benchmark_v2_results.json
"""
import argparse
import importlib.util
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Windows 控制台默认 GBK，打印含 CO₂ 等 Unicode 字符会崩溃；强制 UTF-8 输出。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")


def load_collab():
    """通过 importlib 加载 collab_patterns.py（多 Agent 协作库，与 benchmark 解耦）。"""
    spec = importlib.util.spec_from_file_location(
        "collab_patterns", ROOT / "src" / "collab_patterns.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["collab_patterns"] = m
    spec.loader.exec_module(m)
    return m


# 类别 → (多Agent协作模式, 题型)。题型与 collab_patterns.py 的 task_type 对齐。
CATEGORY_MAP = {
    "math":             ("centralized",   "computation"),
    "code":             ("centralized",   "coding"),
    "creative_writing": ("decentralized", "creative"),
    "translation":      ("decentralized", "translation"),
    "debate":           ("hierarchical",  "analysis"),
    "open_reasoning":   ("hierarchical",  "analysis"),
    "fact_checking":    ("hierarchical",  "analysis"),
    "planning":         ("hierarchical",  "analysis"),
    "science":          ("hierarchical",  "analysis"),
    "tool_use":         ("hierarchical",  "analysis"),
}


def build_task(q: dict) -> str:
    desc = q["description"]
    inp = (q.get("input") or "").strip()
    return desc + (f"\n\n输入：\n{inp}" if inp else "")


# 代码类中的「调试题」（定位 bug 并修复）属于代码推理，不该走「写代码」流水线。
DEBUG_KEYWORDS = ["报错", "崩溃", "竞态", "定位", "修复", "bug", "Bug"]


def resolve_mode(q: dict, adaptive: bool = True):
    """返回 (mode, task_type)；默认由控制层动态选择实际拓扑。"""
    mode, task_type = CATEGORY_MAP[q["category"]]
    if q["category"] == "code" and any(k in q.get("description", "") for k in DEBUG_KEYWORDS):
        mode, task_type = "hierarchical", "analysis"
    return ("adaptive" if adaptive else mode, task_type)


def benchmark_governance(q: dict) -> dict:
    """Translate benchmark metadata into platform-style risk and resource boundaries."""
    difficulty = q.get("difficulty", "medium")
    calls = {"easy": 8, "medium": 10, "hard": 12}.get(difficulty, 10)
    total_tokens = {"easy": 9000, "medium": 14000, "hard": 18000}.get(difficulty, 14000)
    return {
        "task_id": q["task_id"],
        "risk_level": {"easy": "low", "medium": "medium", "hard": "high"}.get(
            difficulty, "medium"
        ),
        "budget": {
            "max_calls": calls, "max_steps": calls * 4,
            "max_input_tokens": total_tokens, "max_output_tokens": total_tokens,
            "max_total_tokens": total_tokens, "max_seconds": 360,
            "max_cost_usd": 0.15, "max_revisions": 1,
        },
        "metadata": {"category": q["category"], "difficulty": difficulty},
    }


def _extract_value(text) -> float | None:
    """从文本提取数值。支持：分数 a/b、科学计数法 1.2e3、以及「=」后的答案部分。

    例：'6/36 = 1/6'→0.1667；'3/5（=0.6）'→0.6；'C(10,3)=120'→120；'1.158e+04'→11580。
    """
    s = str(text)
    if "=" in s:                       # 答案通常在等号后
        s = s.split("=")[-1]
    m = re.search(r'\d+\.?\d*[eE][+-]?\d+', s)   # 科学计数法
    if m:
        return float(m.group(0))
    m = re.search(r'(\d+)\s*/\s*(\d+)', s)        # 分数
    if m and int(m.group(2)) != 0:
        return int(m.group(1)) / int(m.group(2))
    m = re.search(r'\d+\.?\d*', s)                # 普通数字
    return float(m.group(0)) if m else None


def _first_number(text) -> float | None:
    return _extract_value(text)


def score_math(final_result: str, expected: str) -> bool | None:
    """数学题：数值结果与期望值做 1% 相对容差比较。"""
    a = _extract_value(final_result)
    b = _extract_value(expected)
    if a is None or b is None:
        return None
    return abs(a - b) <= max(1e-6, 0.01 * abs(b))


JUDGE_PROMPT = """你是严格的评分裁判。判断一个答案是否正确、合格地完成了任务。

【任务】{task}
【参考答案/评估标准】{expected}
【待评答案】{answer}

评分规则：
1. 先从「参考答案/评估标准」中提取所有关键要点/要求（指令性动作、必要维度、量化指标等）。
2. 逐条核对「待评答案」是否覆盖这些关键要点。
3. 判定原则：
   - 只要覆盖了关键要点、且没有事实错误、结论错误，即判「是」；
   - 表达方式、详略程度、排版/格式差异【不影响】判断；
   - 只有当答案遗漏了关键要点、或有事实/结论错误时，才判「否」；
   - 若评估标准用「或」连接多个可选要点（如「A或B」），答案满足其中一个即可判「是」。
4. 不要因为答案比参考答案更简洁就判错，也不要因为更详细就判错——只看【关键要点是否覆盖、结论是否正确】。

只输出两行，不要输出其它内容：
正确性：是/否
理由：<一句话>"""


# 代码类专用评分：聚焦【功能正确性】，命名/类名/大小写/注释/格式等外观差异不扣分。
CODE_JUDGE_PROMPT = """你是代码评审，判断代码是否在【功能上】正确实现了任务要求。

【任务】{task}
【参考答案】{expected}
【待评代码】{answer}

判断标准：只判断功能是否正确（是否实现了要求的功能/算法/边界处理，是否满足显式约束）。
类名/变量名/大小写/注释/代码格式等外观差异【不影响】判断；只要功能等价即可判「是」。
若代码存在逻辑错误、未实现要求的功能、或违反显式约束（如要求不用 sum 却用了 sum），判「否」。
只输出两行，不要输出其它内容：
正确性：是/否
理由：<一句话>"""


def judge(collab, q: dict, answer: str) -> tuple[bool, str]:
    """LLM-as-Judge 对答案打正确性（是/否）。返回 (correct, reason)。"""
    task = build_task(q)
    tmpl = CODE_JUDGE_PROMPT if q["category"] == "code" else JUDGE_PROMPT
    prompt = tmpl.format(task=task, expected=q["expected_output"], answer=answer[:2000])
    try:
        r = collab._chat(prompt, temperature=0.0)
    except Exception as e:  # noqa: BLE001
        return False, f"judge error: {e}"
    m = re.search(r"正确性[：:]\s*(是|否|对|错|正确|错误|yes|no)", r, re.IGNORECASE)
    correct = bool(m) and m.group(1).lower() in ("是", "对", "正确", "yes")
    reason_m = re.search(r"理由[：:]\s*(.+)", r)
    reason = reason_m.group(1).strip()[:300] if reason_m else r[:300]
    return correct, reason


def make_answer_row(q, mode, mode_name, task_type, r) -> dict:
    row = {
        "task_id": q["task_id"],
        "category": q["category"],
        "difficulty": q["difficulty"],
        "mode": mode,
        "mode_name": mode_name,      # single / multi
        "task_type": task_type,
        "num_agents_suggested": q.get("num_agents_suggested"),
        "collaboration_type": q.get("collaboration_type"),
        "final_result": r.get("final_result", ""),
        "expected_output": q["expected_output"],
        "mechanism": r.get("mechanism"),
        "confidence": r.get("confidence"),
        "agreement": r.get("agreement"),
        "consensus_reached": r.get("consensus_reached"),
        "llm_calls": r.get("llm_calls"),
        "token_total": r.get("token_total"),
        "elapsed": r.get("elapsed"),
    }
    if mode == "adaptive":
        row.update({
            "selected_topology": (r.get("topology_decision") or {}).get("topology"),
            "topology_decision": r.get("topology_decision"),
            "task_profile": r.get("task_profile"),
            "team": r.get("team"),
            "verification_report": r.get("verification_report"),
            "termination": r.get("termination"),
            "budget_usage": r.get("budget_usage"),
            "audit_trail": r.get("audit_trail", []),
            "history": r.get("history", []),
        })
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 题")
    ap.add_argument("--category", type=str, default="", help="只跑指定类别（逗号分隔）")
    ap.add_argument("--per-category", type=int, default=0, help="每类取前 N 题")
    ap.add_argument("--balanced", action="store_true", help="与 --per-category 配合：每类按难度均衡取样")
    ap.add_argument("--force", action="store_true", help="忽略缓存，全部重跑")
    ap.add_argument("--no-judge", action="store_true", help="只生成答案，不评分")
    ap.add_argument("--judge-only", action="store_true", help="只对已有答案评分（不生成新答案）")
    ap.add_argument("--out", type=str, default="", help="结果输出路径")
    ap.add_argument("--fixed-topology", action="store_true",
                    help="兼容旧实验：按类别使用固定拓扑，不启用动态控制层")
    args = ap.parse_args()

    collab = load_collab()
    data = json.load(open(ROOT / "data" / "MultiAgentCollabBench.json", encoding="utf-8"))

    cats = [c.strip() for c in args.category.split(",") if c.strip()] if args.category else []
    selected = [q for q in data if (not cats or q["category"] in cats)]
    if args.per_category:
        if args.balanced:
            buckets = defaultdict(lambda: defaultdict(list))
            for q in selected:
                buckets[q["category"]][q["difficulty"]].append(q)
            tmp = []
            for cat in sorted(buckets):
                diffs = ["easy", "medium", "hard"]
                taken = 0
                while taken < args.per_category:
                    progressed = False
                    for d in diffs:
                        if taken < args.per_category and buckets[cat][d]:
                            tmp.append(buckets[cat][d].pop(0))
                            taken += 1
                            progressed = True
                    if not progressed:
                        break
            selected = tmp
        else:
            seen = defaultdict(int)
            tmp = []
            for q in selected:
                if seen[q["category"]] < args.per_category:
                    tmp.append(q)
                    seen[q["category"]] += 1
            selected = tmp
    if args.limit:
        selected = selected[: args.limit]

    out = Path(args.out) if args.out else (ROOT / "data" / "benchmark_v2_results.json")
    out.parent.mkdir(parents=True, exist_ok=True)

    # 载入已有结果（按 (task_id, mode) 建索引，支持断点续跑）
    index: dict[tuple, dict] = {}
    if out.exists():
        for row in json.load(open(out, encoding="utf-8")):
            index[(row["task_id"], row["mode"])] = row
    if args.force:
        index = {}

    print(f"题目数: {len(selected)}  已有结果: {len(index)}  模式: single + multi")
    if args.judge_only:
        # 只评分：遍历已生成答案的行
        todo = [r for r in index.values() if "correct" not in r]
        print(f"待评分: {len(todo)}")
    else:
        # 生成答案：对每个题目跑 single + multi
        todo_answers = []
        for q in selected:
            mode, task_type = resolve_mode(q, adaptive=not args.fixed_topology)
            for m, mname, tt in [("single", "single", task_type), (mode, "multi", task_type)]:
                if (q["task_id"], m) in index:
                    continue
                todo_answers.append((q, m, mname, tt))
        print(f"待生成答案: {len(todo_answers)}")

        for i, (q, m, mname, tt) in enumerate(todo_answers, 1):
            task = build_task(q)
            t0 = time.time()
            try:
                governance = benchmark_governance(q) if m == "adaptive" else {}
                r = collab.run(m, task, task_type=tt, **governance)
            except Exception as e:  # noqa: BLE001
                r = {"final_result": f"ERROR: {e}", "mechanism": m, "confidence": 0.0,
                     "consensus_reached": False, "agreement": 0.0, "llm_calls": 0,
                     "token_total": 0, "elapsed": time.time() - t0}
            # 多 Agent 模式失败（如推理型模型偶发返回空内容）→ 重试一次，仍失败则回退单 Agent 基线（优雅降级）
            if mname == "multi" and str(r.get("final_result", "")).startswith("ERROR:"):
                try:
                    governance = benchmark_governance(q) if m == "adaptive" else {}
                    r = collab.run(m, task, task_type=tt, **governance)
                except Exception:  # noqa: BLE001
                    pass
                if str(r.get("final_result", "")).startswith("ERROR:"):
                    r = collab.run("single", task, task_type=tt)
                    r["mechanism"] = f"{r.get('mechanism', 'single')}→fallback"
            row = make_answer_row(q, m, mname, tt, r)
            index[(q["task_id"], m)] = row
            print(f"[{i}/{len(todo_answers)}] {q['task_id']:<20} {mname:<6} {row['mechanism']:<26} "
                  f"conf={row['confidence']} agree={row['agreement']} calls={row['llm_calls']} "
                  f"t={row['elapsed']}s", flush=True)
            # 增量落盘
            with open(out, "w", encoding="utf-8") as f:
                json.dump(sorted(index.values(), key=lambda x: (x["task_id"], x["mode"])),
                          f, ensure_ascii=False, indent=2)

    # 评分阶段（可单独 --judge-only 或紧随答案生成）
    if not args.no_judge:
        todo_judge = [r for r in index.values() if "correct" not in r]
        for i, row in enumerate(todo_judge, 1):
            q = {"task_id": row["task_id"], "category": row["category"],
                 "description": "", "input": "", "expected_output": row["expected_output"]}
            # 找回原始题目描述（用于 judge）
            for d in data:
                if d["task_id"] == row["task_id"]:
                    q = d
                    break
            if row["category"] == "math":
                correct = score_math(row["final_result"], row["expected_output"])
                reason = "规则评分（数值容差 1%）" if correct is not None else "无法解析数值"
                correct = bool(correct)
            else:
                correct, reason = judge(collab, q, row["final_result"])
            row["correct"] = correct
            row["correct_rule"] = bool(score_math(row["final_result"], row["expected_output"])) \
                if row["category"] == "math" else None
            row["judge_reason"] = reason
            print(f"[judge {i}/{len(todo_judge)}] {row['task_id']:<20} {row['mode']:<6} "
                  f"correct={correct}  {reason[:60]}", flush=True)
            with open(out, "w", encoding="utf-8") as f:
                json.dump(sorted(index.values(), key=lambda x: (x["task_id"], x["mode"])),
                          f, ensure_ascii=False, indent=2)

    with open(out, "w", encoding="utf-8") as f:
        json.dump(sorted(index.values(), key=lambda x: (x["task_id"], x["mode"])),
                  f, ensure_ascii=False, indent=2)
    print(f"\n已写入 {len(index)} 条结果 -> {out}")

    # 汇总：单 vs 多 正确率
    scored = [r for r in index.values() if "correct" in r]
    if scored:
        def acc(rows):
            return sum(1 for r in rows if r.get("correct")) / len(rows) if rows else 0.0
        by = defaultdict(lambda: defaultdict(list))
        for r in scored:
            by[r["category"]][r["mode_name"]].append(r)
        print("\n=== 汇总：正确率（多 Agent vs 单 Agent） ===")
        print(f"{'类别':<18}{'n':>4}{'单Agent':>9}{'多Agent':>9}{'增益':>8}")
        for c in sorted(by):
            s = acc(by[c]["single"])
            m = acc(by[c]["multi"])
            n = len(by[c]["single"])
            print(f"{c:<18}{n:>4}{s:>8.0%}{m:>9.0%}{m-s:>+8.0%}")
        s_all = acc([r for r in scored if r["mode_name"] == "single"])
        m_all = acc([r for r in scored if r["mode_name"] == "multi"])
        print(f"{'TOTAL':<18}{len(by):>4}{s_all:>8.0%}{m_all:>9.0%}{m_all-s_all:>+8.0%}")


if __name__ == "__main__":
    main()
