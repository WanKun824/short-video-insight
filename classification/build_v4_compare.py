# -*- coding: utf-8 -*-
"""v3（抖音/快手）与 v4（B站）的跨平台对比报告。

回答一个问题：同一套方法换到 B站 能不能直接复用？
"""
from __future__ import annotations

import argparse
import html
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent

V3_PAIRS = {("游戏宣传", "游戏资讯"), ("游戏实况", "实况解说"),
            ("直播切片", "实况解说"), ("二创类", "创意整活")}
BILI_PAIRS = {("创意剪辑", "创意整活"), ("实况解说", "创意整活")}
# 业务方逐条复核 49 条样本后认定的 3 组
REVIEWED_PAIRS = {("创意整活", "游戏实况"), ("创意整活", "高光剪辑"),
                  ("游戏实况", "高光剪辑")}
# 业务方指示批量采纳的 22 组（未逐条核对样本）
BULK_PAIRS = {
    ("实况解说","高光剪辑"),("创意整活","游戏攻略"),("创意剪辑","直播切片"),
    ("实况解说","游戏攻略"),("创意整活","直播切片"),("游戏攻略","游戏资讯"),
    ("游戏吐槽","直播切片"),("游戏测评","游戏资讯"),("游戏资讯","直播切片"),
    ("创意剪辑","实况解说"),("直播切片","高光剪辑"),("创意整活","真人演绎"),
    ("二创类","才艺展示"),("创意剪辑","游戏实况"),("电竞相关","直播切片"),
    ("创意整活","游戏资讯"),("游戏实况","游戏攻略"),("实况解说","游戏资讯"),
    ("游戏吐槽","游戏测评"),("创意整活","皮肤展示"),("游戏攻略","直播切片"),
    ("创意剪辑","颜值COS")}
EVIDENCED = V3_PAIRS | BILI_PAIRS | REVIEWED_PAIRS   # 有逐条依据的
ALL_PAIRS = EVIDENCED | BULK_PAIRS
TAG_RE = re.compile(r"#([^#\s]+)")


def esc(s):
    return html.escape(str(s or ""))


def read_jsonl(p: Path):
    if not p.is_file():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def load(spec):
    d = {}
    for p in spec.split(","):
        for x in read_jsonl(Path(p.strip())):
            if x.get("id") and x.get("label"):
                d[x["id"]] = x
    return d


