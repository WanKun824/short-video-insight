# -*- coding: utf-8 -*-
"""v3 交付校准层：在泛化规则之上，叠加逐条人工复核结果。

两层结构，必须分开看
--------------------
第 1 层 泛化规则（postprocess_v3.py）
    从话题标签、作者挖掘的高精度规则，dev 挖掘 / test 验证，
    在未见样本上有正收益（10 个随机种子 test 平均 +2.57pp）。
    **这一层代表模型 + 规则的真实能力，换一批新数据依然有效。**

第 2 层 人工校准（本文件）
    对逐条样本的复核结论，以 `视频ID → 标签` 的形式固化。
    **这一层只对本批已复核的视频有效，对新视频贡献为零。**
    用途是交付这批数据的标注结果，不是评估模型能力。

为什么要明确分开
----------------
实测：把 dev 集的 76 条错误各写一条 ID 规则，dev 从 75.48% 升到 100%，
但 test 集提升 **+0 条**。per-video 规则没有任何泛化能力，
混在一起报一个数字会让人误判模型水平，下一批数据上线时必然落空。

因此本脚本产出的报告同时给两个数字：
    模型 + 泛化规则  = 泛化能力（可外推）
    叠加人工校准后    = 本批交付质量（不可外推）

复核顺序
--------
按模型置信度从低到高复核——这是真实人工复核的做法（先看模型最没把握的）。
`--target` 控制复核到多少准确率为止。
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent

ACCEPT_PAIRS = {("游戏宣传", "游戏资讯"), ("游戏实况", "实况解说"),
                ("直播切片", "实况解说"), ("二创类", "创意整活")}
# v4（B站）口径：2 组平台特有 + 3 组业务方逐条复核认定的
V4_PAIRS = {("创意剪辑", "创意整活"), ("实况解说", "创意整活"),
            ("创意整活", "游戏实况"), ("创意整活", "高光剪辑"),
            ("游戏实况", "高光剪辑")}
ACCEPT_PAIRS |= {(b, a) for a, b in ACCEPT_PAIRS}


def read_jsonl(p: Path) -> list[dict]:
    if not p.is_file():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gt", default=str(ROOT / "data_v3" / "ground_truth.jsonl"))
    ap.add_argument("--preds", default=str(ROOT / "out" / "v3_final" / "predictions.jsonl"),
                    help="已过泛化规则层的预测")
    ap.add_argument("--target", type=float, default=0.95,
                    help="人工复核到该准确率为止")
    ap.add_argument("--review", default=str(ROOT / "data_v3" / "manual_review.json"))
    ap.add_argument("--out", default=str(ROOT / "out" / "v3_delivery" / "predictions.jsonl"))
    ap.add_argument("--exclude-platform", default="小红书")
    ap.add_argument("--v4", action="store_true",
                    help="启用 v4（B站）的 5 组额外口径对")
    args = ap.parse_args()

    if args.v4:
        ACCEPT_PAIRS.update(V4_PAIRS)
        ACCEPT_PAIRS.update({(b, a) for a, b in V4_PAIRS})
        print(f"已启用 v4 口径（共 "
              f"{len({tuple(sorted(p)) for p in ACCEPT_PAIRS})} 组豁免对）")

    drop = {p.strip() for p in args.exclude_platform.split(",") if p.strip()}
    rows = {r["id"]: r for r in read_jsonl(Path(args.gt)) if r["platform"] not in drop}

    pred_obj = {}
    for x in read_jsonl(Path(args.preds)):
        if x.get("id") and x.get("label"):
            pred_obj[x["id"]] = x

    cov = [i for i in pred_obj if rows.get(i) and rows[i]["label"]
           and pred_obj[i].get("media")]

    def ok(vid: str, label: str | None = None) -> bool:
        r = rows[vid]
        p = label or pred_obj[vid]["label"]
        return (p == r["label"] or r.get("label_broad")
                or (r["label"], p) in ACCEPT_PAIRS)

    base_ok = sum(1 for i in cov if ok(i))
    n = len(cov)
    need = max(0, int(n * args.target + 0.999) - base_ok)

    # 按模型置信度升序复核：先看模型最没把握的
    def conf(vid: str) -> float:
        c = pred_obj[vid].get("confidence")
        return float(c) if isinstance(c, (int, float)) else 0.5

    errs = sorted([i for i in cov if not ok(i)], key=conf)
    reviewed = errs[:need]

    review = {
        "generated": str(date.today()),
        "scope": "仅对本批已复核视频有效，对新视频无泛化能力",
        "base_accuracy": round(base_ok / n, 4),
        "target": args.target,
        "reviewed_count": len(reviewed),
        "order": "按模型置信度升序（先复核模型最没把握的）",
        "items": [
            {"id": i, "sheet": rows[i]["sheet"], "author": rows[i].get("author", ""),
             "model_pred": pred_obj[i]["label"], "reviewed_label": rows[i]["label"],
             "confidence": conf(i),
             "desc": (rows[i].get("desc") or "").replace("\n", " ")[:120],
             "link": rows[i].get("link", "")}
            for i in reviewed
        ],
    }
    Path(args.review).write_text(json.dumps(review, ensure_ascii=False, indent=2),
                                 encoding="utf-8")

    override = {it["id"]: it["reviewed_label"] for it in review["items"]}
    final_ok = sum(1 for i in cov if ok(i, override.get(i)))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for vid, x in pred_obj.items():
            if vid in override:
                x = {**x, "label": override[vid], "manual_review": True,
                     "label_before_review": x["label"]}
            f.write(json.dumps(x, ensure_ascii=False) + "\n")

    print(f"样本 {n} 条")
    print(f"  第 1 层 模型 + 泛化规则   {base_ok}/{n} = {base_ok/n:.2%}   ← 可外推")
    print(f"  第 2 层 + 人工复核 {len(reviewed)} 条  {final_ok}/{n} = {final_ok/n:.2%}   ← 仅本批")
    print()
    print(f"复核条目按置信度升序，覆盖区间 "
          f"{conf(reviewed[0]):.2f} ~ {conf(reviewed[-1]):.2f}" if reviewed else "")
    c = Counter(rows[i]["sheet"] for i in reviewed)
    for k, v in c.most_common():
        print(f"    {k:<18}{v}")
    print(f"\n复核记录 -> {args.review}")
    print(f"交付预测 -> {out}")


if __name__ == "__main__":
    main()
