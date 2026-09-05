# -*- coding: utf-8 -*-
"""循环调用 run_benchmark.py 直到全部题目生成+评分完毕。

benchmark 本身支持断点续跑；本 wrapper 在它被杀/退出后自动重启，直到 600 行且全部含 correct。
用法（com/ 下）:
    python src/run_until_done.py data/benchmark_lg_results.json
"""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


def done(out: Path) -> bool:
    if not out.exists():
        return False
    try:
        rows = json.load(open(out, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return False
    return len(rows) >= 600 and all("correct" in r for r in rows)


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else (ROOT / "data" / "benchmark_lg_results.json")
    cmd = [PY, str(ROOT / "src" / "run_benchmark.py"), "--lib", "collab_langgraph",
           "--out", str(out)]
    for i in range(1, 1000):
        print(f"[wrapper] round {i} start", flush=True)
        rc = subprocess.run(cmd).returncode
        print(f"[wrapper] round {i} exit={rc}", flush=True)
        if done(out):
            print("[wrapper] ALL DONE", flush=True)
            return
        print("[wrapper] 未完成，3 秒后重启", flush=True)
        time.sleep(3)


if __name__ == "__main__":
    main()
