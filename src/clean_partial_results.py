# -*- coding: utf-8 -*-
"""清掉因 DeepSeek 余额不足(402)导致的脏数据，保留有效部分，供充值后续跑。

用法（com/ 下）:
    ./myenv/Scripts/python.exe src/clean_partial_results.py data/benchmark_lg_results.json

规则：
  1. 删除 final_result 以 "ERROR:" 开头的行（余额不足/超时导致生成失败，无价值）；
  2. 非 math 类别里 judge 失败（judge error）的行，剥掉 correct/correct_rule/judge_reason，
     让续跑时重新评分——答案有效则不再重新生成，省 token 省钱。
"""
import json
import sys
from pathlib import Path


def main():
    if len(sys.argv) < 2:
        print("用法: python clean_partial_results.py <results.json>")
        sys.exit(1)
    path = Path(sys.argv[1])
    rows = json.load(open(path, encoding="utf-8"))
    n0 = len(rows)
    kept = []
    n_err = 0
    n_rejudge = 0
    for r in rows:
        fr = str(r.get("final_result", ""))
        if fr.startswith("ERROR:"):
            n_err += 1
            continue
        if r.get("category") != "math" and "judge error" in str(r.get("judge_reason", "")):
            n_rejudge += 1
            for k in ("correct", "correct_rule", "judge_reason"):
                r.pop(k, None)
        kept.append(r)
    json.dump(kept, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"原 {n0} 行 -> 保留 {len(kept)} 行；删除 ERROR {n_err} 行，待重评 {n_rejudge} 行")


if __name__ == "__main__":
    main()