def svg_bars(items, width=760, mx=1.0):
    pad_l, pad_r, row_h, bar_h = 180, 110, 42, 20
    plot_w = width - pad_l - pad_r
    height = len(items) * row_h + 40
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%">']
    for g in (0, .25, .5, .75, 1.0):
        x = pad_l + plot_w * g / mx
        out.append(f'<line x1="{x:.0f}" y1="24" x2="{x:.0f}" y2="{height-16}" class="grid"/>')
        out.append(f'<text x="{x:.0f}" y="16" class="axis" text-anchor="middle">{g:.0%}</text>')
    for i, (name, v, note, cls) in enumerate(items):
        y = 30 + i * row_h
        out.append(f'<text x="{pad_l-10}" y="{y+15}" class="lbl" text-anchor="end">{esc(name)}</text>')
        w = max(2, plot_w * v / mx)
        out.append(f'<rect x="{pad_l}" y="{y}" width="{w:.0f}" height="{bar_h}" rx="3" class="{cls}"/>')
        out.append(f'<text x="{pad_l+w+8:.0f}" y="{y+15}" class="val">{v:.1%}'
                   + (f' <tspan class="axis">{esc(note)}</tspan>' if note else '') + '</text>')
    out.append("</svg>")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "out" / "v4_平台对比报告.html"))
    # 默认路径同时兼容两种布局：
    #   仓库布局   out/v4_final_predictions.jsonl（精简版）
    #   工作目录   out/v4_bili/predictions.jsonl（完整版，含 context）
    ap.add_argument("--v3-preds", default="",
                    help="默认依次尝试 out/v3_final_predictions.jsonl 与 out/v3_final/predictions.jsonl")
    ap.add_argument("--v4-preds", default="",
                    help="默认依次尝试 out/v4_final_predictions.jsonl 与 out/v4_bili/predictions.jsonl")
    ap.add_argument("--v4-base-preds", default="",
                    help="v4 基线预测（v11 相对基线的提升用）；缺省时该节显示为空")
    args = ap.parse_args()

    O = ROOT / "out"

    def first_existing(*cands):
        """返回第一个存在的预测文件；都不存在返回空串（load 会得到空 dict）。"""
        for c in cands:
            if c and Path(c).is_file():
                return str(c)
        return ""

    v3_spec = args.v3_preds or first_existing(
        O / "v3_final_predictions.jsonl", O / "v3_final" / "predictions.jsonl")
    v4_spec = args.v4_preds or first_existing(
        O / "v4_final_predictions.jsonl", O / "v4_bili" / "predictions.jsonl")
    # 基线支持两种命名：
    #   仓库布局   out/v4_base_{peace,king,generic}_predictions.jsonl（精简版）
    #   工作目录   out/v4_{peace,king,generic}/predictions.jsonl（完整版）
    if args.v4_base_preds:
        base_spec = args.v4_base_preds
    else:
        cands = []
        for t in ("peace", "king", "generic"):
            p = first_existing(O / f"v4_base_{t}_predictions.jsonl",
                               O / f"v4_{t}" / "predictions.jsonl")
            if p:
                cands.append(p)
        base_spec = ",".join(cands)

    v3_rows = {r["id"]: r for r in read_jsonl(ROOT / "data_v3" / "ground_truth.jsonl")}
    v4_rows = {r["id"]: r for r in read_jsonl(ROOT / "data_v4" / "ground_truth.jsonl")}
    v3P = load(v3_spec) if v3_spec else {}
    v4P = load(v4_spec) if v4_spec else {}
    v4base = load(base_spec) if base_spec else {}
    missing = [n for n, s in (("v3", v3_spec), ("v4", v4_spec), ("v4 基线", base_spec)) if not s]
    if missing:
        print(f"[警告] 缺少：{'、'.join(missing)} —— 对应章节会显示 0%")
        print("        仓库内精简预测的文件名是 out/v3_final_predictions.jsonl 等，")
        print("        若在工作目录跑，请用 --v3-preds / --v4-preds / --v4-base-preds 指定。")

    def acc(rows, P, pairs):
        pp = pairs | {(b, a) for a, b in pairs}
        cov = [r for r in rows.values() if r["label"] and r["id"] in P
               and P[r["id"]].get("media")
               and r.get("platform") != "小红书"]
        if not cov:          # 该路预测不存在（例如只解包了部分 predictions）
            return 0.0, 0.0, 0
        n = sum(1 for r in cov
                if P[r["id"]]["label"] == r["label"] or r.get("label_broad")
                or (r["label"], P[r["id"]]["label"]) in pp)
        s = sum(1 for r in cov if P[r["id"]]["label"] == r["label"])
        return n / len(cov), s / len(cov), len(cov)

    # 抖音/快手 必须用和 B站 相同的口径才能比。
    # 之前这里用 V3_PAIRS（4 组）对比 B站 的 ALL_PAIRS（31 组），
    # 得出「78.03% vs 94.61%」，是口径不同造成的假差距。
    v3_acc, v3_s, v3_n = acc(v3_rows, v3P, ALL_PAIRS)
    v3_4pairs, _, _ = acc(v3_rows, v3P, V3_PAIRS)
    v4_acc, v4_s, v4_n = acc(v4_rows, v4P, ALL_PAIRS)
    v4b_acc, _, _ = acc(v4_rows, v4base, ALL_PAIRS)
    v4_v3only, _, _ = acc(v4_rows, v4P, V3_PAIRS)
    # 只计有逐条依据的豁免（不含批量采纳的 22 组）
    v4_evid, _, _ = acc(v4_rows, v4P, EVIDENCED)

    # 数据形态对比
    def shape(rows):
        rs = [r for r in rows.values() if r["label"]]
        dl = sorted(len(r["desc"]) for r in rs)
        nt = [len(TAG_RE.findall(r["desc"])) for r in rs]
        return dl[len(dl)//2], sum(1 for x in nt if x == 0) / len(nt), len(set(
            r.get("author", "") for r in rs))
    v3_dl, v3_notag, v3_au = shape(v3_rows)
    v4_dl, v4_notag, v4_au = shape(v4_rows)

    chart = svg_bars([
        ("抖音/快手", v3_acc, f"{v3_n} 条", "bar-v3"),
        ("B站", v4_acc, f"{v4_n} 条", "bar-v4"),
    ])
    # 基线预测缺失时（仓库里没带 v4_peace/king/generic）不画 0% 的柱子，
    # 直接只展示 bili_v11 的绝对值，避免误读成「基线是 0」。
    if v4base:
        chart2 = svg_bars([
            ("基线（v3 的提示词直接用）", v4b_acc, "", "bar-base"),
            ("bili_v11（B站 专用）", v4_acc,
             f"{(v4_acc - v4b_acc) * 100:+.1f}pp", "bar-v4"),
        ])
        compare_note = (f"同 {v4_n} 条样本公平对比："
                        f"基线 {v4b_acc:.2%} → bili_v11 {v4_acc:.2%}")
    else:
        chart2 = svg_bars([("bili_v11（B站 专用）", v4_acc, f"{v4_n} 条", "bar-v4")])
        compare_note = ("基线预测未随仓库提供（体积原因），这里只展示 bili_v11 的绝对值。"
                        "需要对比时请用 --v4-base-preds 指定基线预测。")

    # 分表
    sheet_rows = ""
    for sh in dict.fromkeys(r["sheet"] for r in v4_rows.values()):
        sub = [r for r in v4_rows.values() if r["sheet"] == sh and r["label"]
               and r["id"] in v4P and v4P[r["id"]].get("media")]
        if not sub:
            continue
        pp = ALL_PAIRS
        pp = pp | {(b, a) for a, b in pp}
        m = sum(1 for r in sub if v4P[r["id"]]["label"] == r["label"]
                or (r["label"], v4P[r["id"]]["label"]) in pp)
        mb = sum(1 for r in sub if r["id"] in v4base
                 and (v4base[r["id"]]["label"] == r["label"]
                      or (r["label"], v4base[r["id"]]["label"]) in pp))
        nb = sum(1 for r in sub if r["id"] in v4base)
        cur = m / len(sub)
        base_cell = ""
        chg_cell = ""
        if nb:
            base_cell = f'<td class="num muted">{mb/nb:.1%}</td>'
            d = cur - mb / nb
            chg_cell = (f'<td class="num {"pos" if d > 0 else "muted"}">'
                        f'{d*100:+.1f}pp</td>')
        sheet_rows += (f'<tr><td>{esc(sh)}</td><td>{esc(sub[0]["game_title"])}</td>'
                       f'<td class="num">{len(sub)}</td>'
                       f'{base_cell}'
                       f'<td class="num"><b>{cur:.1%}</b></td>'
                       f'{chg_cell}</tr>')

    sheet_head = ('<tr><th>工作表</th><th>游戏</th><th class="num">样本</th>'
                  '<th class="num">基线</th><th class="num">bili_v11</th>'
                  '<th class="num">变化</th></tr>') if v4base else \
                 ('<tr><th>工作表</th><th>游戏</th><th class="num">样本</th>'
                  '<th class="num">bili_v11</th></tr>')

    # 混淆对
    pp_all = ALL_PAIRS
    pp_all = pp_all | {(b, a) for a, b in pp_all}
    cov4 = [r for r in v4_rows.values() if r["label"] and r["id"] in v4P
            and v4P[r["id"]].get("media")]
    pairs = Counter((r["label"], v4P[r["id"]]["label"]) for r in cov4
                    if v4P[r["id"]]["label"] != r["label"])
    # ---- 剩余错误：按成因分组，带视频链接和模型依据 ----
    v4meta = {}
    _mp = ROOT / "data_v4" / "media_meta.json"
    if _mp.is_file():
        v4meta = json.loads(_mp.read_text(encoding="utf-8"))

    errs4 = [r for r in v4_rows.values()
             if r["label"] and r["id"] in v4P and v4P[r["id"]].get("media")
             and v4P[r["id"]]["label"] != r["label"]
             and (r["label"], v4P[r["id"]]["label"]) not in pp_all]

    CAUSES = [
        ("真人出镜盖掉了更贴切的类",
         "判定规程里真人出镜优先级最高，一出现真人/手部/摄像头小窗，"
         "「真人演绎」「直播切片」就会盖掉更贴切的类",
         "<b>可以修</b>：要求「真人是主要看点」才判，而不是「出现真人」就判"),
        ("内容确实横跨两类",
         "同一条视频既是 A 也是 B，Excel 按主观侧重打标",
         "<b>需要定口径</b>：业务方决定「吐槽 vs 攻略」「测评 vs 整活」谁优先"),
        ("长视频只看了前段",
         "代理只覆盖前 120 秒，开头形态与整体不一致",
         "<b>技术限制</b>：全片抽帧已验证是负收益（−3.2pp）"),
        ("模型判法更符合 24 类定义",
         "模型依据对照定义是成立的，Excel 标签更像沿用了系列惯例",
         "<b>建议改 Excel</b>"),
    ]
    PERSON_SET = {"真人演绎", "直播切片", "颜值COS", "才艺展示"}
    INFO_SET = {"游戏吐槽", "游戏测评", "游戏攻略", "游戏资讯", "玩家故事"}

    def cause_of(r):
        p = v4P[r["id"]]["label"]
        dur = int((v4meta.get(r["id"]) or {}).get("duration") or 0)
        if p in PERSON_SET and r["label"] not in PERSON_SET:
            return 0
        if dur > 240:
            return 2
        if p in INFO_SET or r["label"] in INFO_SET:
            return 1
        return 3

    err_items = errs4
    buckets4 = {}
    for r in errs4:
        buckets4.setdefault(cause_of(r), []).append(r)

    cause_rows = "".join(
        f'<tr><td>{esc(nm)}</td><td class="num">{len(buckets4.get(i, []))}</td><td>{fx}</td></tr>'
        for i, (nm, _, fx) in enumerate(CAUSES) if buckets4.get(i))

    err_sections = ""
    for i, (nm, desc, fx) in enumerate(CAUSES):
        items = buckets4.get(i, [])
        if not items:
            continue
        cards = ""
        for r in items:
            obj = v4P[r["id"]]
            dur = int((v4meta.get(r["id"]) or {}).get("duration") or 0)
            cards += f"""
      <div class="ecard">
        <div class="ehd">
          <span class="chip gt">{esc(r['label'])}</span>
          <span class="arrow">→</span>
          <span class="chip pred">{esc(obj['label'])}</span>
          <a class="vid" href="{esc(r.get('link',''))}" target="_blank" rel="noopener">▸ 视频</a>
          <span class="emeta">{esc(r.get('game_title',''))} · {dur//60}:{dur%60:02d}"""
            if r.get("focus"):
                cards += f" · 侧重点：{esc(r['focus'])}"
            cards += f"""</span>
        </div>
        <div class="etitle">{esc((r.get('desc') or '').replace(chr(10), ' ')[:95])}</div>
        <div class="ersn">模型依据：{esc((obj.get('reason') or '')[:260])}</div>
      </div>"""
        err_sections += f"""
  <h3>{esc(nm)}（{len(items)} 条）</h3>
  <p class="sub">{desc}　—　{fx}</p>
  <div class="ecards">{cards}</div>
"""

    doc = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>v4 B站 评测 · 跨平台对比</title>
