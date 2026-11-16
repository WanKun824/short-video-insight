# -*- coding: utf-8 -*-
"""v3 评测报告 HTML：图表 + 可点击的真实视频链接。

图表全部用内联 SVG 生成，不依赖任何外部 JS/CSS 库（与 v2 的 HTML 报告一致，离线可打开）。

默认排除小红书（该平台免签不可下载，只能纯文本推断，与其余视频样本不可比）。

用法：
    python content_eval/build_v3_html.py \
        --preds "content_eval/out/v3_peace/predictions.jsonl,content_eval/out/v3_generic/predictions.jsonl"
"""
from __future__ import annotations

import argparse
import html
import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# 业务口径规则（与 build_v3_report.py 保持一致）
# ---------------------------------------------------------------------------
# 这些标签对在本批数据里口径重叠，Excel 与模型各有各的合理判法，视为同一类：
ACCEPT_PAIRS: set[tuple[str, str]] = {
    ("游戏宣传", "游戏资讯"),
    ("游戏实况", "实况解说"),
    ("直播切片", "实况解说"),
    ("二创类", "创意整活"),
}
ACCEPT_PAIRS |= {(b, a) for a, b in ACCEPT_PAIRS}

PAIR_NOTE = {
    ("游戏宣传", "游戏资讯"): "官方物料里「品牌宣传」与「告知型资讯」常同时存在"
                            "（如「剧情PV + 4月29日正式上线资讯」）",
    ("游戏实况", "实况解说"): "同为单局对局记录，差别只在主播讲解的连续程度",
    ("直播切片", "实况解说"): "直播录屏剪出的对局片段，是否算「直播」取决于 UI 是否入镜",
    ("二创类", "创意整活"): "都是对游戏素材的再创作，差别只在表现形式"
                           "（动画/手绘 vs 剧情整活）",
}

# 宽口径样本（label_broad）：「暗区」表的人工类型是官方物料统称，
# 一个大筐内部横跨多个 24 类标签，无法一一对应，只要有预测即算对。
BROAD_EXEMPT = True

LINK_TPL = {
    "抖音": "https://www.iesdouyin.com/share/video/{id}",
    "快手": "https://www.kuaishou.com/short-video/{id}",
    "小红书": "https://www.xiaohongshu.com/discovery/item/{id}",
}


def read_jsonl(p: Path) -> list[dict]:
    if not p.is_file():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def esc(s) -> str:
    return html.escape(str(s or ""))


# --------------------------------------------------------------------------
# SVG 图表
# --------------------------------------------------------------------------
def svg_bars(items: list[tuple[str, float, int, int]], width: int = 760) -> str:
    """水平条：(名称, 准确率, 正确数, 总数)。"""
    pad_l, pad_r, row_h, bar_h = 200, 86, 40, 20
    height = len(items) * row_h + 40
    plot_w = width - pad_l - pad_r
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
           f'aria-label="分工作表准确率">']
    for g in (0, 0.25, 0.5, 0.75, 1.0):
        x = pad_l + plot_w * g
        out.append(f'<line x1="{x:.1f}" y1="24" x2="{x:.1f}" y2="{height-16}" class="grid"/>')
        out.append(f'<text x="{x:.1f}" y="16" class="axis" text-anchor="middle">{g:.0%}</text>')
    for i, (name, acc, c, n) in enumerate(items):
        y = 30 + i * row_h
        out.append(f'<text x="{pad_l-10}" y="{y+15}" class="lbl" text-anchor="end">{esc(name)}</text>')
        w = max(1.0, plot_w * acc)
        out.append(f'<rect x="{pad_l}" y="{y}" width="{w:.1f}" height="{bar_h}" rx="3" '
                   f'class="bar-model"><title>{esc(name)}：{c}/{n}</title></rect>')
        out.append(f'<text x="{pad_l+w+8:.1f}" y="{y+15}" class="val">'
                   f'{acc:.1%} <tspan class="axis">({c}/{n})</tspan></text>')
    out.append('</svg>')
    return "\n".join(out)


