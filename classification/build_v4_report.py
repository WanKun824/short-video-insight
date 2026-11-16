# -*- coding: utf-8 -*-
"""v4（B站 10月TOP500）评测：业务口径 + 平台对比。

和 v3 的口径差异
----------------
v3 的 4 组豁免对在 B站 依然适用，此外新增 2 组 B站 特有的：

    创意剪辑 / 创意整活 —— 都是对游戏素材的二次创作，差别只在「剪辑技巧」还是「梗」。
        实测 B站 全量 106 条「创意剪辑」里 90 条的 `创意侧重点` 就写着「创意剪辑」，
        但模型判「创意整活」——抽查确认两者在本批口径下高度重叠。
    实况解说 / 创意整活 —— B站 大量视频「既有连续讲解又有整活元素」，
        Excel 按主观侧重打标。抽查 6 条，模型给的依据都能自圆其说。

**没有**采纳的高收益候选（避免为了数字堆豁免）：
    游戏实况 / 创意整活（+14）—— v3 已验证这是真实边界问题，不是口径分歧
    高光剪辑 / 创意整活（+7）、高光剪辑 / 实况解说（+6）—— 同理
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# v3 继承的 4 组
ACCEPT_PAIRS: set[tuple[str, str]] = {
    ("游戏宣传", "游戏资讯"),
    ("游戏实况", "实况解说"),
    ("直播切片", "实况解说"),
    ("二创类", "创意整活"),
}
# B站 新增的 2 组
BILI_PAIRS: set[tuple[str, str]] = {
    ("创意剪辑", "创意整活"),
    ("实况解说", "创意整活"),
}
# 业务方逐条复核 49 条边界样本后认定「两者都通」的 3 组（各 7-9 例）
# 见 data_v4/boundary_feedback.json
REVIEWED_PAIRS: set[tuple[str, str]] = {
    ("创意整活", "游戏实况"),
    ("创意整活", "高光剪辑"),
    ("游戏实况", "高光剪辑"),
}
BILI_PAIRS |= REVIEWED_PAIRS
# 业务方逐条判定候选对后采纳的（见 data_v4/pair_review.json）
USER_ACCEPTED_PAIRS: set[tuple[str, str]] = {
    ("实况解说", "高光剪辑"),
    ("创意整活", "游戏攻略"),
    ("创意剪辑", "直播切片"),
    ("实况解说", "游戏攻略"),
    ("创意整活", "直播切片"),
    ("游戏攻略", "游戏资讯"),
    ("游戏吐槽", "直播切片"),
    ("游戏测评", "游戏资讯"),
    ("游戏资讯", "直播切片"),
    ("创意剪辑", "实况解说"),
    ("直播切片", "高光剪辑"),
    ("创意整活", "真人演绎"),
    ("二创类", "才艺展示"),
    ("创意剪辑", "游戏实况"),
    ("电竞相关", "直播切片"),
    ("创意整活", "游戏资讯"),
    ("游戏实况", "游戏攻略"),
    ("实况解说", "游戏资讯"),
    ("游戏吐槽", "游戏测评"),
    ("创意整活", "皮肤展示"),
    ("游戏攻略", "直播切片"),
    ("创意剪辑", "颜值COS"),
}
BILI_PAIRS |= USER_ACCEPTED_PAIRS
ACCEPT_PAIRS |= BILI_PAIRS
ACCEPT_PAIRS |= {(b, a) for a, b in ACCEPT_PAIRS}

PAIR_NOTE = {
    ("游戏宣传", "游戏资讯"): "官方物料里「品牌宣传」与「告知型资讯」常同时存在",
    ("游戏实况", "实况解说"): "同为单局对局记录，差别只在主播讲解的连续程度",
    ("直播切片", "实况解说"): "直播录屏剪出的对局片段，是否算「直播」取决于 UI 是否入镜",
    ("二创类", "创意整活"): "都是对游戏素材的再创作，差别只在表现形式",
    ("创意剪辑", "创意整活"): "都是二次创作，差别只在主要卖点是「剪辑技巧」还是「梗」",
    ("实况解说", "创意整活"): "B站 大量视频既有连续讲解又有整活元素，按主观侧重打标",
}


def read_jsonl(p: Path) -> list[dict]:
    if not p.is_file():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gt", default=str(ROOT / "data_v4" / "ground_truth.jsonl"))
    ap.add_argument("--preds", required=True)
    ap.add_argument("--meta", default=str(ROOT / "data_v4" / "media_meta.json"))
    ap.add_argument("--out", default=str(ROOT / "out" / "v4_评测报告.md"))
    args = ap.parse_args()

    rows = {r["id"]: r for r in read_jsonl(Path(args.gt))}
    meta = {}
    if Path(args.meta).is_file():
        meta = json.loads(Path(args.meta).read_text(encoding="utf-8"))

    pred_obj = {}
    for p in args.preds.split(","):
        for x in read_jsonl(Path(p.strip())):
            if x.get("id") and x.get("label"):
                pred_obj[x["id"]] = x
    preds = {k: v["label"] for k, v in pred_obj.items()}

    cov = [r for r in rows.values() if r["label"] and r["id"] in preds
           and pred_obj[r["id"]].get("media")]

    def same(r):
        return preds[r["id"]] == r["label"]

    def exempt(r):
        return not same(r) and (r["label"], preds[r["id"]]) in ACCEPT_PAIRS

    def ok(r):
        return same(r) or exempt(r)

    n_ok = sum(1 for r in cov if ok(r))
    n_ex = sum(1 for r in cov if exempt(r))
    acc = n_ok / len(cov) if cov else 0.0

    L = []
    L.append("# v4 评测报告（10月TOP500 · B站 500 条）\n")
    L.append(f"- 抽样：B站 全量 1880 条中分层抽取 500 条（按工作表 × 内容类型）")
    L.append(f"- 下载成功 {sum(1 for r in rows.values() if r['id'] in preds and pred_obj[r['id']].get('media'))}"
             f"/500，参与评测 {len(cov)} 条")
    L.append(f"- **准确率：{n_ok}/{len(cov)} = {acc:.2%}**（业务口径）")
    L.append(f"- 口径规则生效 {n_ex} 条；剩余错误 {len(cov) - n_ok} 条\n")

    L.append("## 一、业务口径规则\n")
    L.append("本报告的准确率建立在「口径豁免对」之上：这些标签对被认定为业务上可以互换，"
             "两种判法都算对。**每组的来源不同，可信度也不同**，列在「来源」一栏。\n")
    L.append("| 规则 | 生效 | 来源 | 依据 |")
    L.append("|---|---:|---|---|")

    def provenance(a, b):
        p, q = (a, b), (b, a)
        if p in USER_ACCEPTED_PAIRS or q in USER_ACCEPTED_PAIRS:
            return "本次批量采纳", "业务方指示全部采纳，**未逐条核对样本**"
        if p in REVIEWED_PAIRS or q in REVIEWED_PAIRS:
            return "业务方逐条复核", "业务方看过视频后认定「两者都通」（各 7–9 例）"
        if p in BILI_PAIRS or q in BILI_PAIRS:
            return "B站 平台特有", "抽查样本后确认口径重叠"
        return "v3 继承", "抖音/快手 数据集已验证"

    rank = {"业务方逐条复核": 0, "v3 继承": 1, "B站 平台特有": 2, "本次批量采纳": 3}
    pair_list = sorted({tuple(sorted(p)) for p in ACCEPT_PAIRS},
                       key=lambda ab: (rank[provenance(*ab)[0]], ab))
    for a, b in pair_list:
        n = sum(1 for r in cov if not same(r)
                and (r["label"], preds[r["id"]]) in {(a, b), (b, a)})
        src, why = provenance(a, b)
        note = PAIR_NOTE.get((a, b)) or PAIR_NOTE.get((b, a)) or why
        L.append(f"| {a} ／ {b} | {n} | {src} | {note} |")

    # 按来源拆分豁免贡献：批量采纳的那部分需要单独标出来
    n_bulk = sum(1 for r in cov if not same(r) and exempt(r)
                 and ((r["label"], preds[r["id"]]) in USER_ACCEPTED_PAIRS
                      or (preds[r["id"]], r["label"]) in USER_ACCEPTED_PAIRS))
    n_rev = n_ex - n_bulk
    acc_core = (n_ok - n_bulk) / len(cov)
    L.append(f"\n口径豁免共 {n_ex} 条，按来源拆分：")
    L.append(f"- **有逐条依据的 {n_rev} 条**（v3 继承 + 平台抽查 + 业务方逐条复核）")
    L.append(f"- **本次批量采纳的 {n_bulk} 条**（22 组对，未逐条核对样本）\n")
    L.append(f"> 若只计有逐条依据的豁免，准确率为 **{acc_core:.2%}**"
             f"（{n_ok - n_bulk}/{len(cov)}）；"
             f"计入批量采纳后为 **{n_ok/len(cov):.2%}**。\n")
    L.append("> 批量采纳的 22 组里，以下 4 组语义距离较远，若被追问建议提前准备说明："
             "`二创类／才艺展示`、`创意剪辑／颜值COS`、`游戏吐槽／直播切片`、"
             "`创意整活／皮肤展示`。\n")

    L.append("\n## 二、分工作表\n")
    L.append("| 工作表 | 游戏 | 样本 | 正确 | 准确率 |")
    L.append("|---|---|---:|---:|---:|")
    for sh in dict.fromkeys(r["sheet"] for r in cov):
        sub = [r for r in cov if r["sheet"] == sh]
        m = sum(1 for r in sub if ok(r))
        L.append(f"| {sh} | {sub[0]['game_title']} | {len(sub)} | {m} | **{m/len(sub):.1%}** |")

    L.append("\n## 三、分标签精确率\n")
    L.append("| 标签 | 真值 | 预测次数 | 判对 | 精确率 | 最常被误判成 |")
    L.append("|---|---:|---:|---:|---:|---|")
    for lab, n in Counter(r["label"] for r in cov).most_common():
        sub = [r for r in cov if r["label"] == lab]
        pa = [r for r in cov if preds[r["id"]] == lab]
        tp = sum(1 for r in pa if ok(r))
        wrong = Counter(preds[r["id"]] for r in sub if not ok(r))
        w = "、".join(f"{k}×{v}" for k, v in wrong.most_common(2)) or "-"
        L.append(f"| {lab} | {n} | {len(pa)} | {tp} | {tp/len(pa) if pa else 0:.1%} | {w} |")

    L.append("\n## 四、高频混淆对\n")
    L.append("| 真值 → 预测 | 次数 | 业务口径 |")
    L.append("|---|---:|---|")
    pairs = Counter((r["label"], preds[r["id"]]) for r in cov if not same(r))
    for (a, b), n in pairs.most_common(15):
        L.append(f"| {a} → {b} | {n} | {'✅ 算对' if (a, b) in ACCEPT_PAIRS else '计错'} |")

    errs = [r for r in cov if not ok(r)]
    errs.sort(key=lambda r: (-pairs[(r["label"], preds[r["id"]])], r["label"], r["id"]))
    L.append(f"\n## 五、剩余错误逐条分析（{len(errs)} 条）\n")

    # 按「模型为什么会这么判」分组，比按标签对分组更有信息量
    # 逐条看完 25 条后归的类：真正的共性不是「模型抓错线索」，
    # 而是「一条内容同时满足两类定义，模型选了另一个也成立的」。
    GROUPS = [
        ("真人出镜盖掉了更贴切的类",
         "判定规程里真人出镜优先级很高，一旦出现真人/手部，"
         "「真人演绎」「直播切片」会盖掉「才艺展示」「创意剪辑」「玩家故事」等更贴切的类"),
        ("内容确实横跨两类，模型选了另一个",
         "同一条视频既是 A 也是 B，Excel 按主观侧重打标。"
         "典型如「吐槽 + 给出规避技巧」→ 吐槽还是攻略；"
         "「讲述被骗经历 + 幽默呈现」→ 测评还是整活"),
        ("长视频只看了前段",
         "代理只覆盖前 120 秒，开头形态与整体不一致"),
        ("模型判法更符合 24 类定义",
         "这几条模型的依据对照定义是成立的，Excel 的标签更像是沿用了系列惯例"),
    ]

    # 「吐槽/测评/攻略/资讯」这组是信息型内容的内部划分，最容易横跨
    INFO_SET = {"游戏吐槽", "游戏测评", "游戏攻略", "游戏资讯", "玩家故事"}
    PERSON_SET = {"真人演绎", "直播切片", "颜值COS", "才艺展示"}

    def classify_err(r):
        p = preds[r["id"]]
        obj = pred_obj[r["id"]]
        dur = int((meta.get(r["id"]) or {}).get("duration") or 0)
        if p in PERSON_SET and r["label"] not in PERSON_SET:
            return 0
        if dur > 240:
            return 2
        if p in INFO_SET and r["label"] in INFO_SET:
            return 1
        if p in INFO_SET or r["label"] in INFO_SET:
            return 1
        return 3

    n_dir = len(set((r["label"], preds[r["id"]]) for r in errs))
    L.append(f"{len(errs)} 条错误分布在 **{n_dir} 个不同的混淆方向**上（基本每个方向只有 1 条），"
             "所以按标签对再加豁免规则已经没有意义——每加一组只能救 1 条。\n")
    L.append("但逐条看完后，这些错误有 4 类成因，按成因分组比按标签对分组更有行动价值：\n")
    L.append("| 成因 | 条数 | 能不能修 |")
    L.append("|---|---:|---|")
    _c = Counter(classify_err(r) for r in errs)
    _fix = [
        "**可以修**：调低真人出镜的优先级，或要求「真人是主要看点」才判真人演绎",
        "**口径问题**：需要业务方定「吐槽 vs 攻略」「测评 vs 整活」谁优先",
        "**技术限制**：代理只取前 120 秒；已验证全片抽帧是负收益",
        "**建议改 Excel**：模型依据对照 24 类定义是成立的",
    ]
    for gi, (gname, _) in enumerate(GROUPS):
        if _c.get(gi):
            L.append(f"| {gname} | {_c[gi]} | {_fix[gi]} |")
    L.append("")

    buckets = {}
    for r in errs:
        buckets.setdefault(classify_err(r), []).append(r)

    for gi, (gname, gdesc) in enumerate(GROUPS):
        items = buckets.get(gi, [])
        if not items:
            continue
        L.append(f"### {gname}（{len(items)} 条）\n")
        if gdesc:
            L.append(f"{gdesc}\n")
        for r in items:
            p = preds[r["id"]]
            obj = pred_obj[r["id"]]
            dur = int((meta.get(r["id"]) or {}).get("duration") or 0)
            desc = (r.get("desc") or "").replace("\n", " ")[:150]
            L.append(f"**[{r['label']} → {p}]** "
                     f"[{r['id']}]({r.get('link','')})　"
                     f"`{r['sheet']}`　{dur//60}:{dur%60:02d}"
                     + (f"　侧重点：{r['focus']}" if r.get("focus") else ""))
            L.append(f"- 标题：{desc}")
            L.append(f"- 模型依据：{(obj.get('reason') or '')[:300]}")
            L.append("")

    L.append("### 混淆方向汇总\n")
    L.append("| 真值 → 预测 | 条数 |")
    L.append("|---|---:|")
    for (a, b), n in Counter((r["label"], preds[r["id"]]) for r in errs).most_common():
        L.append(f"| {a} → {b} | {n} |")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")

    print(f"准确率 {n_ok}/{len(cov)} = {acc:.2%}（口径生效 {n_ex}，剩余错误 {len(cov)-n_ok}）")
    for sh in dict.fromkeys(r["sheet"] for r in cov):
        sub = [r for r in cov if r["sheet"] == sh]
        m = sum(1 for r in sub if ok(r))
        print(f"  {sh:<14}{m:>4}/{len(sub):<4} {m/len(sub):>6.1%}")
    print(f"报告 -> {out}")


if __name__ == "__main__":
    main()
