# -*- coding: utf-8 -*-
"""读回「候选豁免对判定.html」导出的 pair_review.json，写进评测口径。

会直接改 build_v4_report.py 里的 REVIEWED_PAIRS，并打印采纳前后的准确率。
被拒绝的对保留在报告里作为「真实错误」，不计入豁免。

用法：
    python content_eval/apply_pair_feedback.py --fb ~/Downloads/pair_review.json
    python content_eval/apply_pair_feedback.py --fb ... --write   # 真正写入
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fb", required=True)
    ap.add_argument("--write", action="store_true", help="真正写入 build_v4_report.py")
    ap.add_argument("--target", default=str(ROOT / "build_v4_report.py"))
    args = ap.parse_args()

    fb = json.loads(Path(args.fb).read_text(encoding="utf-8"))
    accept = fb.get("accept", [])
    reject = fb.get("reject", [])
    later = fb.get("later", [])

    print(f"判定结果（{fb.get('generated','')}）：")
    print(f"  采纳 {len(accept)} 个 · 拒绝 {len(reject)} 个 · 待议 {len(later)} 个")
    b = fb.get("base", {})
    e = fb.get("estimated", {})
    if b and e:
        print(f"\n准确率：{b['correct']}/{b['total']} = {b['correct']/b['total']:.2%}"
              f"  →  {e['correct']}/{e['total']} = {e['accuracy']:.2%}")

    if accept:
        print(f"\n【采纳的豁免对】")
        for r in accept:
            note = f"  // {r['why']}" if r.get("why") else ""
            print(f'    ("{r["a"]}", "{r["b"]}"),   # +{r["gain"]} 条{note}')
    if reject:
        print(f"\n【拒绝的（留作真实错误，需靠提示词/规则修）】")
        for r in reject:
            note = f"  — {r['why']}" if r.get("why") else ""
            print(f"    {r['a']} / {r['b']}   放弃 +{r['gain']} 条{note}")
    if later:
        print(f"\n【待议】")
        for r in later:
            print(f"    {r['a']} / {r['b']}")

    if not args.write:
        print("\n（这是预览。加 --write 才会真正写入 build_v4_report.py）")
        return

    if not accept:
        print("\n没有采纳任何对，不改动文件。")
        return

    path = Path(args.target)
    src = path.read_text(encoding="utf-8")
    block = "\n".join(
        f'    ("{r["a"]}", "{r["b"]}"),'
        + (f'   # {r["why"]}' if r.get("why") else "")
        for r in accept)
    new = (
        "# 业务方逐条判定候选对后采纳的（见 data_v4/pair_review.json）\n"
        "USER_ACCEPTED_PAIRS: set[tuple[str, str]] = {\n"
        + block + "\n}\nBILI_PAIRS |= USER_ACCEPTED_PAIRS\n"
    )
    anchor = "BILI_PAIRS |= REVIEWED_PAIRS\n"
    if "USER_ACCEPTED_PAIRS" in src:
        src = re.sub(r"# 业务方逐条判定候选对后采纳的.*?BILI_PAIRS \|= USER_ACCEPTED_PAIRS\n",
                     new, src, flags=re.S)
    else:
        src = src.replace(anchor, anchor + new, 1)
    path.write_text(src, encoding="utf-8")
    print(f"\n已写入 {path}（{len(accept)} 个对）")
    print("重新出报告：python content_eval/build_v4_report.py --preds "
          "content_eval/out/v4_bili/predictions.jsonl")

    out = ROOT / "data_v4" / "pair_review.json"
    out.write_text(json.dumps(fb, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"判定记录 -> {out}")


if __name__ == "__main__":
    main()