def svg_confusion(cov: list[dict], preds: dict, top_n: int = 12) -> str:
    """混淆矩阵热力图：行=真值，列=预测。"""
    labs = [l for l, _ in Counter(r["label"] for r in cov).most_common(top_n)]
    idx = {l: i for i, l in enumerate(labs)}
    n = len(labs)
    mat = [[0] * n for _ in range(n)]
    for r in cov:
        g, p = r["label"], preds[r["id"]]
        if g in idx and p in idx:
            mat[idx[g]][idx[p]] += 1
    mx = max((max(row) for row in mat), default=1) or 1

    cell, pad_l, pad_t = 38, 112, 104
    # 列头斜排 -55° 且 text-anchor=start，最后一列文字会向右上延伸，
    # 右边距留 16px 会把「创意剪辑」这类 4 字标签裁掉。
    pad_r = 64
    width = pad_l + n * cell + pad_r
    height = pad_t + n * cell + 16
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="混淆矩阵">']
    for j, l in enumerate(labs):
        x = pad_l + j * cell + cell / 2
        out.append(f'<text x="{x}" y="{pad_t-10}" class="axis" text-anchor="start" '
                   f'transform="rotate(-55 {x} {pad_t-10})">{esc(l)}</text>')
    for i, l in enumerate(labs):
        y = pad_t + i * cell
        out.append(f'<text x="{pad_l-8}" y="{y+cell/2+4}" class="lbl" text-anchor="end">{esc(l)}</text>')
        for j in range(n):
            v = mat[i][j]
            x = pad_l + j * cell
            if v == 0:
                fill, tc = "var(--cell0)", "transparent"
            elif i == j:
                a = 0.25 + 0.75 * (v / mx)
                fill, tc = f"rgba(15,118,110,{a:.2f})", "#fff" if a > 0.55 else "var(--ink)"
            else:
                a = 0.2 + 0.8 * (v / mx)
                fill, tc = f"rgba(185,28,28,{a:.2f})", "#fff" if a > 0.55 else "var(--ink)"
            out.append(f'<rect x="{x+1}" y="{y+1}" width="{cell-2}" height="{cell-2}" '
                       f'rx="4" fill="{fill}"><title>{esc(labs[i])} → {esc(labs[j])}：{v} 条</title></rect>')
            if v:
                out.append(f'<text x="{x+cell/2}" y="{y+cell/2+4}" text-anchor="middle" '
                           f'class="cellnum" fill="{tc}">{v}</text>')
    out.append('</svg>')
    return "\n".join(out)


def svg_precision(items: list[tuple[str, int, float, int, int]], width: int = 820) -> str:
    """分标签精确率：(标签, 支持数, 精确率, tp, tp+fp)。"""
    # 右侧要放得下「99% (130/131，真值 131)」这样的标注
    pad_l, pad_r, row_h, bar_h = 120, 190, 30, 16
    height = len(items) * row_h + 38
    plot_w = width - pad_l - pad_r
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
           f'aria-label="分标签精确率">']
    for g in (0, 0.25, 0.5, 0.75, 1.0):
        x = pad_l + plot_w * g
        out.append(f'<line x1="{x:.1f}" y1="24" x2="{x:.1f}" y2="{height-14}" class="grid"/>')
        out.append(f'<text x="{x:.1f}" y="16" class="axis" text-anchor="middle">{g:.0%}</text>')
    for i, (lab, sup, prec, tp, pp) in enumerate(items):
        y = 30 + i * row_h
        out.append(f'<text x="{pad_l-10}" y="{y+12}" class="lbl" text-anchor="end">{esc(lab)}</text>')
        w = max(1.0, plot_w * prec)
        cls = "bar-prec" if prec >= 0.5 else "bar-bad"
        out.append(f'<rect x="{pad_l}" y="{y}" width="{w:.1f}" height="{bar_h}" rx="3" '
                   f'class="{cls}"><title>{esc(lab)}：预测 {pp} 次，对 {tp} 次</title></rect>')
        out.append(f'<text x="{pad_l+w+8:.1f}" y="{y+12}" class="val">{prec:.0%} '
                   f'<tspan class="axis">({tp}/{pp}，真值 {sup})</tspan></text>')
    out.append('</svg>')
    return "\n".join(out)


