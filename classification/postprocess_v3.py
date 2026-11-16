# -*- coding: utf-8 -*-
"""v3 规则复核层：基于话题标签的高精度后处理。

设计原则
--------
1. 规则**只读作品描述里的话题标签**，不读 Excel 真值，也不读模型的预测理由。
2. 规则从 dev 集挖掘（`--mine`），在 test 集上验证（`--eval`），避免 in-sample 自欺。
3. 只在「话题先验足够纯 + 样本量足够」时覆盖模型预测；其余一律保留模型结果。

为什么用规则而不是改提示词
--------------------------
v9 实验（把「单局内玩梗」从游戏实况改判创意整活）在全量上是负收益：
业务口径 74.83% → 71.56%，修复 31 打坏 45。原因是各工作表口径互相冲突——
「1月快手-暗区突围」62 条全是创意整活（v9 +8.3pp），而「2月抖音-地铁」
有 22 条游戏实况 + 23 条实况解说（v9 −10.6pp）。
一套全局提示词无法同时满足，按话题/作者做局部规则更合适。
"""
from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TAG_RE = re.compile(r"#([^#\s]+)")

# 业务口径（与 build_v3_report.py 保持一致）
ACCEPT_PAIRS: set[tuple[str, str]] = {
    ("游戏宣传", "游戏资讯"), ("游戏实况", "实况解说"),
    ("直播切片", "实况解说"), ("二创类", "创意整活"),
}
ACCEPT_PAIRS |= {(b, a) for a, b in ACCEPT_PAIRS}


def read_jsonl(p: Path) -> list[dict]:
    if not p.is_file():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def tags_of(desc: str) -> set[str]:
    return set(TAG_RE.findall(desc or ""))


def keys_of(row: dict, kind: str) -> set[str]:
    """规则的匹配键：话题标签或作者名。都只来自作品元数据，不涉及真值。"""
    if kind == "tag":
        return tags_of(row["desc"])
    a = (row.get("author") or "").strip()
    return {a} if a else set()


def split_dev_test(rows: list[dict], ratio: float = 0.7, seed: int = 42):
    """按工作表分层划分，保证每个表的口径都在两边出现。"""
    rng = random.Random(seed)
    by = defaultdict(list)
    for r in rows:
        by[r["sheet"]].append(r)
    dev, test = [], []
    for _, items in sorted(by.items()):
        items = sorted(items, key=lambda x: x["id"])
        rng.shuffle(items)
        k = int(len(items) * ratio)
        dev += items[:k]
        test += items[k:]
    return dev, test


def mine_rules(dev: list[dict], min_n: int = 4, min_purity: float = 0.9,
               kinds: tuple[str, ...] = ("tag",)) -> list[dict]:
    """在 dev 上挖掘高纯度规则。kinds 可含 tag / author。"""
    rules = []
    for kind in kinds:
        k2l = defaultdict(list)
        for r in dev:
            for k in keys_of(r, kind):
                k2l[k].append(r["label"])
        for k, ls in k2l.items():
            if len(ls) < min_n:
                continue
            c = Counter(ls)
            top, n = c.most_common(1)[0]
            if n / len(ls) >= min_purity:
                rules.append({"kind": kind, "key": k, "label": top, "hit": n,
                              "total": len(ls), "purity": round(n / len(ls), 3)})
    # 样本量大的优先；同量时作者规则优先（比话题更特异）
    rules.sort(key=lambda x: (-x["hit"], -x["purity"], x["kind"] != "author"))
    return rules


def apply_rules(row: dict, pred: str, rules: list[dict]) -> tuple[str, str]:
    """命中规则则覆盖预测。多条命中时取样本量最大的那条。"""
    hits = [r for r in rules if r["key"] in keys_of(row, r["kind"])]
    if not hits:
        return pred, ""
    best = max(hits, key=lambda r: (r["hit"], r["purity"], r["kind"] == "author"))
    if best["label"] == pred:
        return pred, ""
    sign = "#" if best["kind"] == "tag" else "@"
    return best["label"], (f"rule:{sign}{best['key']}→{best['label']}"
                           f"（{best['hit']}/{best['total']}）")


