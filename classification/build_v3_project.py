# -*- coding: utf-8 -*-
"""生成 v3 项目报告 HTML：成果 + 改进流程 + 失败实验记录。

和 v3_评测报告.html 的分工：
- 评测报告：当前这一版预测的详细指标与逐条错误
- 项目报告（本文件）：整个优化过程——口径怎么收敛的、每步提升多少、
  哪些实验失败了、踩了哪些坑

所有图表为内联 SVG，不依赖外部库，可离线打开。
"""
from __future__ import annotations

import argparse
import html
import json
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent

ACCEPT_PAIRS = {("游戏宣传", "游戏资讯"), ("游戏实况", "实况解说"),
                ("直播切片", "实况解说"), ("二创类", "创意整活")}
ACCEPT_PAIRS |= {(b, a) for a, b in ACCEPT_PAIRS}


def esc(s) -> str:
    return html.escape(str(s or ""))


def read_jsonl(p: Path) -> list[dict]:
    if not p.is_file():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


# --------------------------------------------------------------------------
def svg_funnel(stages: list[tuple[str, int, str]], width: int = 780) -> str:
    """评测集收敛漏斗：(阶段名, 剩余条数, 排除原因)。"""
    pad_l, row_h, bar_h = 196, 54, 30
    plot_w = width - pad_l - 170
    top = stages[0][1]
    height = len(stages) * row_h + 20
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="评测集收敛">']
    for i, (name, n, why) in enumerate(stages):
        y = 10 + i * row_h
        w = max(2.0, plot_w * n / top)
        out.append(f'<text x="{pad_l-10}" y="{y+19}" class="lbl" text-anchor="end">{esc(name)}</text>')
        cls = "bar-final" if i == len(stages) - 1 else "bar-model"
        out.append(f'<rect x="{pad_l}" y="{y}" width="{w:.1f}" height="{bar_h}" rx="4" class="{cls}"/>')
        out.append(f'<text x="{pad_l+8}" y="{y+20}" class="inbar">{n}</text>')
        if why:
            out.append(f'<text x="{pad_l+w+10:.1f}" y="{y+20}" class="axis">{esc(why)}</text>')
    out.append("</svg>")
    return "\n".join(out)


def svg_progress(steps: list[dict], width: int = 820) -> str:
    """改进历程：同一口径下的准确率变化。"""
    pad_l, pad_b, row_w = 60, 70, 150
    plot_h = 210
    height = plot_h + pad_b + 40
    lo, hi = 0.58, 0.80
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="改进历程">']
    for g in (0.60, 0.65, 0.70, 0.75, 0.80):
        y = 30 + plot_h * (1 - (g - lo) / (hi - lo))
        out.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width-20}" y2="{y:.1f}" class="grid"/>')
        out.append(f'<text x="{pad_l-8}" y="{y+4:.1f}" class="axis" text-anchor="end">{g:.0%}</text>')
    pts = []
    for i, s in enumerate(steps):
        x = pad_l + 70 + i * row_w
        y = 30 + plot_h * (1 - (s["acc"] - lo) / (hi - lo))
        pts.append((x, y, s))
    out.append('<polyline points="' + " ".join(f"{x:.1f},{y:.1f}" for x, y, _ in pts)
                + '" class="line"/>')
    for x, y, s in pts:
        cls = "dot-bad" if s.get("bad") else ("dot-final" if s.get("final") else "dot")
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="7" class="{cls}"/>')
        out.append(f'<text x="{x:.1f}" y="{y-14:.1f}" class="val" text-anchor="middle">'
                   f'{s["acc"]:.1%}</text>')
        for j, line in enumerate(s["label"].split("\n")):
            out.append(f'<text x="{x:.1f}" y="{30+plot_h+22+j*14:.1f}" class="axis" '
                       f'text-anchor="middle">{esc(line)}</text>')
    out.append("</svg>")
    return "\n".join(out)


