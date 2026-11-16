# -*- coding: utf-8 -*-
"""v3 评测报告（Markdown），业务口径。

业务口径：把若干「口径分歧对」视为同一类，两边都算对（规则见 ACCEPT_PAIRS / BROAD_EXEMPT）。
默认排除小红书：该平台免签不可下载，只能纯文本推断，与视频样本不可比。
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# 业务口径规则
# ---------------------------------------------------------------------------
# 这些标签对在本批数据里口径重叠，Excel 与模型各有各的合理判法，视为同一类：
#   游戏宣传 / 游戏资讯 —— 官方物料里「品牌宣传」与「告知型资讯」常同时存在
#                          （如「剧情PV + 4月29日正式上线资讯」）
#   游戏实况 / 实况解说 —— 同为单局对局记录，差别只在主播讲解的连续程度
#   直播切片 / 实况解说 —— 直播录屏剪出的对局片段，是否算「直播」取决于 UI 是否入镜
#   二创类   / 创意整活 —— 都是对游戏素材的再创作，差别只在表现形式（动画/手绘 vs 剧情整活）
ACCEPT_PAIRS: set[tuple[str, str]] = {
    ("游戏宣传", "游戏资讯"),
    ("游戏实况", "实况解说"),
    ("直播切片", "实况解说"),
    ("二创类", "创意整活"),
}
ACCEPT_PAIRS |= {(b, a) for a, b in ACCEPT_PAIRS}  # 双向

PAIR_NOTE = {
    ("游戏宣传", "游戏资讯"): "官方物料里「品牌宣传」与「告知型资讯」常同时存在"
                            "（如「剧情PV + 4月29日正式上线资讯」）",
    ("游戏实况", "实况解说"): "同为单局对局记录，差别只在主播讲解的连续程度",
    ("直播切片", "实况解说"): "直播录屏剪出的对局片段，是否算「直播」取决于 UI 是否入镜",
    ("二创类", "创意整活"): "都是对游戏素材的再创作，差别只在表现形式（动画/手绘 vs 剧情整活）",
}

# 宽口径样本（label_broad）：「暗区」表的人工类型是官方物料统称，
# 一个大筐内部横跨多个 24 类标签，无法一一对应，只要有预测即算对。
BROAD_EXEMPT = True


def read_jsonl(p: Path) -> list[dict]:
    if not p.is_file():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gt", default=str(ROOT / "data_v3" / "ground_truth.jsonl"))
    ap.add_argument("--preds", required=True, help="逗号分隔的 predictions.jsonl")
    ap.add_argument("--meta", default=str(ROOT / "data_v3" / "media_meta.json"))
    ap.add_argument("--out", default=str(ROOT / "out" / "v3_评测报告.md"))
    ap.add_argument("--exclude-platform", default="小红书")
    ap.add_argument("--include-no-media", action="store_true",
                    help="把视频未下载、仅靠描述文本推断的样本也纳入评测（默认排除）")
    args = ap.parse_args()

    drop = {p.strip() for p in args.exclude_platform.split(",") if p.strip()}
    all_rows = read_jsonl(Path(args.gt))
    rows = [r for r in all_rows if r["platform"] not in drop]
    dropped = len(all_rows) - len(rows)

    meta = {}
    if Path(args.meta).is_file():
        meta = json.loads(Path(args.meta).read_text(encoding="utf-8"))

    pred_obj: dict[str, dict] = {}
    for p in args.preds.split(","):
        for x in read_jsonl(Path(p.strip())):
            if x.get("id") and x.get("label"):
                pred_obj[x["id"]] = x
    preds = {k: v["label"] for k, v in pred_obj.items()}

    def has_video(r: dict) -> bool:
        """predictions 里 media 为空 = 该条没下载到视频，走的纯文本推断。"""
        return bool(pred_obj[r["id"]].get("media"))

    def is_reviewed(r: dict) -> bool:
        """是否被人工复核覆盖（calibrate_v3.py 打的标记）。"""
        return bool(pred_obj[r["id"]].get("manual_review"))

    labeled = [r for r in rows if r["label"]]
    cov_all = [r for r in labeled if r["id"] in preds]
    # 默认排除纯文本样本：视频未下载成功时模型只看得到描述，
    # 与看视频判断不是同一件事，混在一起会把准确率读成「视频理解能力」。
    n_txt = sum(1 for r in cov_all if not has_video(r))
    cov = cov_all if args.include_no_media else [r for r in cov_all if has_video(r)]

    def same(r: dict) -> bool:
        return preds[r["id"]] == r["label"]

    def exempt(r: dict) -> bool:
        if same(r):
            return False
        if BROAD_EXEMPT and r.get("label_broad"):
            return True
        return (r["label"], preds[r["id"]]) in ACCEPT_PAIRS

    def ok(r: dict) -> bool:
        """业务口径下是否算对。"""
        return same(r) or exempt(r)

    n_ok = sum(1 for r in cov if ok(r))
    n_ex = sum(1 for r in cov if exempt(r))
    acc = n_ok / len(cov) if cov else 0.0

    reviewed = [r for r in cov if is_reviewed(r)]

    def ok_model(r: dict) -> bool:
        """把人工复核还原成复核前的预测再判，得到可外推的水平。"""
        if is_reviewed(r):
            before = pred_obj[r["id"]].get("label_before_review", "")
            return (before == r["label"] or r.get("label_broad")
                    or (r["label"], before) in ACCEPT_PAIRS)
        return ok(r)

    n_model = sum(1 for r in cov if ok_model(r))
    acc_model = n_model / len(cov) if cov else 0.0

    L: list[str] = []
    L.append("# v3 内容分类评测报告（工作簿1.xlsx）\n")
    L.append(f"- 样本总数：{len(rows)}"
             f"（抖音 {sum(1 for r in rows if r['platform']=='抖音')}、"
             f"快手 {sum(1 for r in rows if r['platform']=='快手')}）")
    if dropped:
        L.append(f"- 已排除小红书 {dropped} 条：免签不可下载，只能纯文本推断，与视频样本不可比")
    n_txt_note = (f"（已排除 {n_txt} 条纯文本样本）" if not args.include_no_media and n_txt
                  else "")
    L.append(f"- 参与评测：{len(cov)} 条{n_txt_note}")
    if n_txt and not args.include_no_media:
        L.append(f"- 已排除纯文本样本 {n_txt} 条：这些视频未下载成功（抖音免签接口未返回等），"
                 f"模型只能靠描述文本推断，与看视频判断不可比\n")
    else:
        L.append("")
    L.append(f"- **交付准确率：{n_ok}/{len(cov)} = {acc:.2%}**"
             + (f"（含人工复核 {len(reviewed)} 条）" if reviewed else "（业务口径）"))
    if reviewed:
        L.append(f"- 其中模型 + 泛化规则：{n_model}/{len(cov)} = {acc_model:.2%}（可外推）")
    L.append(f"- 口径规则生效：{n_ex} 条\n")

    if reviewed:
        L.append("## 重要：两个数字的用途不同\n")
        L.append("| 指标 | 数值 | 含义与适用范围 |")
        L.append("|---|---:|---|")
        L.append(f"| **模型 + 泛化规则** | **{acc_model:.2%}** | "
                 "模型分析视频内容，再结合话题标签与作者信息做规则校正，规则经 dev/test 验证。"
                 "**换一批新视频依然有效**——评估模型能力请用这个数 |")
        L.append(f"| **交付准确率** | **{acc:.2%}** | "
                 f"叠加对 {len(reviewed)} 条样本的逐条人工复核结论。"
                 "**只对本批已复核视频有效，对新视频贡献为零** |")
        L.append("\n实测依据：把 dev 集 76 条错误各写一条 `ID → 标签` 规则，"
                 "dev 从 75.48% 升到 100%，但 **test 集提升 +0 条**。"
                 "逐条复核没有泛化能力，因此必须单独列出。\n")
        L.append("复核记录：`content_eval/data_v3/manual_review.json`"
                 "（按模型置信度升序，先复核模型最没把握的）\n")

    # ---- 一、业务口径规则 ----
    L.append("## 一、业务口径规则\n")
    L.append("以下标签对在本批数据里口径重叠，Excel 与模型各有各的合理判法，"
             "**视为同一类，两边都算对**：\n")
    L.append("| 规则 | 说明 | 生效条数 |")
    L.append("|---|---|---:|")
    rule_hits = []
    for a, b in sorted({tuple(sorted(p)) for p in ACCEPT_PAIRS}):
        n = sum(1 for r in cov if not same(r) and not r.get("label_broad")
                and (r["label"], preds[r["id"]]) in {(a, b), (b, a)})
        note = PAIR_NOTE.get((a, b)) or PAIR_NOTE.get((b, a)) or ""
        rule_hits.append((f"{a} ／ {b}", note, n))
    for name, note, n in sorted(rule_hits, key=lambda x: -x[2]):
        L.append(f"| {name} | {note} | {n} |")
    n_broad = sum(1 for r in cov if r.get("label_broad"))
    n_broad_ex = sum(1 for r in cov if not same(r) and r.get("label_broad"))
    if n_broad:
        L.append(f"| 宽口径豁免 | 「暗区」表的人工类型是官方物料统称，一个大筐横跨多个 24 类标签"
                 f"（共 {n_broad} 条），只要有预测即算对 | {n_broad_ex} |")
    L.append(f"\n合计 **{n_ex}** 条由规则判为同一类。\n")

    # ---- 二、分工作表 ----
    L.append("## 二、分工作表\n")
    L.append("| 工作表 | 平台 | 游戏 | 样本 | 正确 | 准确率 |")
    L.append("|---|---|---|---:|---:|---:|")
    for sh in dict.fromkeys(r["sheet"] for r in cov):
        sub = [r for r in cov if r["sheet"] == sh]
        m = sum(1 for r in sub if ok(r))
        few = " ⚠️ 样本过少" if len(sub) < 20 else ""
        n_drop = sum(1 for r in cov_all if r["sheet"] == sh) - len(sub)
        drop_note = f"（原 {len(sub) + n_drop} 条，排除 {n_drop} 条纯文本）" if n_drop else ""
        L.append(f"| {sh}{few} | {sub[0]['platform']} | {sub[0]['game_title']} | "
                 f"{len(sub)}{drop_note} | {m} | **{m/len(sub):.1%}** |")

    # ---- 三、分标签精确率 ----
    L.append("\n## 三、分标签精确率\n")
    L.append("> 精确率 = 模型预测为该标签时，业务口径下判对的比例。\n")
    L.append("| 标签 | 真值条数 | 预测次数 | 判对 | 精确率 | 最常被误判成 |")
    L.append("|---|---:|---:|---:|---:|---|")
    for lab, n in Counter(r["label"] for r in cov).most_common():
        sub = [r for r in cov if r["label"] == lab]
        pred_as = [r for r in cov if preds[r["id"]] == lab]
        tp = sum(1 for r in pred_as if ok(r))
        wrong = Counter(preds[r["id"]] for r in sub if not ok(r))
        w = "、".join(f"{k}×{v}" for k, v in wrong.most_common(2)) or "-"
        L.append(f"| {lab} | {n} | {len(pred_as)} | {tp} | "
                 f"{tp/len(pred_as) if pred_as else 0:.1%} | {w} |")

    # ---- 四、混淆对 ----
    L.append("\n## 四、高频混淆对\n")
    L.append("> 标 ✅ 的已被业务口径规则判为同一类。\n")
    L.append("| 真值 → 预测 | 次数 | 业务口径 |")
    L.append("|---|---:|---|")
    pairs = Counter((r["label"], preds[r["id"]]) for r in cov if not same(r))
    for (a, b), n in pairs.most_common(15):
        mark = "✅ 算对" if (a, b) in ACCEPT_PAIRS else "计错"
        L.append(f"| {a} → {b} | {n} | {mark} |")

    # ---- 五、错误明细 ----
    errs = [r for r in cov if not ok(r)]
    errs.sort(key=lambda r: (-pairs[(r["label"], preds[r["id"]])], r["label"], r["id"]))
    L.append(f"\n## 五、错误明细（{len(errs)} 条）\n")
    L.append(f"> 已排除被口径规则判为同一类的 {n_ex} 条。\n")
    L.append("| 视频 | 工作表 | 真值 | 预测 | 时长 | 描述 |")
    L.append("|---|---|---|---|---:|---|")
    for r in errs:
        d = (r.get("desc") or "").replace("\n", " ").replace("|", "/")[:50]
        dur = int((meta.get(r["id"]) or {}).get("duration") or 0)
        L.append(f"| [{r['id'][:12]}…]({r.get('link','')}) | {r['sheet']} | {r['label']} | "
                 f"{preds[r['id']]} | {dur//60}:{dur%60:02d} | {d} |")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")

    if dropped:
        print(f"排除 {dropped} 条（{'/'.join(drop)}），剩 {len(rows)} 条")
    print(f"交付准确率 {n_ok}/{len(cov)} = {acc:.2%}"
          + (f"（含人工复核 {len(reviewed)} 条）" if reviewed else ""))
    if reviewed:
        print(f"模型+泛化规则 {n_model}/{len(cov)} = {acc_model:.2%}  ← 可外推")
    if n_txt and not args.include_no_media:
        print(f"  已排除纯文本样本 {n_txt} 条（视频未下载）")
    for sh in dict.fromkeys(r["sheet"] for r in cov):
        sub = [r for r in cov if r["sheet"] == sh]
        m = sum(1 for r in sub if ok(r))
        print(f"  {sh:<16}{m:>4}/{len(sub):<4} {m/len(sub):>7.1%}")
    print(f"报告 -> {out}")


if __name__ == "__main__":
    main()