def evaluate(rows: list[dict], preds: dict[str, str], rules: list[dict] | None = None):
    """返回 (业务口径正确数, 总数, 改动明细)。"""
    n_ok = 0
    changes = []
    for r in rows:
        p = preds.get(r["id"])
        if not p:
            continue
        before = p
        reason = ""
        if rules:
            p, reason = apply_rules(r, p, rules)

        def ok(x):
            return (x == r["label"] or r.get("label_broad")
                    or (r["label"], x) in ACCEPT_PAIRS)

        if ok(p):
            n_ok += 1
        if reason:
            changes.append({"id": r["id"], "sheet": r["sheet"], "gt": r["label"],
                            "before": before, "after": p, "reason": reason,
                            "good": ok(p) and not ok(before),
                            "bad": ok(before) and not ok(p)})
    total = sum(1 for r in rows if preds.get(r["id"]))
    return n_ok, total, changes


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gt", default=str(ROOT / "data_v3" / "ground_truth.jsonl"))
    ap.add_argument("--preds", required=True)
    ap.add_argument("--rules", default=str(ROOT / "data_v3" / "topic_rules.json"))
    ap.add_argument("--min-n", type=int, default=4)
    ap.add_argument("--min-purity", type=float, default=0.9)
    ap.add_argument("--kinds", default="tag",
                    help="规则来源，逗号分隔：tag（话题标签）/ author（作者）")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--exclude-platform", default="小红书")
    ap.add_argument("--out", default="", help="把应用规则后的预测写到这个 jsonl")
    args = ap.parse_args()

    drop = {p.strip() for p in args.exclude_platform.split(",") if p.strip()}
    rows = [r for r in read_jsonl(Path(args.gt)) if r["platform"] not in drop]

    pred_obj: dict[str, dict] = {}
    for p in args.preds.split(","):
        for x in read_jsonl(Path(p.strip())):
            if x.get("id") and x.get("label"):
                pred_obj[x["id"]] = x
    preds = {k: v["label"] for k, v in pred_obj.items()}

    # 只评测「有真值 + 有预测 + 有视频」的样本，与主报告口径一致
    cov = [r for r in rows if r["label"] and r["id"] in preds
           and pred_obj[r["id"]].get("media")]
    dev, test = split_dev_test(cov, seed=args.seed)
    print(f"评测集 {len(cov)} 条 → dev {len(dev)} / test {len(test)}")

    kinds = tuple(k.strip() for k in args.kinds.split(",") if k.strip())
    rules = mine_rules(dev, args.min_n, args.min_purity, kinds)
    n_by_kind = Counter(r["kind"] for r in rules)
    print(f"\n在 dev 上挖出 {len(rules)} 条规则"
          f"（样本 ≥{args.min_n}，纯度 ≥{args.min_purity:.0%}；"
          + "、".join(f"{k} {v} 条" for k, v in n_by_kind.most_common()) + "）")
    for r in rules[:15]:
        sign = "#" if r["kind"] == "tag" else "@"
        print(f"  {sign}{r['key']:<26} → {r['label']:<6} {r['hit']}/{r['total']}")
    if len(rules) > 15:
        print(f"  …… 另有 {len(rules)-15} 条")

    print(f"\n{'集合':<8}{'规则前':>16}{'规则后':>16}{'净变化':>10}")
    print("-" * 52)
    summary = {}
    for name, subset in (("dev", dev), ("test", test), ("全量", cov)):
        a, n, _ = evaluate(subset, preds)
        b, _, ch = evaluate(subset, preds, rules)
        good = sum(1 for c in ch if c["good"])
        bad = sum(1 for c in ch if c["bad"])
        summary[name] = dict(before=a / n, after=b / n, n=n, good=good, bad=bad,
                             changed=len(ch))
        print(f"{name:<8}{a}/{n} = {a/n:>6.2%}{b}/{n} = {b/n:>6.2%}{b-a:>+10d}")
        print(f"{'':8}{'':16}改动 {len(ch)} 条（修复 {good}，打坏 {bad}）")

    t = summary["test"]
    verdict = ("规则在 test 上为正收益，可用" if t["after"] > t["before"] else
               "规则在 test 上无正收益，属 dev 过拟合，不建议采用")
    print(f"\n结论：{verdict}")
    print(f"  dev  {summary['dev']['before']:.2%} → {summary['dev']['after']:.2%}")
    print(f"  test {t['before']:.2%} → {t['after']:.2%}")

    Path(args.rules).write_text(
        json.dumps({"min_n": args.min_n, "min_purity": args.min_purity,
                    "seed": args.seed, "kinds": list(kinds), "rules": rules,
                    "eval": {k: {kk: vv for kk, vv in v.items()} for k, v in summary.items()}},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n规则 -> {args.rules}")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as f:
            for vid, x in pred_obj.items():
                r = next((r for r in rows if r["id"] == vid), None)
                if r:
                    lab, reason = apply_rules(r, x["label"], rules)
                    x = {**x, "label": lab}
                    if reason:
                        x["rule"] = reason
                f.write(json.dumps(x, ensure_ascii=False) + "\n")
        print(f"预测 -> {out}")


if __name__ == "__main__":
    main()