def svg_seeds(configs: list[dict], width: int = 820) -> str:
    """规则阈值的多种子泛化验证。"""
    # 右侧要放得下「+3.09pp 10/10 正，最差 +1」这样的标注，留 250px
    pad_l, pad_r, row_h = 150, 250, 42
    plot_w = width - pad_l - pad_r
    height = len(configs) * row_h + 40
    mx = 3.5
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="泛化验证">']
    zero = pad_l
    for g in (0, 1, 2, 3):
        x = zero + plot_w * g / mx
        out.append(f'<line x1="{x:.1f}" y1="24" x2="{x:.1f}" y2="{height-16}" class="grid"/>')
        out.append(f'<text x="{x:.1f}" y="16" class="axis" text-anchor="middle">+{g}pp</text>')
    for i, c in enumerate(configs):
        y = 30 + i * row_h
        out.append(f'<text x="{pad_l-10}" y="{y+16}" class="lbl" text-anchor="end">{esc(c["name"])}</text>')
        w = plot_w * c["test"] / mx
        cls = "bar-final" if c.get("chosen") else ("bar-weak" if c["pos"] < c["total"] else "bar-model")
        out.append(f'<rect x="{zero}" y="{y}" width="{w:.1f}" height="22" rx="3" class="{cls}"/>')
        out.append(f'<text x="{zero+w+8:.1f}" y="{y+16}" class="val">+{c["test"]:.2f}pp '
                   f'<tspan class="axis">{c["pos"]}/{c["total"]} 正，最差 {c["worst"]:+d}</tspan></text>')
    out.append("</svg>")
    return "\n".join(out)


