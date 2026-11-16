# -*- coding: utf-8 -*-
"""导出「创意整活 / 游戏实况 / 高光剪辑」三类的对比样本，供业务方裁定口径。

为什么是这三类
--------------
v3（抖音/快手）与 v4（B站）两个数据集、三次提示词实验都指向同一处：
这三类之间的边界在标注里不稳定，提示词和规则都修不动。

页面结构
--------
1. 混淆样本：Excel 与模型判法不同的，按混淆方向分组，两边理由并列
2. 对照样本：Excel 与模型一致的，用来看「什么情况下没有歧义」
3. 每条都带 B站 链接、Excel 的「创意侧重点」、模型的判断依据、时长

判断时建议关注「创意侧重点」列——它是标注员自己写的，往往能看出当时的判断角度。
"""
from __future__ import annotations

import argparse
import html
import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FOCUS3 = ("创意整活", "游戏实况", "高光剪辑")


def esc(s):
    return html.escape(str(s or ""))


def read_jsonl(p: Path):
    if not p.is_file():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "out" / "三类边界对比.html"))
    args = ap.parse_args()

    # 两个数据集一起看：v4（B站）+ v3（抖音/快手），同一处边界在两个平台都存在
    rows, meta, P = {}, {}, {}
    for gt, preds, mp in (
        (ROOT / "data_v4" / "ground_truth.jsonl",
         [ROOT / "out" / "v4_final_predictions.jsonl",
          ROOT / "out" / "v4_bili" / "predictions.jsonl"],
         ROOT / "data_v4" / "media_meta.json"),
        (ROOT / "data_v3" / "ground_truth.jsonl",
         [ROOT / "out" / "v3_final_predictions.jsonl",
          ROOT / "out" / "v3_final" / "predictions.jsonl"],
         ROOT / "data_v3" / "media_meta.json"),
    ):
        for r in read_jsonl(gt):
            if r["id"] not in rows:
                rows[r["id"]] = r
        if mp.is_file():
            for k, v in json.loads(mp.read_text(encoding="utf-8")).items():
                meta.setdefault(k, v)
        for p in preds:
            for x in read_jsonl(p):
                if x.get("id") and x.get("label") and x["id"] not in P:
                    P[x["id"]] = x

    cov = [i for i in P if rows.get(i) and rows[i]["label"] and P[i].get("media")
           and rows[i].get("platform") != "小红书"]

    def item(i, kind):
        r, p = rows[i], P[i]
        dur = int((meta.get(i) or {}).get("duration") or 0)
        return {
            "id": i, "link": r.get("link", ""), "sheet": r["sheet"],
            "platform": r.get("platform", ""),
            "game": r.get("game_title", ""), "author": r.get("author", ""),
            "gt": r["label"], "pred": p["label"],
            "focus": r.get("focus", ""),
            "desc": (r.get("desc") or "").replace("\n", " ")[:160],
            "reason": (p.get("reason") or "")[:400],
            "conf": p.get("confidence", ""),
            "dur": f"{dur//60}:{dur%60:02d}",
            "views": r.get("views", ""), "kind": kind,
        }

    # 混淆样本：真值与预测都落在三类内
    mixed = [item(i, "mixed") for i in cov
             if {rows[i]["label"], P[i]["label"]} <= set(FOCUS3)
             and rows[i]["label"] != P[i]["label"]]
    # 对照样本：三类中判对的
    agreed = [item(i, "agreed") for i in cov
              if rows[i]["label"] in FOCUS3 and P[i]["label"] == rows[i]["label"]]

    by_pair = defaultdict(list)
    for m in mixed:
        by_pair[(m["gt"], m["pred"])].append(m)
    by_label = defaultdict(list)
    for a in agreed:
        by_label[a["gt"]].append(a)

    def feedback_box(d):
        """每条混淆样本下的反馈框。

        纯前端 + localStorage：这是个离线 HTML，没有后端，
        所以用 localStorage 存，刷新/关页面不丢，填完点「导出」给回一个 JSON。
        导出的 JSON 可以直接被 apply_boundary_feedback.py 读回流水线。
        """
        vid = esc(d["id"])
        return f'''
  <div class="fb" data-id="{vid}" data-gt="{esc(d["gt"])}" data-pred="{esc(d["pred"])}">
    <div class="fb-q">你的判断：</div>
    <label><input type="radio" name="v_{vid}" value="excel"> Excel 对（<b>{esc(d["gt"])}</b>）</label>
    <label><input type="radio" name="v_{vid}" value="model"> 模型对（<b>{esc(d["pred"])}</b>）</label>
    <label><input type="radio" name="v_{vid}" value="both"> 两者都通（口径分歧）</label>
    <label><input type="radio" name="v_{vid}" value="neither"> 都不对</label>
    <input class="fb-other" type="text" placeholder="若都不对，应该是哪一类？">
    <textarea class="fb-why" rows="2" placeholder="你是看什么判断的？（画面特征 / 剪辑方式 / 创作意图…）"></textarea>
  </div>'''

    def card(d, show_pred=True):
        rows_ = [
            f'<div class="card">',
            f'  <div class="hd">',
            f'    <a class="vid" href="{esc(d["link"])}" target="_blank" rel="noopener">▸ 打开视频</a>',
            f'    <span class="meta">{esc(d["platform"])} · {esc(d["game"])}'
            f' · {esc(d["author"])} · {d["dur"]}'
            + (f' · {int(d["views"]):,} 播放' if str(d["views"]).isdigit() else '') + '</span>',
            f'  </div>',
            f'  <div class="title">{esc(d["desc"])}</div>',
            f'  <div class="labels">',
            f'    <span class="chip gt">Excel：{esc(d["gt"])}</span>',
        ]
        if show_pred:
            rows_.append(f'    <span class="chip pred">模型：{esc(d["pred"])}</span>')
        if d["focus"]:
            rows_.append(f'    <span class="chip focus">创意侧重点：{esc(d["focus"])}</span>')
        rows_.append('  </div>')
        if d["reason"]:
            rows_.append(f'  <div class="reason"><b>模型依据：</b>{esc(d["reason"])}</div>')
        if show_pred:
            rows_.append(feedback_box(d))
        rows_.append('</div>')
        return "\n".join(rows_)

    sections = ""
    for (gt, pred), items in sorted(by_pair.items(), key=lambda kv: -len(kv[1])):
        sections += f"""
  <h3>Excel 判「{esc(gt)}」，模型判「{esc(pred)}」　<span class="cnt">{len(items)} 条</span></h3>
  <div class="grid">{''.join(card(d) for d in items)}</div>
"""

    agreed_sections = ""
    for lab in FOCUS3:
        items = by_label.get(lab, [])[:6]
        if not items:
            continue
        agreed_sections += f"""
  <h3>两边都判「{esc(lab)}」　<span class="cnt">共 {len(by_label[lab])} 条，展示 {len(items)} 条</span></h3>
  <div class="grid">{''.join(card(d, show_pred=False) for d in items)}</div>
"""

    # 统计：各方向的 focus 分布
    focus_rows = ""
    for (gt, pred), items in sorted(by_pair.items(), key=lambda kv: -len(kv[1])):
        c = Counter(d["focus"] or "（空）" for d in items)
        focus_rows += (f'<tr><td>{esc(gt)} → {esc(pred)}</td><td class="num">{len(items)}</td>'
                       f'<td>{esc("、".join(f"{k}×{v}" for k, v in c.most_common()))}</td></tr>')

    # footer（含反馈框的 JS）放在单独文件里：
    # 那段 JS 有大量 {} 和模板字符串，塞进 f-string 要双写转义，极易出错
    FOOTER = (ROOT / "_boundary_footer.html").read_text(encoding="utf-8") \
        .replace("__MIXED__", str(len(mixed)))

    doc = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>创意整活 / 游戏实况 / 高光剪辑 — 边界对比</title>