def svg_donut(parts: list[tuple[str, int, str]], size: int = 190) -> str:
    total = sum(v for _, v, _ in parts) or 1
    r, cx, cy, sw = size / 2 - 16, size / 2, size / 2, 22
    circ = 2 * 3.14159265 * r
    out = [f'<svg viewBox="0 0 {size} {size}" width="{size}" height="{size}">']
    off = 0.0
    for name, v, color in parts:
        frac = v / total
        out.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{color}" '
                   f'stroke-width="{sw}" stroke-dasharray="{circ*frac:.2f} {circ:.2f}" '
                   f'stroke-dashoffset="{-off:.2f}" transform="rotate(-90 {cx} {cy})">'
                   f'<title>{esc(name)}：{v}（{frac:.1%}）</title></circle>')
        off += circ * frac
    out.append(f'<text x="{cx}" y="{cy-2}" text-anchor="middle" class="donut-num">{total}</text>')
    out.append(f'<text x="{cx}" y="{cy+15}" text-anchor="middle" class="axis">样本</text>')
    out.append('</svg>')
    return "\n".join(out)


# --------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gt", default=str(ROOT / "data_v3" / "ground_truth.jsonl"))
    ap.add_argument("--preds", required=True)
    ap.add_argument("--meta", default=str(ROOT / "data_v3" / "media_meta.json"))
    ap.add_argument("--out", default=str(ROOT / "out" / "v3_评测报告.html"))
    ap.add_argument("--exclude-platform", default="小红书",
                    help="逗号分隔；默认排除小红书（无法下载，只能纯文本推断）")
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

    labeled = [r for r in rows if r["label"]]
    cov_all = [r for r in labeled if r["id"] in preds]
    # 默认排除纯文本样本：视频未下载成功时模型只看得到描述，
    # 与看视频判断不是同一件事，混在一起会把准确率读成「视频理解能力」。
    n_txt = sum(1 for r in cov_all if not has_video(r))
    cov = cov_all if args.include_no_media else [r for r in cov_all if has_video(r)]

    # 人工复核覆盖的样本（calibrate_v3.py 打的标记）——这部分只对本批有效，
    # 必须和模型+泛化规则的成绩分开报，否则会把不可外推的数字当成模型能力。
    reviewed = {r["id"] for r in cov if pred_obj[r["id"]].get("manual_review")}

    def same(r: dict) -> bool:
        return preds[r["id"]] == r["label"]

    # 宽口径样本：「暗区」表的人工类型是官方物料统称，内部横跨多个 24 类标签
    broad = [r for r in cov if r.get("label_broad")]

    def exempt(r: dict) -> bool:
        """是否被业务口径规则判为同一类。"""
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
    acc = n_ok / len(cov) if cov else 0

    # 剔除人工复核后的成绩 = 模型 + 泛化规则的真实水平
    def ok_model(r: dict) -> bool:
        """把人工复核还原成复核前的预测再判。"""
        if r["id"] in reviewed:
            before = pred_obj[r["id"]].get("label_before_review", "")
            return (before == r["label"] or r.get("label_broad")
                    or (r["label"], before) in ACCEPT_PAIRS)
        return ok(r)

    n_model = sum(1 for r in cov if ok_model(r))
    acc_model = n_model / len(cov) if cov else 0

    def link_of(r: dict) -> str:
        return r.get("link") or LINK_TPL.get(r["platform"], "{id}").format(id=r["id"])

    # ---- 分表 ----
    sheets = []
    for sh in dict.fromkeys(r["sheet"] for r in cov):
        sub = [r for r in cov if r["sheet"] == sh]
        m = sum(1 for r in sub if ok(r))
        n_drop = sum(1 for r in cov_all if r["sheet"] == sh) - len(sub)
        sheets.append(dict(sheet=sh, platform=sub[0]["platform"], game=sub[0]["game_title"],
                           n=len(sub), acc=m / len(sub), correct=m, n_drop=n_drop,
                           few=len(sub) < 20))
    sheets.sort(key=lambda d: -d["n"])

    # ---- 分标签精确率（业务口径）----
    prec_items = []
    for lab, n in Counter(r["label"] for r in cov).most_common():
        pred_as = [r for r in cov if preds[r["id"]] == lab]
        tp = sum(1 for r in pred_as if ok(r))
        prec_items.append((lab, n, tp / len(pred_as) if pred_as else 0.0, tp, len(pred_as)))

    pairs = Counter((r["label"], preds[r["id"]]) for r in cov if not same(r))

    # ---- 错误明细 ----
    errs = []
    for r in cov:
        if ok(r):
            continue
        x = pred_obj[r["id"]]
        errs.append({
            "id": r["id"], "sheet": r["sheet"], "platform": r["platform"],
            "gt": r["label"], "pred": preds[r["id"]],
            "pair": f'{r["label"]} → {preds[r["id"]]}',
            "desc": (r.get("desc") or "").replace("\n", " ")[:200],
            "author": r.get("author", ""), "link": link_of(r),
            "dur": int((meta.get(r["id"]) or {}).get("duration") or 0),
            "reason": (x.get("reason") or "")[:300],
            "broad": bool(r.get("label_broad")),
            "raw": r.get("label_raw", ""),
        })
    errs.sort(key=lambda e: (-pairs[(e["gt"], e["pred"])], e["pair"], e["id"]))


    plat = Counter(r["platform"] for r in rows)
    donut = svg_donut([("抖音", plat.get("抖音", 0), "#0f766e"),
                       ("快手", plat.get("快手", 0), "#f59e0b")])
    chart1 = svg_bars([(f'{s["sheet"]}', s["acc"], s["correct"], s["n"]) for s in sheets])
    chart2 = svg_confusion(cov, preds)
    chart3 = svg_precision(prec_items[:14])

    errs_json = json.dumps(errs, ensure_ascii=False).replace("</", "<\\/")

    err_pairs = Counter(e["pair"] for e in errs)
    pair_opts = "".join(f'<option value="{esc(k)}">{esc(k)}（{n}）</option>'
                        for k, n in err_pairs.most_common())
    sheet_opts = "".join(f'<option value="{esc(s["sheet"])}">{esc(s["sheet"])}</option>'
                         for s in sheets)

    sheet_rows = "".join(
        f'<tr><td>{esc(s["sheet"])}'
        + (' <span class="chip warnchip">样本过少</span>' if s["few"] else '')
        + f'</td><td>{esc(s["platform"])}</td><td>{esc(s["game"])}</td>'
        f'<td class="num">{s["n"]}'
        + (f'<div class="reason">原 {s["n"]+s["n_drop"]} 条，排除 {s["n_drop"]} 条纯文本</div>'
           if s["n_drop"] else '')
        + f'</td><td class="num">{s["correct"]}</td>'
        f'<td class="num"><b>{s["acc"]:.1%}</b></td></tr>' for s in sheets)

    pair_rows = "".join(
        f'<tr><td>{esc(a)} → {esc(b)}</td><td class="num">{n}</td>'
        f'<td>{"<span class='chip ok'>✅ 算对</span>" if (a, b) in ACCEPT_PAIRS else "<span class='muted'>计错</span>"}</td>'
        f'<td><div class="barwrap"><div class="bar" style="width:{n/max(pairs.values())*100:.0f}%"></div></div></td></tr>'
        for (a, b), n in pairs.most_common(15))

    notes = []
    if dropped:
        notes.append(f"已排除小红书 {dropped} 条（免签不可下载）")
    if n_txt and not args.include_no_media:
        notes.append(f"已排除 {n_txt} 条视频未下载的纯文本样本")
    drop_note = ("（" + "、".join(notes) + "）") if notes else ""

    two_layer_note = ""
    if reviewed:
        two_layer_note = f"""
  <div class="alert">
    <b>这份报告里有两个数字，用途不同，请不要混用。</b>
    <table style="margin-top:10px">
      <tr><th>指标</th><th class="num">数值</th><th>含义与适用范围</th></tr>
      <tr><td><b>模型 + 泛化规则</b></td><td class="num"><b>{acc_model:.2%}</b></td>
          <td>模型分析视频内容，再结合话题标签与作者信息做规则校正。规则经 dev/test 验证，
              <b>换一批新视频依然有效</b>——评估模型能力请用这个数。</td></tr>
      <tr><td><b>交付准确率</b></td><td class="num"><b>{acc:.2%}</b></td>
          <td>在上一行基础上，叠加对 {len(reviewed)} 条样本的逐条人工复核结论。
              <b>只对本批已复核视频有效，对新视频贡献为零</b>——
              用于交付这批数据的标注结果。</td></tr>
    </table>
    <p style="margin:10px 0 0">实测依据：把 dev 集 76 条错误各写一条 ID 规则，
       dev 从 75.48% 升到 100%，但 <b>test 集提升 +0 条</b>。
       逐条复核没有泛化能力，因此必须单独列出。</p>
  </div>
"""

    # 业务口径规则表
    rule_rows = ""
    for a, b in sorted({tuple(sorted(x)) for x in ACCEPT_PAIRS}):
        n = sum(1 for r in cov if not ok(r) and not r.get("label_broad")
                and (r["label"], preds[r["id"]]) in {(a, b), (b, a)})
        note = PAIR_NOTE.get((a, b)) or PAIR_NOTE.get((b, a)) or ""
        rule_rows += (f'<tr><td><span class="chip gt">{esc(a)}</span>'
                      f'<span class="chip pred">{esc(b)}</span></td>'
                      f'<td>{esc(note)}</td><td class="num">{n}</td></tr>')
    n_broad_ex = sum(1 for r in cov if not ok(r) and r.get("label_broad"))
    if broad:
        rule_rows += (f'<tr><td><span class="chip warnchip">宽口径豁免</span></td>'
                      f'<td>「暗区」表的人工类型是官方物料统称，一个大筐横跨多个 24 类标签'
                      f'（共 {len(broad)} 条），只要有预测即算对</td>'
                      f'<td class="num">{n_broad_ex}</td></tr>')

    html_doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>v3 内容分类评测报告</title>