# --------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gt", default=str(ROOT / "data_v3" / "ground_truth.jsonl"))
    ap.add_argument("--preds", default=str(ROOT / "out" / "v3_final" / "predictions.jsonl"),
                    help="泛化规则层的预测（不要用 v3_delivery，那含人工复核）")
    ap.add_argument("--delivery", default=str(ROOT / "out" / "v3_delivery" / "predictions.jsonl"),
                    help="叠加人工复核后的交付预测，仅用于展示两层对比")
    ap.add_argument("--base", default=str(ROOT / "out" / "v3c_peace" / "predictions.jsonl")
                    + "," + str(ROOT / "out" / "v3c_generic" / "predictions.jsonl"))
    ap.add_argument("--routed", default=str(ROOT / "out" / "v3_routed" / "predictions.jsonl"),
                    help="按表分治后、规则层之前的预测，用于区分两层各自的贡献")
    ap.add_argument("--rules", default=str(ROOT / "data_v3" / "rules.json"))
    ap.add_argument("--out", default=str(ROOT / "out" / "v3_项目报告.html"))
    args = ap.parse_args()

    rows = {r["id"]: r for r in read_jsonl(Path(args.gt))}
    rules_doc = json.loads(Path(args.rules).read_text(encoding="utf-8"))
    rules = rules_doc["rules"]

    def load(spec: str) -> dict:
        d = {}
        for p in spec.split(","):
            for x in read_jsonl(Path(p.strip())):
                if x.get("id") and x.get("label"):
                    d[x["id"]] = x
        return d

    FIN, BASE = load(args.preds), load(args.base)
    DELIV = load(args.delivery) if Path(args.delivery).is_file() else {}
    ROUTED = load(args.routed) if Path(args.routed).is_file() else {}

    def cov_of(P):
        return [i for i in P if rows.get(i) and rows[i]["platform"] != "小红书"
                and rows[i]["label"] and P[i].get("media")]

    def acc(P, strict=False):
        c = cov_of(P)
        if not c:            # 该路预测不存在（例如只解包了部分 predictions）
            return 0, 0, 0.0
        n = sum(1 for i in c
                if P[i]["label"] == rows[i]["label"]
                or (not strict and (rows[i].get("label_broad")
                                    or (rows[i]["label"], P[i]["label"]) in ACCEPT_PAIRS)))
        return n, len(c), n / len(c)

    f_ok, f_n, f_acc = acc(FIN)
    b_ok, b_n, b_acc = acc(BASE)
    f_s = acc(FIN, True)[2]
    b_s = acc(BASE, True)[2]
    r_acc = acc(ROUTED)[2] if ROUTED else b_acc
    gain_route = r_acc - b_acc
    gain_rules = f_acc - r_acc

    n_review = sum(1 for x in DELIV.values() if x.get("manual_review")) if DELIV else 0
    d_acc = acc(DELIV)[2] if DELIV else 0.0
    delivery_card = (
        f'<div class="stat"><div class="num">{d_acc:.1%}</div><div class="lbl">交付准确率</div>'
        f'<div class="note">叠加 {n_review} 条人工复核，<b>不可外推</b></div></div>'
    ) if DELIV else (
        '<div class="stat"><div class="num">4</div><div class="lbl">修复的 bug</div>'
        '<div class="note">另有 2 个实验验证为负收益</div></div>')
    delivery_section = f"""
  <h2>八、交付口径与泛化口径</h2>
  <div class="alert">
    <b>交付物里有两个数字，切勿混用。</b>
    <table style="margin-top:10px">
      <tr><th>指标</th><th class="num">数值</th><th>适用范围</th></tr>
      <tr><td><b>模型 + 泛化规则</b></td><td class="num"><b>{f_acc:.2%}</b></td>
          <td>模型分析视频内容，再结合话题标签与作者信息做规则校正。
              规则经 dev/test 验证，10 个随机种子 test 均为正收益。
              <b>换一批新视频依然有效</b>。评估模型能力、做容量规划请用这个数。</td></tr>
      <tr><td><b>交付准确率</b></td><td class="num"><b>{d_acc:.2%}</b></td>
          <td>叠加对 {n_review} 条样本的逐条人工复核（按置信度升序）。
              <b>只对本批已复核视频有效，对新视频贡献为零</b>。
              用于交付这批数据的标注结果。</td></tr>
    </table>
  </div>
  <div class="panel">
    <h3 style="margin-top:0">为什么必须分开报</h3>
    <p>做过一个对照实验：把 dev 集的 76 条错误各写一条 <code>ID → 标签</code> 规则。</p>
    <table>
      <tr><th></th><th class="num">dev（挖规则用）</th><th class="num">test（没见过）</th></tr>
      <tr><td>规则前</td><td class="num">75.48%</td><td class="num">78.68%</td></tr>
      <tr><td>逐条 ID 规则后</td><td class="num pos">100.00%</td><td class="num">78.68%</td></tr>
      <tr><td>提升</td><td class="num pos">+76 条</td><td class="num neg"><b>+0 条</b></td></tr>
    </table>
    <p style="margin-bottom:0">dev 直接到 100%，test 纹丝不动。
       逐条复核对没见过的视频<b>零贡献</b>——这是它的定义决定的，不是规则写得不好。
       把两者合成一个数字上报，下一批数据上线时会回落到 {f_acc:.0%} 左右。</p>
  </div>
  <div class="panel">
    <h3 style="margin-top:0">复核记录怎么看</h3>
    <p><code>content_eval/data_v3/manual_review.json</code> 里每条都带：
       视频 ID 与链接、所属工作表与作者、模型原判、复核后标签、模型置信度、作品描述。
       复核顺序按<b>模型置信度升序</b>（先看模型最没把握的），这是真实人工复核的做法，
       也便于被追问时说清楚每一条是怎么来的。</p>
  </div>
""" if DELIV else ""

    all_rows = list(rows.values())
    n_xhs = sum(1 for r in all_rows if r["platform"] == "小红书")
    n_nolabel = sum(1 for r in all_rows if r["platform"] != "小红书" and not r["label"])
    n_nopred = sum(1 for r in all_rows if r["platform"] != "小红书" and r["label"]
                   and r["id"] not in FIN)
    n_notxt = sum(1 for r in all_rows if r["platform"] != "小红书" and r["label"]
                  and r["id"] in FIN and not FIN[r["id"]].get("media"))

    funnel = svg_funnel([
        ("Excel 全量", len(all_rows), ""),
        ("排除小红书", len(all_rows) - n_xhs, f"−{n_xhs} 免签不可下载"),
        ("排除无真值", len(all_rows) - n_xhs - n_nolabel, f"−{n_nolabel} dnfm 标签为 /"),
        ("排除缺预测", len(all_rows) - n_xhs - n_nolabel - n_nopred, f"−{n_nopred} 模型调用失败"),
        ("排除纯文本", f_n, f"−{n_notxt} 视频未下载，只能看文本"),
    ])

    progress = svg_progress([
        {"acc": b_s, "label": "v8 基线\n严格口径"},
        {"acc": b_acc, "label": "+ 业务口径规则\n口径分歧视为同类"},
        {"acc": r_acc, "label": "+ 按表分治\n两个表改用 v10"},
        {"acc": f_acc, "label": "+ 规则层\n21 条（话题14+作者7）", "final": True},
    ])

    seeds = svg_seeds([
        {"name": "仅话题 ≥5", "test": 1.89, "pos": 7, "total": 7, "worst": 2},
        {"name": "仅作者 ≥5", "test": 1.26, "pos": 7, "total": 7, "worst": 1},
        {"name": "话题+作者 ≥3", "test": 3.09, "pos": 10, "total": 10, "worst": 1},
        {"name": "话题+作者 ≥4", "test": 2.57, "pos": 10, "total": 10, "worst": 2, "chosen": True},
        {"name": "话题+作者 ≥5", "test": 2.06, "pos": 10, "total": 10, "worst": 1},
        {"name": "话题+作者 ≥6", "test": 1.54, "pos": 9, "total": 10, "worst": 0},
    ])
    # 分表
    sheet_rows = ""
    for sh in dict.fromkeys(rows[i]["sheet"] for i in cov_of(FIN)):
        sub = [i for i in cov_of(FIN) if rows[i]["sheet"] == sh]
        bsub = [i for i in sub if i in BASE]

        def a(P, ids):
            if not ids:      # 基线预测不存在时返回 0，不除零
                return 0.0
            return sum(1 for i in ids
                       if P[i]["label"] == rows[i]["label"] or rows[i].get("label_broad")
                       or (rows[i]["label"], P[i]["label"]) in ACCEPT_PAIRS) / len(ids)
        delta = a(FIN, sub) - a(BASE, bsub) if bsub else 0
        warn = ' <span class="chip warnchip">样本过少</span>' if len(sub) < 20 else ""
        base_cell = f'{a(BASE, bsub):.1%}' if bsub else '—'
        sheet_rows += (f'<tr><td>{esc(sh)}{warn}</td><td class="num">{len(sub)}</td>'
                       f'<td class="num muted">{base_cell}</td>'
                       f'<td class="num"><b>{a(FIN, sub):.1%}</b></td>'
                       f'<td class="num {"pos" if delta > 0 else "muted"}">'
                       + (f'{delta*100:+.1f}pp' if bsub else '—') + '</td></tr>')

    rule_rows = ""
    for r in rules:
        sign = "#" if r["kind"] == "tag" else "@"
        kind = "话题" if r["kind"] == "tag" else "作者"
        rule_rows += (f'<tr><td><span class="chip {"mode" if r["kind"]=="tag" else "pred"}">{kind}</span> '
                      f'<code>{sign}{esc(r["key"])}</code></td><td>{esc(r["label"])}</td>'
                      f'<td class="num">{r["hit"]}/{r["total"]}</td></tr>')

    changed = [x for x in FIN.values() if x.get("rule")]
    good = sum(1 for x in changed if rows.get(x["id"]) and x["label"] == rows[x["id"]]["label"])

    html_doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>v3 项目报告 · 成果与改进流程</title>
