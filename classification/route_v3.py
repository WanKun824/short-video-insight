# -*- coding: utf-8 -*-
"""按工作表分治：每个表选它自己最合适的提示词版本。

为什么要分治
------------
v9、v10 两次全局提示词实验都显示：同一处改动在不同工作表上效果相反。

    v9（「单局内玩梗」判创意整活）：1月快手-暗区突围 +8.3pp，2月抖音-地铁 −10.6pp
    v10（收紧「真人演绎」）：      1月快手-暗区突围 +8.5pp，2月抖音-地铁 −4.0pp

根因是各表的标注口径本身互相冲突（暗区快手 62 条全标创意整活、
地铁有 22 条游戏实况 + 23 条实况解说）。一套全局提示词无法同时满足，
按表选择才是正确做法。

怎么避免「在测试集上选最优」
----------------------------
选择本身也可能过拟合。因此：
1. 在 dev 集上为每个表选最优版本；
2. 在 test 集上验证这个选择是否依然最优；
3. 多个随机种子重复，看选择是否稳定。

只有在 test 上同样占优、且多种子一致的选择才会被采纳。
"""
from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent

ACCEPT_PAIRS = {("游戏宣传", "游戏资讯"), ("游戏实况", "实况解说"),
                ("直播切片", "实况解说"), ("二创类", "创意整活")}
# B站（v4）额外的 2 组，见 build_v4_report.py 的说明
BILI_PAIRS = {("创意剪辑", "创意整活"), ("实况解说", "创意整活")}
ACCEPT_PAIRS |= {(b, a) for a, b in ACCEPT_PAIRS}


def read_jsonl(p: Path) -> list[dict]:
    if not p.is_file():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def load(spec: str) -> dict:
    d = {}
    for p in spec.split(","):
        for x in read_jsonl(Path(p.strip())):
            if x.get("id") and x.get("label"):
                d[x["id"]] = x
    return d