<style>
  :root {{ --bg:#f7f8fa; --panel:#fff; --ink:#111827; --muted:#6b7280; --line:#e5e7eb;
    --gt:#b91c1c; --gtbg:#fee2e2; --pred:#1d4ed8; --predbg:#dbeafe;
    --accent:#0f766e; --focus:#a16207; --focusbg:#fef9c3; --warn:#b45309; --warnbg:#fef3c7; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#0b0f14; --panel:#131a22; --ink:#e5e7eb; --muted:#9ca3af; --line:#263241;
      --gtbg:#3f1d1d; --predbg:#172554; --gt:#fca5a5; --pred:#93c5fd;
      --focusbg:#3b2f0b; --focus:#fde047; --warnbg:#3b2f0b; --warn:#fcd34d; }} }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink); line-height:1.65;
    font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif; }}
  .wrap {{ max-width:1180px; margin:0 auto; padding:36px 20px 70px; }}
  h1 {{ font-size:26px; margin:0 0 6px; }}
  h2 {{ font-size:20px; margin:42px 0 10px; border-bottom:1px solid var(--line); padding-bottom:8px; }}
  h3 {{ font-size:16px; margin:28px 0 12px; }}
  h3 .cnt {{ font-size:13px; color:var(--muted); font-weight:400; }}
  .sub {{ color:var(--muted); margin:0 0 20px; }}
  .panel {{ background:var(--panel); border:1px solid var(--line); border-radius:14px;
    padding:18px; margin:14px 0; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(330px,1fr)); gap:12px; }}
  .card {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:14px; }}
  .hd {{ display:flex; justify-content:space-between; align-items:center; gap:8px;
    flex-wrap:wrap; margin-bottom:8px; }}
  a.vid {{ color:var(--accent); text-decoration:none; font-size:13px; white-space:nowrap;
    border:1px solid var(--accent); padding:3px 10px; border-radius:6px; font-weight:600; }}
  a.vid:hover {{ background:var(--accent); color:#fff; }}
  .meta {{ font-size:11px; color:var(--muted); }}
  .title {{ font-size:14px; margin:6px 0 10px; }}
  .labels {{ display:flex; gap:6px; flex-wrap:wrap; margin-bottom:8px; }}
  .chip {{ display:inline-block; font-size:12px; padding:3px 9px; border-radius:999px; }}
  .chip.gt {{ background:var(--gtbg); color:var(--gt); font-weight:600; }}
  .chip.pred {{ background:var(--predbg); color:var(--pred); font-weight:600; }}
  .chip.focus {{ background:var(--focusbg); color:var(--focus); }}
  .reason {{ font-size:12px; color:var(--muted); border-top:1px dashed var(--line);
    padding-top:8px; margin-top:4px; }}
  table {{ width:100%; border-collapse:collapse; font-size:14px; }}
  th,td {{ text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); }}
  th {{ color:var(--muted); font-weight:600; font-size:12px; }}
  td.num,th.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  .alert {{ background:var(--warnbg); color:var(--warn); border:1px solid var(--warn);
    border-radius:12px; padding:14px 18px; margin:14px 0; font-size:14px; }}
  .ask {{ background:rgba(15,118,110,.08); border:1px solid var(--accent);
    border-radius:12px; padding:16px 18px; margin:16px 0; font-size:14px; }}
  ol,ul {{ margin:8px 0 0 20px; padding:0; }} li {{ margin:6px 0; }}
  footer {{ color:var(--muted); font-size:12px; margin-top:46px; }}

  /* ---- 反馈框 ---- */
  .fb {{ border-top:1px solid var(--line); margin-top:10px; padding-top:10px; }}
  .fb-q {{ font-size:12px; color:var(--muted); margin-bottom:6px; font-weight:600; }}
  .fb label {{ display:block; font-size:13px; margin:3px 0; cursor:pointer; }}
  .fb input[type=radio] {{ margin-right:6px; vertical-align:middle; }}
  .fb-other, .fb-why {{ width:100%; margin-top:6px; font:inherit; font-size:12.5px;
    padding:6px 8px; border:1px solid var(--line); border-radius:6px;
    background:var(--bg); color:var(--ink); resize:vertical; }}
  .fb-other {{ display:none; }}
  .fb.show-other .fb-other {{ display:block; }}
  .card.done {{ border-color:var(--accent); box-shadow:0 0 0 1px var(--accent) inset; }}

  /* ---- 悬浮工具条 ---- */
  .bar {{ position:fixed; left:0; right:0; bottom:0; z-index:50;
    background:var(--panel); border-top:1px solid var(--line);
    padding:10px 20px; display:flex; gap:12px; align-items:center;
    flex-wrap:wrap; box-shadow:0 -2px 12px rgba(0,0,0,.08); }}
  .bar .prog {{ font-size:13px; font-variant-numeric:tabular-nums; }}
  .bar .prog b {{ color:var(--accent); font-size:16px; }}
  .bar .track {{ flex:1; min-width:120px; height:8px; background:var(--line);
    border-radius:999px; overflow:hidden; }}
  .bar .fill {{ height:100%; width:0; background:var(--accent); transition:width .2s; }}
  .btn {{ font:inherit; font-size:13px; padding:7px 14px; border-radius:8px;
    border:1px solid var(--accent); background:var(--accent); color:#fff;
    cursor:pointer; font-weight:600; }}
  .btn.ghost {{ background:transparent; color:var(--accent); }}
  .btn:hover {{ opacity:.88; }}
  .toast {{ position:fixed; bottom:70px; left:50%; transform:translateX(-50%);
    background:var(--ink); color:var(--bg); padding:10px 18px; border-radius:8px;
    font-size:13px; z-index:60; opacity:0; transition:opacity .25s; pointer-events:none; }}
  .toast.show {{ opacity:1; }}
  body {{ padding-bottom:64px; }}
</style></head><body><div class="wrap">
  <h1>「创意整活 / 游戏实况 / 高光剪辑」边界对比</h1>
  <p class="sub">B站 500 条 + 抖音/快手 446 条 · 共 {len(mixed)} 条三类互混 + {len(agreed)} 条两边一致 ·
     模型 gemini-2.5-flash · {date.today()}</p>

  <div class="alert">
    <b>为什么把这三类单独拎出来。</b>
    v3（抖音/快手）和 v4（B站）两个数据集、三次提示词实验（v9/v10/v11）都卡在这里：
    改提示词会「按下葫芦浮起瓢」——同一处改动在某些表 +8pp、在另一些表 −10pp，
    因为不同工作表对这三类的判法本身就不一致。<b>这不是模型能力问题，是口径问题。</b>
  </div>

  <div class="ask">
    <b>希望你帮忙判断的是：</b>
    <ol>
      <li>逐条点开视频看，<b>Excel 和模型谁更合理</b>？还是两者都说得通（那就该列为口径分歧对）？</li>
      <li>如果 Excel 更合理，<b>你判断时看的是什么</b>？——是画面特征、剪辑方式、还是创作意图？</li>
      <li>这三类之间，<b>有没有一条可以写成规则的判定顺序</b>？
          比如「先看有没有脱离对局的设计 → 再看是否多段快剪 → 否则算实况」。</li>
    </ol>
    有了明确的判定顺序，我可以直接写进提示词并用 dev/test 验证是否真的涨。
  </div>

  <h2>一、混淆方向与标注员的「创意侧重点」</h2>
  <p class="sub">「创意侧重点」是 Excel 里标注员自己填的，往往能反映当时的判断角度。</p>
  <div class="panel"><table>
    <tr><th>混淆方向</th><th class="num">条数</th><th>Excel 的创意侧重点</th></tr>
    {focus_rows}
  </table></div>

  <h2>二、混淆样本（{len(mixed)} 条）</h2>
  {sections}

  <h2>三、对照：两边一致的样本</h2>
  <p class="sub">这些没有歧义，可以用来对比「什么情况下判法是清楚的」。</p>
  {agreed_sections}

{FOOTER}"""

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")
    print(f"三类互混 {len(mixed)} 条，两边一致 {len(agreed)} 条"
          f"（B站 {sum(1 for m in mixed if m['platform']=='B站')} +"
          f" 抖音/快手 {sum(1 for m in mixed if m['platform']!='B站')}）")
    for (gt, pred), items in sorted(by_pair.items(), key=lambda kv: -len(kv[1])):
        print(f"  {gt} → {pred}: {len(items)}")
    print(f"HTML -> {out}  ({out.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    main()