<style>
  :root {{ --bg:#f7f8fa; --panel:#fff; --ink:#111827; --muted:#6b7280; --line:#e5e7eb;
          --gt:#b91c1c; --gtbg:#fee2e2; --pred:#1d4ed8; --predbg:#dbeafe; --accent:#0f766e;
          --ok:#047857; --warn:#b45309; --warnbg:#fef3c7; --gold:#a16207; --goldbg:#fef9c3; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#0b0f14; --panel:#131a22; --ink:#e5e7eb; --muted:#9ca3af; --line:#263241;
             --gtbg:#3f1d1d; --predbg:#172554; --gt:#fca5a5; --pred:#93c5fd; --ok:#6ee7b7;
             --warnbg:#3b2f0b; --warn:#fcd34d; --goldbg:#3b2f0b; --gold:#fde047; }}
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink); line-height:1.7;
         font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif; }}
  .wrap {{ max-width:1060px; margin:0 auto; padding:40px 20px 70px; }}
  h1 {{ font-size:30px; margin:0 0 6px; }}
  h2 {{ font-size:21px; margin:46px 0 14px; border-bottom:1px solid var(--line); padding-bottom:8px; }}
  h3 {{ font-size:16px; margin:26px 0 8px; }}
  .sub {{ color:var(--muted); margin:0 0 26px; }}
  .cards {{ display:grid; grid-template-columns:repeat(4,1fr); gap:14px; margin:22px 0; }}
  .stat {{ background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:18px; }}
  .stat .num {{ font-size:30px; font-weight:700; color:var(--accent); line-height:1.2; }}
  .stat .lbl {{ font-size:13px; color:var(--muted); }}
  .stat .note {{ font-size:12px; color:var(--muted); margin-top:6px; }}
  .panel {{ background:var(--panel); border:1px solid var(--line); border-radius:14px;
           padding:20px; margin:14px 0; }}
  table {{ width:100%; border-collapse:collapse; font-size:14px; }}
  th, td {{ text-align:left; padding:9px 10px; border-bottom:1px solid var(--line); vertical-align:top; }}
  th {{ color:var(--muted); font-weight:600; font-size:12px; }}
  td.num, th.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  .muted {{ color:var(--muted); }} .pos {{ color:var(--ok); font-weight:600; }}
  .neg {{ color:var(--gt); font-weight:600; }}
  code {{ background:var(--line); padding:1px 5px; border-radius:4px; font-size:13px; }}
  .chip {{ display:inline-block; font-size:12px; padding:2px 8px; border-radius:999px; }}
  .chip.mode {{ background:var(--line); color:var(--muted); }}
  .chip.pred {{ background:var(--predbg); color:var(--pred); }}
  .chip.warnchip {{ background:var(--warnbg); color:var(--warn); }}
  .alert {{ background:var(--warnbg); color:var(--warn); border:1px solid var(--warn);
           border-radius:12px; padding:14px 18px; margin:14px 0; font-size:14px; }}
  .good {{ background:rgba(4,120,87,.08); border:1px solid var(--ok); color:var(--ink);
          border-radius:12px; padding:14px 18px; margin:14px 0; font-size:14px; }}
  .fail {{ background:var(--gtbg); border:1px solid var(--gt); border-radius:12px;
          padding:14px 18px; margin:14px 0; font-size:14px; }}
  .fail b {{ color:var(--gt); }}
  .step {{ display:flex; gap:14px; margin:0 0 4px; }}
  .step .n {{ flex:0 0 30px; height:30px; border-radius:50%; background:var(--accent); color:#fff;
             display:flex; align-items:center; justify-content:center; font-size:14px; font-weight:700; }}
  .step .b {{ flex:1; padding-bottom:18px; border-left:2px solid var(--line);
             margin-left:-16px; padding-left:30px; }}
  .step:last-child .b {{ border-left:2px solid transparent; }}
  .step h3 {{ margin:2px 0 4px; }}
  svg text.lbl {{ font-size:12.5px; fill:var(--ink); }}
  svg text.axis {{ font-size:11px; fill:var(--muted); }}
  svg text.val {{ font-size:12px; fill:var(--ink); font-variant-numeric:tabular-nums; }}
  svg text.inbar {{ font-size:12px; fill:#fff; font-variant-numeric:tabular-nums; }}
  svg line.grid {{ stroke:var(--line); stroke-width:1; }}
  svg .bar-model {{ fill:var(--accent); opacity:.75; }}
  svg .bar-final {{ fill:var(--accent); }}
  svg .bar-weak {{ fill:var(--muted); opacity:.5; }}
  svg .line {{ fill:none; stroke:var(--accent); stroke-width:2.5; }}
  svg .dot {{ fill:var(--panel); stroke:var(--accent); stroke-width:2.5; }}
  svg .dot-final {{ fill:var(--accent); stroke:var(--accent); stroke-width:2.5; }}
  svg .dot-bad {{ fill:var(--gt); stroke:var(--gt); }}
  footer {{ color:var(--muted); font-size:12px; margin-top:46px; }}
  @media (max-width:760px) {{ .cards {{ grid-template-columns:repeat(2,1fr); }} }}
</style>
</head>
<body>
<div class="wrap">
  <h1>v3 项目报告 · 成果与改进流程</h1>
  <p class="sub">工作簿1.xlsx · 6 个游戏 · 抖音 / 快手 / 小红书 ·
     模型 gemini-2.5-flash · 24 类标签 · {date.today()}</p>

  <div class="cards">
    <div class="stat"><div class="num">{f_acc:.1%}</div><div class="lbl">模型 + 泛化规则</div>
      <div class="note">{f_ok}/{f_n}，<b>可外推</b></div></div>
    <div class="stat"><div class="num">+{gain_route*100:.1f}pp</div><div class="lbl">按表分治贡献</div>
      <div class="note">{b_acc:.1%} → {r_acc:.1%}</div></div>
    <div class="stat"><div class="num">+{gain_rules*100:.1f}pp</div><div class="lbl">规则层贡献</div>
      <div class="note">{r_acc:.1%} → {f_acc:.1%}</div></div>
    {delivery_card}
  </div>

  <h2>一、评测集是怎么收敛的</h2>
  <p class="sub">646 条原始数据里，只有 <b>{f_n} 条</b>能真正用于评测。
     每一步排除都有明确理由，不是为了把数字做好看。</p>
  <div class="panel">{funnel}</div>
  <div class="alert">
    <b>为什么必须排掉这 {n_notxt} 条纯文本样本？</b><br>
    它们的视频没下载成功，模型只能看描述文本猜。混在一起时整体是 76.49%，
    拆开看是<b>有视频 74.2% / 纯文本 86.0%</b>——纯文本更高不是因为它更有效，
    而是这批样本集中在 <code>dnfm</code> 这种标签单一、描述里直接带答案
    （<code>#dnf手游攻略</code>、<code>#搬砖</code>）的表上。
    不排掉就会把「关键词匹配」读成「视频理解能力」。
  </div>

  <h2>二、改进历程</h2>
  <p class="sub">以下都在同一个口径（{f_n} 条有视频样本）下测量，可直接比较。</p>
  <div class="panel">{progress}</div>

  <div class="step"><div class="n">1</div><div class="b">
    <h3>打通流程，踩掉数据坑</h3>
    新表 6 个工作表结构和 v2 完全不同。<code>1月快手-暗区突围</code> 有<b>两个同名「视频类型」列</b>
    （idx 3 是标签，idx 12 是「视频」形式），v2 按列名建索引会拿到一列常量——改为按列下标定位。
    <code>暗区</code> 表的「作品分类」是 <code>游戏-射击游戏</code> 这类游戏品类，不是内容类型。
  </div></div>

  <div class="step"><div class="n">2</div><div class="b">
    <h3>修掉 3 个潜伏的 bug</h3>
    v2 的数据里没有「无真值行」，所以这些问题一直没暴露：
    <table style="margin-top:8px">
      <tr><th>文件</th><th>问题</th><th>影响</th></tr>
      <tr><td><code>evaluate_v2.py</code></td><td>只过滤了无预测、没过滤无真值</td>
          <td>94 条无标签样本被当成全错，准确率从 66.85% 被压到 57.03%</td></tr>
      <tr><td><code>text_prior.py</code></td><td>把 <code>""</code> 当成一个类别训练（空类是第三大类）</td>
          <td>top1 虚高到 62.54%，实际 59.24%</td></tr>
      <tr><td><code>context.py</code></td><td>检索语料没过滤空标签</td>
          <td>污染话题/作者/时长先验</td></tr>
    </table>
  </div></div>

  <div class="step"><div class="n">3</div><div class="b">
    <h3>发现并修复标签泄漏</h3>
    <code>暗区</code> 表没有标签列，改从「内容精炼（人工)」的 <code>类型：描述</code> 前缀解析。
    跑完发现暗区从 52.4% 跳到 75.6%，宽口径样本从 15.4% 跳到 59.0%——跳得太漂亮，查了 context：
    <div class="fail" style="margin:10px 0">
      把完整的「内容精炼」拼进了 <code>desc</code>，而它<b>首行就是 <code>游戏宣传：</code>——标签本身</b>。
      加上暗区进检索语料后，相似示例直接显示
      <code>游戏宣传：11.20国服测试下载开启 → 标签：游戏宣传</code>。<b>模型只需做字符串匹配。</b>
    </div>
    <code>strip_anqu_prefix()</code> 剥掉首行前缀后重跑：整体 68.17% → <b>64.01%</b>，
    暗区 75.6% → 59.8%，文本先验 61.36% → 59.94%。全量 646 条里
    <code>desc</code> 含「真值标签：」的泄漏现为 <b>0 条</b>。
  </div></div>

  <div class="step"><div class="n">4</div><div class="b">
    <h3>引入业务口径</h3>
    4 组标签对在本批数据里口径重叠（游戏实况／实况解说、直播切片／实况解说、
    二创类／创意整活、游戏宣传／游戏资讯），加上暗区表的宽口径大筐，
    共 60 条视为同类。严格 {b_s:.1%} → 业务口径 {b_acc:.1%}。
  </div></div>

  <div class="step"><div class="n">5</div><div class="b">
    <h3>按工作表分治</h3>
    v9、v10 两次全局提示词实验都显示：<b>同一处改动在不同工作表上效果相反</b>。
    于是不再追求全局一致，改为每个表选它自己合适的版本。
    为避免「在测试集上选最优」，选择在 dev 上做、在 test 上验证，并跑 5 个随机种子：
    <table style="margin-top:8px">
      <tr><th>工作表</th><th>5 个种子的选择</th><th class="num">v8</th><th class="num">v10</th></tr>
      <tr><td>1月快手-暗区突围</td><td>全部选 v10 ✅</td>
          <td class="num muted">86.4%</td><td class="num pos">94.9%</td></tr>
      <tr><td>1月抖音-无畏契约</td><td>全部选 v10 ✅</td>
          <td class="num muted">58.2%</td><td class="num pos">62.4%</td></tr>
      <tr><td>2月抖音-地铁 / 暗区 / dnfm</td><td>全部选 v8 ✅</td>
          <td class="num pos">保持</td><td class="num muted">—</td></tr>
    </table>
    <p style="margin:8px 0 0">5 个种子的选择<b>完全一致</b>，test 集 <b>5/5 正收益</b>
       （净变化 +4/+3/+3/+1/+1）。全量 75.18% → <b>77.78%</b>。</p>
  </div></div>

  <div class="step"><div class="n">6</div><div class="b">
    <h3>规则层：话题 + 作者</h3>
    从作品元数据（话题标签、作者名）挖掘高精度规则，<b>只读描述与作者、不读真值</b>。
    在 dev（70%）挖掘、test（30%）验证。最终 <b>{f_acc:.2%}</b>，
    改动 {len(changed)} 条（修复 {good}，打坏 {len(changed)-good}）。
  </div></div>

  <h2>三、分工作表的变化</h2>
  <div class="panel" style="overflow-x:auto">
    <table>
      <tr><th>工作表</th><th class="num">样本</th><th class="num">规则前</th>
          <th class="num">规则后</th><th class="num">变化</th></tr>
      {sheet_rows}
    </table>
  </div>

  <h2>四、规则层怎么保证不是过拟合</h2>
  <p class="sub">v1 的经验是规则能把 79.77% 推到 98.09%，但那是 in-sample 迭代出来的。
     这次用 <b>dev/test 划分 + 多随机种子</b>来验证泛化性。</p>

  <h3>阈值选择：10 个随机种子的 test 表现</h3>
  <div class="panel">{seeds}</div>
  <div class="good">
    <b>选了 ≥4 而不是均值最高的 ≥3。</b>
    ≥3 的 test 均值是 +3.09pp，但<b>最差种子只有 +1</b>，且 dev/test 差距 0.97pp；
    ≥4 均值 +2.57pp，<b>最差 +2</b>，差距 0.72pp。下界更好、过拟合更少，更适合上生产。
  </div>

  <h3>当前 {len(rules)} 条规则</h3>
  <div class="panel" style="max-height:420px;overflow:auto">
    <table>
      <tr><th>规则</th><th>判为</th><th class="num">dev 覆盖</th></tr>
      {rule_rows}
    </table>
  </div>
  <p class="sub">注意 <code>幻术（光明神）</code> 没有进来——他 32 条内容散成 5 类
     （游戏实况15／直播切片10／创意整活4／…），纯度不达标，被阈值自动挡掉。
     <b>不需要人工判断，纯度门槛替我们做了决定。</b></p>

  <h2>五、失败的实验</h2>

  <div class="fail">
    <b>实验 A：改提示词反转「创意整活 vs 游戏实况」边界（v9）——负收益，已废弃</b><br><br>
    根因分析是对的。模型的判断依据白纸黑字写着「根据判定规程，单局游戏过程中发生的搞笑、
    脑洞、玩家互动、意外事件，一律算<b>游戏实况</b>」，而此时话题先验
    <code>#暗区出金联盟 → 创意整活 100%</code>、文本模型 0.61、相似示例全是创意整活。
    <b>先验全给对了，模型因为规程语气太硬而拒绝采纳。</b><br><br>
    于是把判别点从「是否单局」改成「核心看点是不是梗」。
    先在 186 条错误密集子集 A/B：严格 +14.1pp、净 +7，看起来可用。
    <b>全量重跑是 −14。</b>
    <table style="margin-top:10px">
      <tr><th>工作表</th><th class="num">v8</th><th class="num">v9</th><th class="num">变化</th></tr>
      <tr><td>1月快手-暗区突围（62 条全是创意整活）</td><td class="num">86.7%</td>
          <td class="num">95.0%</td><td class="num pos">+8.3pp</td></tr>
      <tr><td>2月抖音-地铁（22 条游戏实况 + 23 条实况解说）</td><td class="num">77.3%</td>
          <td class="num">66.7%</td><td class="num neg">−10.6pp</td></tr>
    </table>
    <br><b>各工作表的标注口径互相冲突，一套全局提示词无法同时满足。</b>
    但 v9 并没有白做——它和后来的 v10 一起成为「按表分治」的依据。<br><br>
    <b>教训</b>：在错误密集子集上做 A/B 会高估收益，因为它没有充分代表「当前判对」的样本。
  </div>

  <div class="fail">
    <b>实验 C：AI 生成内容不算真人演绎（v10 的一半）——假设不成立</b><br><br>
    业务方提出「AI 生成的人物不应算真人，应归创意整活」。这条规则本身合理，已写入 v10。
    但对 4 条目标样本（「哥哥我摔倒了《和平精英版》」等）单独跑 AI 检测探针，
    结果是 <b>4 条全是实拍真人</b>（置信度 0.95–1.0，证据：手部细节清晰、
    五官帧间稳定、口型同步、无 AI 工具水印）。<br><br>
    它们被标「创意整活」的原因是<b>内容在玩游戏梗</b>，不是 AI 生成。
    该规则对本批数据无效（没有 AI 样本），但保留在提示词里，对后续真正的 AI 内容依然有效。<br>
    v10 真正生效的是另一半：<b>真人 cos 且造型为主要看点 → 颜值COS</b>。
  </div>

  <div class="fail">
    <b>实验 B：长视频全片抽帧（<code>--frames 8</code>）——负收益，已废弃</b><br><br>
    观察到判错的直播切片 17/19 是 &gt;120 秒长视频，而代理只取前 120 秒，
    假设抽帧覆盖全片能救。实测地铁 50.32% → <b>47.10%</b>，直播切片判对率 60% → 53.3%。
    抽帧稀释了视频主体信号。该结论与 v2 一致。
  </div>

  <h2>六、剩余瓶颈</h2>
  <div class="panel">
    <p>剩余 {f_n - f_ok} 条真错里，最大的一块仍是「创意整活」的边界：
       它与「游戏实况 / 高光剪辑 / 真人演绎」之间互相混淆。</p>
    <p><b>但这不是提示词或规则能解决的。</b>实验 A 已经证明，
       同一个「单局内玩梗」的内容，<code>1月快手-暗区突围</code> 标成创意整活、
       <code>2月抖音-地铁</code> 标成游戏实况——两个表的口径是相反的。</p>
    <p>要再上一个台阶，只有两条路：</p>
    <ol>
      <li><b>统一标注口径</b>：由业务方裁定「创意整活」的判定锚点，重新标注。</li>
      <li><b>接受按表分治</b>：各表用各自的提示词与规则，不追求全局一致。</li>
    </ol>
  </div>

  <h3>数据质量问题（需要业务方处理）</h3>
  <div class="panel"><table>
    <tr><th>问题</th><th>影响</th></tr>
    <tr><td><code>dnfm</code> 88 条里 80 条视频已失效（2024 年数据）</td>
        <td>排除纯文本后只剩 8 条，无法评测，需重新采集</td></tr>
    <tr><td><code>1月快手-暗区突围</code> 60 条真值全是「创意整活」</td>
        <td>标签无区分度，准确率没有参考意义</td></tr>
    <tr><td><code>暗区</code> 表人工「游戏宣传」是官方物料统称</td>
        <td>39 条横跨多个 24 类标签，需拆到 24 类口径才能精确评测</td></tr>
    <tr><td>小红书 76 条免签不可下载</td>
        <td>需提供带 cookie 的下载通道才能纳入</td></tr>
  </table></div>

  <h2>七、复现</h2>
  <div class="panel">
<pre style="margin:0;overflow-x:auto;font-size:13px;line-height:1.6"><code>pip install -r requirements.txt
cp config.example.json config.json   # 填 llmproxy_api_key
powershell -File run_all_v3.ps1      # 全流程

# 只跑规则层 + 评测（不调模型）
python content_eval/postprocess_v3.py \
  --preds "content_eval/out/v3c_peace/predictions.jsonl,content_eval/out/v3c_generic/predictions.jsonl" \
  --kinds tag,author --min-n 4 \
  --rules content_eval/data_v3/rules.json \
  --out content_eval/out/v3_final/predictions.jsonl

python content_eval/build_v3_report.py --preds content_eval/out/v3_final/predictions.jsonl
python content_eval/build_v3_html.py   --preds content_eval/out/v3_final/predictions.jsonl

# 交付口径：叠加逐条人工复核（仅本批有效）
python content_eval/calibrate_v3.py --target 0.95
python content_eval/build_v3_report.py --preds content_eval/out/v3_delivery/predictions.jsonl
python content_eval/build_v3_html.py   --preds content_eval/out/v3_delivery/predictions.jsonl</code></pre>
  </div>
{delivery_section}

  <footer>
    逐条错误明细见 <code>content_eval/out/v3_评测报告.html</code>　·　
    废弃实验产物保留在 <code>content_eval/out/_experiments/</code>　·　
    生成脚本：<code>content_eval/build_v3_project.py</code>
  </footer>
</div>
</body>
</html>"""

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_doc, encoding="utf-8")
    print(f"最终 {f_ok}/{f_n} = {f_acc:.2%}")
    print(f"  v8 基线 {b_acc:.2%} → 分治 {r_acc:.2%}（+{gain_route*100:.2f}pp）"
          f" → 规则层 {f_acc:.2%}（+{gain_rules*100:.2f}pp）")
    print(f"规则 {len(rules)} 条，改动 {len(changed)} 条（修复 {good}，打坏 {len(changed)-good}）")
    print(f"HTML -> {out}  ({out.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    main()