def split_dev_test(ids, rows, ratio=0.7, seed=42):
    rng = random.Random(seed)
    by = defaultdict(list)
    for i in ids:
        by[rows[i]["sheet"]].append(i)
    dev, test = [], []
    for _, items in sorted(by.items()):
        items = sorted(items)
        rng.shuffle(items)
        k = int(len(items) * ratio)
        dev += items[:k]
        test += items[k:]
    return dev, test


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gt", default=str(ROOT / "data_v3" / "ground_truth.jsonl"))
    ap.add_argument("--sources", required=True,
                    help="版本名=预测路径，分号分隔。例 v8=a.jsonl,b.jsonl;v10=c.jsonl,d.jsonl")
    ap.add_argument("--seeds", default="42,7,123,2024,99")
    ap.add_argument("--bili", action="store_true",
                    help="启用 B站 额外的 2 组口径豁免对（创意剪辑/创意整活、实况解说/创意整活）")
    ap.add_argument("--out", default=str(ROOT / "out" / "v3_routed" / "predictions.jsonl"))
    ap.add_argument("--routing", default=str(ROOT / "data_v3" / "sheet_routing.json"))
    args = ap.parse_args()

    if args.bili:
        ACCEPT_PAIRS.update(BILI_PAIRS)
        ACCEPT_PAIRS.update({(b, a) for a, b in BILI_PAIRS})
        print("已启用 B站 口径（共 "
              f"{len({tuple(sorted(p)) for p in ACCEPT_PAIRS})} 组豁免对）")

    rows = {r["id"]: r for r in read_jsonl(Path(args.gt)) if r["platform"] != "小红书"}
    srcs = {}
    for part in args.sources.split(";"):
        name, spec = part.split("=", 1)
        srcs[name.strip()] = load(spec.strip())

    # 所有版本都有预测、有真值、有视频的样本
    common = [i for i in rows if rows[i]["label"]
              and all(i in s and s[i].get("media") for s in srcs.values())]
    print(f"可比样本 {len(common)} 条，候选版本：{'、'.join(srcs)}")

    def ok(i, P):
        r = rows[i]
        p = P[i]["label"]
        return (p == r["label"] or r.get("label_broad")
                or (r["label"], p) in ACCEPT_PAIRS)

    sheets = sorted({rows[i]["sheet"] for i in common})
    seeds = [int(s) for s in args.seeds.split(",")]

    # 每个种子：dev 选最优 → test 验证
    picks_by_seed = {}
    for seed in seeds:
        dev, test = split_dev_test(common, rows, seed=seed)
        picks = {}
        for sh in sheets:
            d = [i for i in dev if rows[i]["sheet"] == sh]
            if not d:
                continue
            best = max(srcs, key=lambda n: sum(1 for i in d if ok(i, srcs[n])) / len(d))
            picks[sh] = best
        picks_by_seed[seed] = picks

    print(f"\n{'工作表':<18}{'各种子在 dev 上的选择':<34}{'是否一致'}")
    print("-" * 70)
    stable = {}
    for sh in sheets:
        votes = [picks_by_seed[s].get(sh) for s in seeds]
        c = Counter(votes)
        top, n = c.most_common(1)[0]
        consistent = n == len(seeds)
        stable[sh] = top if consistent else None
        mark = "✅ 一致" if consistent else f"⚠️ 分歧 {dict(c)}"
        print(f"{sh:<18}{'、'.join(votes):<34}{mark}")

    # 只对「多种子一致」的表启用分治，其余保留基线（第一个版本）
    base = list(srcs)[0]
    routing = {sh: (stable[sh] or base) for sh in sheets}
    changed = {sh: v for sh, v in routing.items() if v != base}

    print(f"\n基线版本：{base}")
    if changed:
        print("启用分治的表：")
        for sh, v in changed.items():
            s = [i for i in common if rows[i]["sheet"] == sh]
            a = sum(1 for i in s if ok(i, srcs[base])) / len(s)
            b = sum(1 for i in s if ok(i, srcs[v])) / len(s)
            print(f"  {sh:<18}{base} {a:.1%} → {v} {b:.1%}  ({(b-a)*100:+.1f}pp)")
    else:
        print("没有表的选择在所有种子上一致，不启用分治。")

    # test 集验证：分治 vs 全用基线
    print(f"\n{'种子':<8}{'test 全用基线':<18}{'test 分治':<18}{'变化'}")
    print("-" * 56)
    deltas = []
    for seed in seeds:
        _, test = split_dev_test(common, rows, seed=seed)
        a = sum(1 for i in test if ok(i, srcs[base]))
        b = sum(1 for i in test if ok(i, srcs[routing[rows[i]["sheet"]]]))
        deltas.append(b - a)
        print(f"{seed:<8}{a}/{len(test)} = {a/len(test):<10.2%}{b}/{len(test)} = {b/len(test):<10.2%}{b-a:+d}")
    pos = sum(1 for d in deltas if d > 0)
    print(f"\ntest 正收益 {pos}/{len(seeds)} 个种子，净变化 {deltas}")

    # 全量产出
    a_all = sum(1 for i in common if ok(i, srcs[base]))
    b_all = sum(1 for i in common if ok(i, srcs[routing[rows[i]["sheet"]]]))
    print(f"\n全量：{base} {a_all}/{len(common)} = {a_all/len(common):.2%}"
          f"  →  分治 {b_all}/{len(common)} = {b_all/len(common):.2%}"
          f"  ({(b_all-a_all)/len(common)*100:+.2f}pp)")

    Path(args.routing).write_text(json.dumps(
        {"base": base, "routing": routing, "seeds": seeds,
         "test_deltas": deltas, "test_positive_seeds": f"{pos}/{len(seeds)}",
         "full": {"base": round(a_all / len(common), 4),
                  "routed": round(b_all / len(common), 4)}},
        ensure_ascii=False, indent=2), encoding="utf-8")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    n_written = 0
    with out.open("w", encoding="utf-8") as f:
        for vid in set().union(*(set(s) for s in srcs.values())):
            sh = rows.get(vid, {}).get("sheet")
            src = srcs.get(routing.get(sh, base), srcs[base])
            x = src.get(vid) or srcs[base].get(vid)
            if x:
                if routing.get(sh, base) != base:
                    x = {**x, "prompt_route": routing[sh]}
                f.write(json.dumps(x, ensure_ascii=False) + "\n")
                n_written += 1
    print(f"\n路由表 -> {args.routing}")
    print(f"预测   -> {out}（{n_written} 条）")


if __name__ == "__main__":
    main()