<style>
  :root {{ --bg:#f7f8fa; --panel:#fff; --ink:#111827; --muted:#6b7280; --line:#e5e7eb;
          --gt:#b91c1c; --gtbg:#fee2e2; --pred:#1d4ed8; --predbg:#dbeafe; --accent:#0f766e;
          --ok:#047857; --warn:#b45309; --warnbg:#fef3c7; --cell0:#f1f5f9; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#0b0f14; --panel:#131a22; --ink:#e5e7eb; --muted:#9ca3af; --line:#263241;
             --gtbg:#3f1d1d; --predbg:#172554; --gt:#fca5a5; --pred:#93c5fd; --ok:#6ee7b7;
             --warnbg:#3b2f0b; --warn:#fcd34d; --cell0:#1b232e; }}
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink); line-height:1.65;
         font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif; }}
  .wrap {{ max-width:1120px; margin:0 auto; padding:36px 20px 60px; }}
  h1 {{ font-size:28px; margin:0 0 6px; }}
  h2 {{ font-size:20px; margin:40px 0 12px; border-bottom:1px solid var(--line); padding-bottom:6px; }}
  h3 {{ font-size:15px; margin:22px 0 8px; color:var(--muted); font-weight:600; }}
  .sub {{ color:var(--muted); margin:0 0 24px; }}
  .cards {{ display:grid; grid-template-columns:repeat(4,1fr); gap:14px; margin:20px 0; }}
  .stat {{ background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:18px; }}
  .stat .num {{ font-size:30px; font-weight:700; color:var(--accent); line-height:1.2; }}
  .stat .lbl {{ font-size:13px; color:var(--muted); }}
  .stat .note {{ font-size:12px; color:var(--muted); margin-top:6px; }}
  .panel {{ background:var(--panel); border:1px solid var(--line); border-radius:14px;
           padding:18px; margin:14px 0; }}
  .flex {{ display:flex; gap:18px; align-items:center; flex-wrap:wrap; }}
  table {{ width:100%; border-collapse:collapse; font-size:14px; }}
  th, td {{ text-align:left; padding:8px 10px; border-bottom:1px solid var(--line);
           vertical-align:top; }}
  th {{ color:var(--muted); font-weight:600; font-size:12px; position:sticky; top:0;
       background:var(--panel); }}
  td.num, th.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  tr.off td {{ background:var(--warnbg); }}
  .muted {{ color:var(--muted); }}
  .chip {{ display:inline-block; font-size:12px; padding:2px 8px; border-radius:999px;
          margin-right:6px; white-space:nowrap; }}
  .chip.gt {{ background:var(--gtbg); color:var(--gt); }}
  .chip.pred {{ background:var(--predbg); color:var(--pred); }}
  .chip.mode {{ background:var(--line); color:var(--muted); }}
  .chip.warnchip {{ background:var(--warnbg); color:var(--warn); }}
  .chip.ok {{ background:rgba(4,120,87,.14); color:var(--ok); }}
  .barwrap {{ background:var(--line); border-radius:999px; height:14px; min-width:120px; }}
  .bar {{ height:100%; background:var(--accent); border-radius:999px; }}
  a.vid {{ color:var(--accent); text-decoration:none; font-size:12px; white-space:nowrap;
          border:1px solid var(--accent); padding:2px 8px; border-radius:6px; }}
  a.vid:hover {{ background:var(--accent); color:#fff; }}
  .desc {{ font-size:13px; margin:4px 0; }}
  .reason {{ font-size:12px; color:var(--muted); margin-top:4px; }}
  .legend {{ font-size:12px; color:var(--muted); margin:8px 0 2px; }}
  .legend i {{ display:inline-block; width:11px; height:11px; border-radius:3px;
              margin:0 4px 0 12px; vertical-align:middle; }}
  svg text.lbl {{ font-size:12px; fill:var(--ink); }}
  svg text.axis {{ font-size:11px; fill:var(--muted); }}
  svg text.val {{ font-size:11px; fill:var(--ink); font-variant-numeric:tabular-nums; }}
  svg text.cellnum {{ font-size:11px; font-variant-numeric:tabular-nums; }}
  svg text.donut-num {{ font-size:22px; font-weight:700; fill:var(--ink); }}
  svg line.grid {{ stroke:var(--line); stroke-width:1; }}
  svg .bar-model {{ fill:var(--accent); }}
  svg .bar-prec {{ fill:var(--pred); }}
  svg .bar-bad {{ fill:var(--gt); }}
  .ctrl {{ display:flex; gap:10px; flex-wrap:wrap; margin:12px 0; }}
  .cm svg {{ max-width:760px; display:block; margin:0 auto; }}
  select, input {{ font:inherit; font-size:13px; padding:6px 10px; border-radius:8px;
                  border:1px solid var(--line); background:var(--panel); color:var(--ink); }}
  footer {{ color:var(--muted); font-size:12px; margin-top:46px; }}
  @media (max-width:760px) {{ .cards {{ grid-template-columns:repeat(2,1fr); }} }}
</style>
</head>
<body>
<div class="wrap">
  <h1>v3 内容分类评测报告</h1>
  <p class="sub">工作簿1.xlsx · {len(rows)} 条 · 抖音 / 快手 ·
     模型 gemini-2.5-flash · 24 类标签 · {date.today()}<br>{drop_note}</p>

  {two_layer_note}

  <div class="cards">
    <div class="stat"><div class="num">{acc:.1%}</div><div class="lbl">交付准确率</div>
      <div class="note">{n_ok}/{len(cov)}，含人工复核 {len(reviewed)} 条</div></div>
    <div class="stat"><div class="num">{acc_model:.1%}</div><div class="lbl">模型 + 泛化规则</div>
      <div class="note">{n_model}/{len(cov)}，<b>这个才是可外推的水平</b></div></div>
    <div class="stat"><div class="num">{n_ex}</div><div class="lbl">口径规则生效</div>
      <div class="note">被判为同一类的样本数</div></div>
    <div class="stat"><div class="num">{len(errs)}</div><div class="lbl">错误样本</div>
      <div class="note">可逐条点开复核</div></div>
  </div>

  <h2>一、业务口径规则</h2>
  <p class="sub">以下标签对在本批数据里口径重叠，Excel 与模型各有各的合理判法，
     <b>视为同一类，两边都算对</b>。</p>
  <div class="panel" style="overflow-x:auto">
    <table>
      <tr><th>规则</th><th>说明</th><th class="num">生效条数</th></tr>
      {rule_rows}
    </table>
    <p class="reason" style="margin-top:10px">合计 <b>{n_ex}</b> 条由规则判为同一类。</p>
  </div>

  <h2>二、分工作表准确率</h2>
  <div class="panel">{chart1}</div>
  <div class="panel" style="overflow-x:auto">
    <table>
      <tr><th>工作表</th><th>平台</th><th>游戏</th><th class="num">样本</th>
          <th class="num">正确</th><th class="num">准确率</th></tr>
      {sheet_rows}
    </table>
  </div>

  <h2>三、数据构成</h2>
  <div class="panel flex">
    {donut}
    <div style="flex:1;min-width:280px">
      <table>
        <tr><th>平台</th><th class="num">样本</th><th>下载情况</th></tr>
        <tr><td>抖音</td><td class="num">{plat.get("抖音",0)}</td>
            <td>387 成功，<b>121 条接口未返回</b>（已删除/私密）</td></tr>
        <tr><td>快手</td><td class="num">{plat.get("快手",0)}</td><td>60 成功，2 条无直链</td></tr>
      </table>
    </div>
  </div>

  <h2>四、混淆矩阵</h2>
  <p class="sub">行 = Excel 真值，列 = 模型预测。<span style="color:var(--accent)">绿色对角线</span>为判对，
     <span style="color:var(--gt)">红色</span>为误判，颜色越深数量越多。鼠标悬停看具体条数。</p>
  <div class="panel cm" style="overflow-x:auto">{chart2}</div>

  <h3>高频混淆对</h3>
  <div class="panel"><table>
    <tr><th>真值 → 预测</th><th class="num">次数</th><th>业务口径</th><th style="width:30%"></th></tr>
    {pair_rows}
  </table></div>

  <h2>五、分标签精确率</h2>
  <p class="sub">精确率 = 模型预测为该标签时，业务口径下判对的比例。括号内为「判对数 / 预测次数，真值条数」。
     <span style="color:var(--gt)">红色</span>表示低于 50%。</p>
  <div class="panel">{chart3}</div>

  <h2>六、错误明细（{len(errs)} 条）</h2>
  <p class="sub">已排除被口径规则判为同一类的 {n_ex} 条。</p>
  <div class="ctrl">
    <select id="fs"><option value="">全部工作表</option>{sheet_opts}</select>
    <select id="fp"><option value="">全部混淆对</option>{pair_opts}</select>
    <input id="q" type="search" placeholder="搜索描述/作者/ID…" style="flex:1;min-width:180px">
    <span class="muted" id="cnt" style="align-self:center;font-size:13px"></span>
  </div>
  <div class="panel" style="overflow-x:auto"><table id="tbl"></table></div>

  <footer>
    视频链接指向各平台作品页，可直接点开复核。<br>
    生成脚本：<code>content_eval/build_v3_html.py</code>　·　
    Markdown 版：<code>content_eval/out/v3_评测报告.md</code>
  </footer>
</div>

<script>
const ERRS = {errs_json};
const el = id => document.getElementById(id);
const esc = s => String(s==null?'':s).replace(/[&<>"]/g, c =>
  ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]));
const dur = d => d ? Math.floor(d/60)+':'+String(Math.floor(d%60)).padStart(2,'0') : '-';
const vid = l => `<a class="vid" href="${{esc(l)}}" target="_blank" rel="noopener">打开 ▸</a>`;

function renderErrs() {{
  const s = el('fs').value, p = el('fp').value, q = el('q').value.trim().toLowerCase();
  const rows = ERRS.filter(e =>
    (!s || e.sheet === s) && (!p || e.pair === p) &&
    (!q || (e.desc+e.author+e.id).toLowerCase().includes(q)));
  el('cnt').textContent = `显示 ${{rows.length}} / ${{ERRS.length}} 条`;
  el('tbl').innerHTML =
    '<tr><th>视频</th><th>工作表</th><th>真值 → 预测</th><th class="num">时长</th>' +
    '<th>描述 / 模型判断依据</th></tr>' +
    rows.map(e => `<tr class="${{e.broad?'off':''}}">
      <td>${{vid(e.link)}}<div class="reason">${{esc(e.platform)}}</div></td>
      <td>${{esc(e.sheet)}}<div class="reason">${{esc(e.author)}}</div></td>
      <td><span class="chip gt">${{esc(e.gt)}}</span><br><span class="chip pred">${{esc(e.pred)}}</span>
          ${{e.broad ? `<div class="reason"><span class="chip warnchip">宽口径</span>人工：${{esc(e.raw)}}</div>` : ''}}</td>
      <td class="num">${{dur(e.dur)}}</td>
      <td><div class="desc">${{esc(e.desc)}}</div>
          <div class="reason">模型：${{esc(e.reason)}}</div></td></tr>`).join('');
}}

['fs','fp','q'].forEach(i => el(i).addEventListener('input', renderErrs));
renderErrs();
</script>
</body>
</html>"""

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_doc, encoding="utf-8")
    print(f"排除 {dropped} 条（{'/'.join(drop)}），剩 {len(rows)} 条")
    print(f"交付准确率 {n_ok}/{len(cov)} = {acc:.2%}"
          + (f"（含人工复核 {len(reviewed)} 条）" if reviewed else ""))
    if reviewed:
        print(f"模型+泛化规则 {n_model}/{len(cov)} = {acc_model:.2%}  ← 可外推")
    if n_txt and not args.include_no_media:
        print(f"  已排除纯文本样本 {n_txt} 条（视频未下载）")
    print(f"HTML -> {out}  ({out.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    main()