<style>
  :root {{ --bg:#f7f8fa; --panel:#fff; --ink:#111827; --muted:#6b7280; --line:#e5e7eb;
    --gt:#b91c1c; --pred:#1d4ed8; --accent:#0f766e; --ok:#047857;
    --warn:#b45309; --warnbg:#fef3c7; --v4:#7c3aed; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#0b0f14; --panel:#131a22; --ink:#e5e7eb; --muted:#9ca3af; --line:#263241;
      --warnbg:#3b2f0b; --warn:#fcd34d; --ok:#6ee7b7; --v4:#a78bfa; }} }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink); line-height:1.7;
    font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif; }}
  .wrap {{ max-width:1020px; margin:0 auto; padding:40px 20px 70px; }}
  h1 {{ font-size:28px; margin:0 0 6px; }}
  h2 {{ font-size:20px; margin:42px 0 12px; border-bottom:1px solid var(--line); padding-bottom:8px; }}
  h3 {{ font-size:15px; margin:22px 0 8px; }}
  .sub {{ color:var(--muted); margin:0 0 24px; }}
  .cards {{ display:grid; grid-template-columns:repeat(4,1fr); gap:14px; margin:22px 0; }}
  .stat {{ background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:18px; }}
  .stat .num {{ font-size:28px; font-weight:700; color:var(--accent); line-height:1.2; }}
  .stat .lbl {{ font-size:13px; color:var(--muted); }}
  .stat .note {{ font-size:12px; color:var(--muted); margin-top:6px; }}
  .panel {{ background:var(--panel); border:1px solid var(--line); border-radius:14px;
    padding:20px; margin:14px 0; }}
  table {{ width:100%; border-collapse:collapse; font-size:14px; }}
  th,td {{ text-align:left; padding:9px 10px; border-bottom:1px solid var(--line); vertical-align:top; }}
  th {{ color:var(--muted); font-weight:600; font-size:12px; }}
  td.num,th.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  .muted {{ color:var(--muted); }} .pos {{ color:var(--ok); font-weight:600; }}
  .neg {{ color:var(--gt); font-weight:600; }}
  code {{ background:var(--line); padding:1px 5px; border-radius:4px; font-size:13px; }}
  .chip {{ display:inline-block; font-size:12px; padding:2px 8px; border-radius:999px; }}
  .chip.ok {{ background:rgba(4,120,87,.14); color:var(--ok); }}
  .alert {{ background:var(--warnbg); color:var(--warn); border:1px solid var(--warn);
    border-radius:12px; padding:14px 18px; margin:14px 0; font-size:14px; }}
  .fail {{ background:rgba(185,28,28,.08); border:1px solid var(--gt); border-radius:12px;
    padding:14px 18px; margin:14px 0; font-size:14px; }}
  .fail b {{ color:var(--gt); }}
  svg text.lbl {{ font-size:12.5px; fill:var(--ink); }}
  svg text.axis {{ font-size:11px; fill:var(--muted); }}
  svg text.val {{ font-size:12px; fill:var(--ink); font-variant-numeric:tabular-nums; }}
  svg line.grid {{ stroke:var(--line); }}
  svg .bar-v3 {{ fill:var(--accent); }} svg .bar-v4 {{ fill:var(--v4); }}
  svg .bar-base {{ fill:var(--muted); opacity:.5; }}
  footer {{ color:var(--muted); font-size:12px; margin-top:46px; }}

  /* ---- 错误案例卡片 ---- */
  .ecards {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(310px,1fr)); gap:10px; }}
  .ecard {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:12px; }}
  .ehd {{ display:flex; align-items:center; gap:6px; flex-wrap:wrap; margin-bottom:7px; }}
  .ehd .arrow {{ color:var(--muted); font-size:13px; }}
  .emeta {{ font-size:11px; color:var(--muted); width:100%; margin-top:2px; }}
  .etitle {{ font-size:13px; margin:5px 0 7px; }}
  .ersn {{ font-size:11.5px; color:var(--muted);
    border-top:1px dashed var(--line); padding-top:7px; }}
  .chip.gt {{ background:var(--gtbg,rgba(185,28,28,.12)); color:var(--gt); font-weight:600; }}
  .chip.pred {{ background:var(--predbg,rgba(29,78,216,.12)); color:var(--pred); font-weight:600; }}
  a.vid {{ color:var(--accent); text-decoration:none; font-size:11.5px; white-space:nowrap;
    border:1px solid var(--accent); padding:2px 8px; border-radius:6px; font-weight:600;
    margin-left:auto; }}
  a.vid:hover {{ background:var(--accent); color:#fff; }}
  @media (max-width:760px) {{ .cards {{ grid-template-columns:repeat(2,1fr); }} }}
</style></head><body><div class="wrap">
  <h1>v4 B站 评测 · 跨平台对比</h1>
  <p class="sub">10月TOP500内容分类.xlsx · B站 4 个工作表 1880 条中分层抽样 500 条 ·
     模型 gemini-2.5-flash · 24 类标签 · {date.today()}</p>

  <div class="cards">
    <div class="stat"><div class="num">{v4_acc:.1%}</div><div class="lbl">B站 准确率</div>
      <div class="note">{v4_n} 条，业务口径</div></div>
    <div class="stat"><div class="num">{v3_acc:.1%}</div><div class="lbl">抖音/快手 准确率</div>
      <div class="note">{v3_n} 条，<b>同口径</b></div></div>
    <div class="stat"><div class="num">{v4_n - int(v4_acc*v4_n+0.5)}</div><div class="lbl">剩余错误</div>
      <div class="note">已按成因分 4 类，见评测报告第五节</div></div>
    <div class="stat"><div class="num">468</div><div class="lbl">下载成功</div>
      <div class="note">500 条中，失败 32 条</div></div>
  </div>

  <h2>一、B站 vs 抖音/快手（同口径）</h2>
  <p class="sub">两边都用 31 组豁免对，可直接比较。</p>
  <div class="panel">{chart}</div>
  <div class="panel" style="overflow-x:auto">
    <table>
      <tr><th>口径</th><th class="num">抖音/快手</th><th class="num">B站</th><th>说明</th></tr>
      <tr><td>4 组豁免对</td><td class="num muted">{v3_4pairs:.2%}</td>
          <td class="num muted">{v4_v3only:.2%}</td>
          <td>v3 最初的口径</td></tr>
      <tr><td>31 组豁免对</td><td class="num"><b>{v3_acc:.2%}</b></td>
          <td class="num"><b>{v4_acc:.2%}</b></td>
          <td><b>当前口径，两边可比</b></td></tr>
      <tr><td>+ 逐条人工复核</td><td class="num"><b>95.07%</b></td>
          <td class="num"><b>95.04%</b></td>
          <td>仅本批有效，对新数据零泛化</td></tr>
    </table>
  </div>
  <div class="alert">
    <b>31 组豁免对里有 22 组是批量采纳的（未逐条核对样本）。</b>
    若只计有逐条依据的豁免，B站 是 <b>{v4_evid:.2%}</b>。<br>
    批量采纳的 22 组里有 4 组语义距离较远
    （<code>二创类／才艺展示</code>、<code>创意剪辑／颜值COS</code>、
    <code>游戏吐槽／直播切片</code>、<code>创意整活／皮肤展示</code>），
    被追问口径时建议用这个数。
  </div>

  <h3>B站 的数据形态差异</h3>
  <p class="sub">同口径下 B站 ({v4_acc:.1%}) 高于抖音/快手 ({v3_acc:.1%})，
     但这不代表 B站 更简单——下面几项让 B站 的<b>纯文本线索几乎不可用</b>，
     同时也意味着 v3 的话题规则层无法复用。</p>
  <div class="panel"><table>
    <tr><th>维度</th><th class="num">抖音/快手</th><th class="num">B站</th><th>影响</th></tr>
    <tr><td>描述长度（中位）</td><td class="num">{v3_dl} 字</td><td class="num">{v4_dl} 字</td>
        <td>B站 只有一句标题，文本信息量少</td></tr>
    <tr><td>无话题标签占比</td><td class="num">{v3_notag:.0%}</td>
        <td class="num"><b>{v4_notag:.0%}</b></td>
        <td><b>话题规则层在 B站 完全失效</b>（挖出 0 条规则）</td></tr>
    <tr><td>作者数量</td><td class="num">—</td><td class="num">{v4_au} 个</td>
        <td>500 条分散在 {v4_au} 个 UP 主，最多的只有 8 条，作者规则也难成立</td></tr>
    <tr><td>文本先验 top1</td><td class="num">59.9%</td><td class="num"><b>40.8%</b></td>
        <td>纯文本几乎预测不了，必须靠画面</td></tr>
  </table></div>

  <h2>二、B站 专用提示词（bili_v11）</h2>
  <div class="panel">{chart2}</div>
  <p class="sub">{compare_note}</p>
  <div class="panel" style="overflow-x:auto"><table>
    {sheet_head}
    {sheet_rows}
  </table></div>
  <h3>v11 相对 generic_v8 加了什么</h3>
  <div class="panel"><table>
    <tr><th>改动</th><th>依据</th></tr>
    <tr><td>B站 场景说明：描述短、无标签，判断以画面为主</td>
        <td>实测 97% 无话题标签，描述中位仅 21 字</td></tr>
    <tr><td>提醒视频较长，开头常是片头/前情提要</td>
        <td>中位 163 秒，65% 超过 120 秒（代理只覆盖前 120 秒）</td></tr>
    <tr><td>细化「创意剪辑 vs 创意整活」「高光剪辑 vs 创意剪辑」边界</td>
        <td>创意剪辑在 B站 是常见类（106 条），但基线召回 1/25</td></tr>
  </table></div>

  <h2>三、剩余错误案例（{len(err_items)} 条）</h2>
  <p class="sub">25 条错误分布在 25 个不同的混淆方向上，再加豁免对每组只能救 1 条。
     按<b>成因</b>分组后，其中 12 条是可行动的。</p>
  <div class="panel" style="overflow-x:auto"><table>
    <tr><th>成因</th><th class="num">条数</th><th>能不能修</th></tr>
    {cause_rows}
  </table></div>
{err_sections}

  <h2>四、哪些方法没能复用</h2>
  <div class="fail">
    <b>话题规则层：在 B站 挖出 0 条规则</b><br>
    v3 靠话题+作者规则拿到 +1.35pp，但 B站 97% 的视频没有话题标签，
    规则挖掘直接返回空。
  </div>
  <div class="fail">
    <b>作者规则：10 个种子只有 6 个正收益，平均 +0.43pp，不采纳</b><br>
    500 条分散在 {v4_au} 个 UP 主，最多的只有 8 条，达不到 v3 的样本量门槛。
    降到 n≥3 虽然能挖出 11 条规则，但 test 上不稳定。
  </div>
  <div class="fail">
    <b>按表分治：只有三角洲表在 5 个种子上一致，test 仅 2/5 正收益</b><br>
    v3 的分治能拿 +2.47pp（5/5 种子一致），B站 上不成立——
    直接全量用 bili_v11（{v4_acc:.2%}）比分治（73.92%）更好。
  </div>

  <h2>五、过程中修的两个 bug</h2>
  <div class="panel"><table>
    <tr><th>问题</th><th>表现</th><th>修复</th></tr>
    <tr><td><code>classify.py</code> 的标签集路由用 <code>endswith("_v8")</code></td>
        <td><code>bili_v11</code> 不匹配任何分支，静默退回 16 类 <code>LABELS</code>，
            导致「游戏测评/皮肤展示/游戏吐槽」被判非法标签，<b>20 条整条失败</b></td>
        <td>改为显式枚举 <code>_v8/_v9/_v10/_v11</code>，并打印实际标签集</td></tr>
    <tr><td><code>max_tokens=700</code> 截断响应</td>
        <td>v11 的「判断依据」更长，约 5% 的响应 JSON 不完整</td>
        <td>提到 1200，并加正则兜底解析截断的 JSON</td></tr>
  </table></div>

  <h2>六、结论</h2>
  <div class="panel">
    <p><b>方法可以复用，但要按平台重新验证。</b>v3 的核心资产里：</p>
    <ul>
      <li>✅ <b>业务口径框架</b>（哪些标签对视为同类）——可复用，但对数需要重新确定</li>
      <li>✅ <b>提示词骨架</b>（判定规程 + 24 类定义）——可复用，加平台说明后 +1.5pp</li>
      <li>✅ <b>dev/test + 多种子验证流程</b>——可复用，而且正是它挡住了不稳的分治与作者规则</li>
      <li>❌ <b>话题规则层</b>——B站 没有话题标签，完全失效</li>
      <li>❌ <b>按表分治</b>——B站 上种子间不一致，不成立</li>
    </ul>
    <p>B站 当前 <b>{v4_acc:.2%}</b>，剩余 {v4_n - int(v4_acc*v4_n+0.5)} 条错误
       分布在同样多的混淆方向上——再加豁免对每组只能救 1 条，已无意义。</p>
    <p style="margin-bottom:0">按成因拆开后有三条可行路径（详见评测报告第五节）：
       <b>8 条</b>是真人出镜优先级过高盖掉了更贴切的类，可以改提示词；
       <b>10 条</b>是内容横跨两类，需要业务方定「吐槽 vs 攻略」这类的优先顺序；
       <b>4 条</b>模型判法对照 24 类定义更成立，建议改 Excel。</p>
  </div>

  <footer>
    逐条错误明细见 <code>content_eval/out/v4_评测报告.md</code>　·　
    生成脚本：<code>content_eval/build_v4_compare.py</code>
  </footer>
</div></body></html>"""

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")
    print(f"同口径（31 组豁免对）对比：")
    print(f"  抖音/快手 {v3_acc:.2%}（{v3_n} 条）")
    print(f"  B站       {v4_acc:.2%}（{v4_n} 条）")
    print(f"  仅有逐条依据的豁免：{v4_evid:.2%}")
    print(f"  若只用 v3 的 4 组口径：{v4_v3only:.2%}")
    if v4base:
        print(f"  基线（v3 提示词直接用）：{v4b_acc:.2%}")
    else:
        print("  基线预测未提供，跳过对比（--v4-base-preds 可指定）")

    print(f"HTML -> {out}  ({out.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    main()
