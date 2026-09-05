# -*- coding: utf-8 -*-
"""collab_langgraph.py 冒烟测试。

默认离线（不调用 LLM）：验证 5 个图能编译、run() 对各类题型返回完整契约字段。
--live 会真实调用一次模型验证连通性（需 .env 里的 API Key）。

用法（在 src/ 下）:
    python test_collab_langgraph.py            # 离线结构 + 契约
    python test_collab_langgraph.py --live     # 再加一次真实调用
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CONTRACT = ["final_result", "mechanism", "confidence", "consensus_reached",
            "agreement", "llm_calls", "token_input", "token_output", "token_total",
            "elapsed", "models_used", "history"]


def load():
    spec = importlib.util.spec_from_file_location("collab_langgraph", ROOT / "collab_langgraph.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["collab_langgraph"] = m
    spec.loader.exec_module(m)
    return m


def test_structure(m):
    for mode in m._BUILDERS:
        g = m._get_graph(mode)
        assert g is not None, f"{mode} 图编译失败"
        print(f"  [ok] graph {mode:16} nodes={len(g.get_graph().nodes)}")


def test_contract_offline(m):
    # monkeypatch LLM 调用，离线跑通全部拓扑（不发网络请求）
    def fake_chat(prompt, model=None, temperature=0.7, max_tokens=400):
        return "答案：0.5"

    def fake_parallel(prompts, models=None, temperatures=None):
        return ["答案：0.5" for _ in prompts]

    m._chat = fake_chat
    m._parallel_chat = fake_parallel

    cases = [
        ("single", "1+1=?", "computation"),
        ("selfconsistency", "骰子和为 7 的概率", "computation"),
        ("centralized", "1+1=?", "computation"),
        ("decentralized", "写一句广告文案", "creative"),
        ("hierarchical", "分析远程办公对生产率的影响", "analysis"),
    ]
    for mode, task, tt in cases:
        r = m.run(mode, task, task_type=tt)
        missing = [k for k in CONTRACT if k not in r]
        assert not missing, f"{mode} 缺少字段 {missing}"
        assert isinstance(r["final_result"], str) and r["final_result"], f"{mode} final_result 为空"
        assert not r["final_result"].startswith("ERROR:"), f"{mode} 异常：{r['final_result']}"
        print(f"  [ok] {mode:16} -> {r['mechanism']:26} conf={r['confidence']} calls={r['llm_calls']}")


def test_live(m):
    r = m.run("single", "1+1 等于几？只回答数字。", task_type="computation")
    print(f"  [live] final_result={r['final_result'][:80]!r} elapsed={r['elapsed']}s")
    assert not r["final_result"].startswith("ERROR:"), f"真实调用失败：{r['final_result']}"


def main():
    live = "--live" in sys.argv
    m = load()
    print("结构检查：")
    test_structure(m)
    if live:
        print("真实调用检查：")
        test_live(m)   # 在 monkeypatch 之前跑
    print("契约检查（离线）：")
    test_contract_offline(m)
    print("\nALL SMOKE TESTS PASSED")


if __name__ == "__main__":
    main()
