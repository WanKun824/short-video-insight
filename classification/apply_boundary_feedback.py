# -*- coding: utf-8 -*-
"""读回「三类边界对比.html」导出的 boundary_feedback.json，给出可执行的下一步。

页面上的四个选项对应四种处理：
    excel    Excel 对、模型错        → 真实错误，需要靠提示词/规则修
    model    模型对、Excel 错        → 标注错误，应反馈给标注方修 Excel
    both     两者都通（口径分歧）    → 加入 ACCEPT_PAIRS 豁免
    neither  都不对                  → 真实错误，且连 Excel 也要改

用法：
    python content_eval/apply_boundary_feedback.py --fb ~/Downloads/boundary_feedback.json
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent

VERDICT_CN = {
    "excel": "Excel 对（模型真错）",
    "model": "模型对（Excel 标注错）",
    "both": "两者都通（口径分歧）",
    "neither": "都不对",
}


def read_jsonl(p: Path):
    if not p.is_file():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fb", required=True, help="页面导出的 boundary_feedback.json")
    ap.add_argument("--out", default=str(ROOT / "data_v4" / "boundary_verdicts.json"))
    args = ap.parse_args()

    fb = json.loads(Path(args.fb).read_text(encoding="utf-8"))
    items = fb.get("items", [])
    if not items:
        raise SystemExit("反馈文件里没有已判断的条目")

    rows = {}
    for g in ("data_v4", "data_v3"):
        for r in read_jsonl(ROOT / g / "ground_truth.jsonl"):
            rows.setdefault(r["id"], r)

    print(f"读入 {len(items)}/{fb.get('total', '?')} 条判断"
          f"（{fb.get('generated', '')}）\n")

    c = Counter(i["verdict"] for i in items)
    print("判断分布：")
    for k, n in c.most_common():
        print(f"  {VERDICT_CN.get(k, k):<22}{n:>4}  ({n/len(items):.0%})")

    # ---- 1. 口径分歧对：可直接加进 ACCEPT_PAIRS ----
    both = [i for i in items if i["verdict"] == "both"]
    pairs = Counter(tuple(sorted((i["excel_label"], i["model_label"]))) for i in both)
    if pairs:
        print(f"\n【建议加入 ACCEPT_PAIRS 的口径分歧对】")
        print("  （出现 ≥2 次才建议采纳，单次可能是个例）")
        for (a, b), n in pairs.most_common():
            mark = "✅ 建议采纳" if n >= 2 else "⚠️ 仅 1 例，先观察"
            print(f'    ("{a}", "{b}"),   # {n} 例  {mark}')

    # ---- 2. 标注错误：反馈给标注方 ----
    wrong_label = [i for i in items if i["verdict"] == "model"]
    if wrong_label:
        print(f"\n【Excel 标注有误，建议反馈标注方修正】{len(wrong_label)} 条")
        for i in wrong_label[:10]:
            r = rows.get(i["id"], {})
            print(f"  {i['id']}  {i['excel_label']} → 应为 {i['model_label']}")
            print(f"    {r.get('link','')}")
            if i.get("why"):
                print(f"    理由：{i['why']}")

    # ---- 3. 真实错误：需要靠提示词/规则修 ----
    real = [i for i in items if i["verdict"] in ("excel", "neither")]
    if real:
        print(f"\n【模型真实判错】{len(real)} 条，这部分才是优化空间")
        d = Counter(f'{i["excel_label"]} → {i["model_label"]}' for i in real)
        for k, n in d.most_common():
            print(f"    {k}: {n}")
        whys = [i["why"] for i in real if i.get("why")]
        if whys:
            print(f"\n  你填的判断依据（{len(whys)} 条，用于改提示词）：")
            for w in whys[:12]:
                print(f"    · {w}")

    # ---- 4. 判定顺序 ----
    rule = (fb.get("decision_rule") or "").strip()
    if rule:
        print(f"\n【你写的判定顺序】\n{rule}")
        print("\n  → 下一步：我把它写成 prompts.py 里的 DECISION_RULES 片段，")
        print("    用 dev/test + 10 个随机种子验证是否真的涨，不涨就不采纳。")
    else:
        print("\n⚠️ 没有填写「整体判定顺序」——那一栏是最有价值的，")
        print("   有了它才能把判断固化成提示词规则，而不是逐条修。")

    # ---- 落盘 ----
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    detail = []
    for i in items:
        r = rows.get(i["id"], {})
        detail.append({**i, "link": r.get("link", ""), "sheet": r.get("sheet", ""),
                       "desc": (r.get("desc") or "").replace("\n", " ")[:120]})
    out.write_text(json.dumps({
        "generated": fb.get("generated"), "judged": len(items),
        "verdict_counts": dict(c), "decision_rule": rule,
        "suggested_accept_pairs": [list(p) for p, n in pairs.items() if n >= 2],
        "label_errors": [i["id"] for i in wrong_label],
        "real_errors": [i["id"] for i in real],
        "items": detail,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n-> {out}")


if __name__ == "__main__":
    main()
