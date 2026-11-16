# -*- coding: utf-8 -*-
"""候选口径豁免对的判定页：每个对给 3 条样本，由业务方决定是否豁免。

为什么不直接加
--------------
把全部 21 个候选对都豁免，准确率正好是 95.26%——数字好看，但里面混着
`才艺展示/二创类`、`创意剪辑/颜值COS` 这类明显不是同一回事的对。
豁免对的本质是「业务上认为这两类可以互换」，这个判断必须由业务方做，
我自己加等于替业务方定口径，而且会让准确率失去意义。

页面做法和「三类边界对比」一致：每个候选对给 3 条实际样本 + 视频链接，
选「可以豁免 / 不能豁免 / 再看看」，导出 JSON 后用
apply_pair_feedback.py 直接写回评测口径。
"""
from __future__ import annotations

import argparse
import html
import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent

CURRENT = {("游戏宣传", "游戏资讯"), ("游戏实况", "实况解说"),
           ("直播切片", "实况解说"), ("二创类", "创意整活"),
           ("创意剪辑", "创意整活"), ("实况解说", "创意整活"),
           ("创意整活", "游戏实况"), ("创意整活", "高光剪辑"),
           ("游戏实况", "高光剪辑")}
CURRENT |= {(b, a) for a, b in CURRENT}


def esc(s):
    return html.escape(str(s or ""))


def read_jsonl(p: Path):
    if not p.is_file():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gt", default=str(ROOT / "data_v4" / "ground_truth.jsonl"))
    # 兼容两种布局：仓库里是 out/v4_final_predictions.jsonl，
    # 工作目录里是 out/v4_bili/predictions.jsonl
    _d = ROOT / "out" / "v4_final_predictions.jsonl"
    _w = ROOT / "out" / "v4_bili" / "predictions.jsonl"
    ap.add_argument("--preds", default=str(_d if _d.is_file() else _w))
    ap.add_argument("--meta", default=str(ROOT / "data_v4" / "media_meta.json"))
    ap.add_argument("--per-pair", type=int, default=3, help="每个候选对展示几条样本")
    ap.add_argument("--out", default=str(ROOT / "out" / "候选豁免对判定.html"))
    args = ap.parse_args()

    rows = {r["id"]: r for r in read_jsonl(Path(args.gt))}
    meta = json.loads(Path(args.meta).read_text(encoding="utf-8")) if Path(args.meta).is_file() else {}
    P = {}
    for x in read_jsonl(Path(args.preds)):
        if x.get("id") and x.get("label"):
            P[x["id"]] = x

    cov = [i for i in P if rows.get(i) and rows[i]["label"] and P[i].get("media")]
    if not cov:
        raise SystemExit(f"预测为空或路径不对：{args.preds}")

    def ok(i, extra=frozenset()):
        r, p = rows[i], P[i]["label"]
        return p == r["label"] or (r["label"], p) in (CURRENT | extra)

    base = sum(1 for i in cov if ok(i))
    n = len(cov)

    # 候选对 + 每个对的样本
    rest = Counter((rows[i]["label"], P[i]["label"]) for i in cov if not ok(i))
    samples = defaultdict(list)
    for i in cov:
        if ok(i):
            continue
        key = tuple(sorted((rows[i]["label"], P[i]["label"])))
        samples[key].append(i)

    cands = []
    for key, ids in samples.items():
        a, b = key
        ex = frozenset({(a, b), (b, a)})
        gain = sum(1 for i in cov if ok(i, ex)) - base
        if gain >= 2:
            cands.append({"a": a, "b": b, "gain": gain, "ids": ids})
    cands.sort(key=lambda c: -c["gain"])

    cum = base
    cards = ""
    for ci, c in enumerate(cands):
        cum += c["gain"]
        a, b = c["a"], c["b"]
        pid = f"{a}__{b}"
        sample_html = ""
        for i in c["ids"][:args.per_pair]:
            r, p = rows[i], P[i]
            dur = int((meta.get(i) or {}).get("duration") or 0)
            sample_html += f"""
      <div class="smp">
        <a class="vid" href="{esc(r.get('link',''))}" target="_blank" rel="noopener">▸ 视频</a>
        <span class="meta">{esc(r.get('game_title',''))} · {esc(r.get('author',''))} · {dur//60}:{dur%60:02d}</span>
        <div class="t">{esc((r.get('desc') or '').replace(chr(10),' ')[:110])}</div>
        <div class="lb"><span class="chip gt">Excel：{esc(r['label'])}</span>
          <span class="chip pred">模型：{esc(p['label'])}</span>
          {f'<span class="chip focus">侧重点：{esc(r.get("focus",""))}</span>' if r.get('focus') else ''}</div>
        <div class="rsn">{esc((p.get('reason') or '')[:230])}</div>
      </div>"""

        cards += f"""
  <div class="pair" data-a="{esc(a)}" data-b="{esc(b)}" data-gain="{c['gain']}">
    <div class="phd">
      <div class="pname"><span class="chip gt">{esc(a)}</span>
        <span class="vs">／</span><span class="chip pred">{esc(b)}</span></div>
      <div class="pgain">豁免后 +{c['gain']} 条 · 累计可到 {cum/n:.1%}</div>
    </div>
    <div class="smps">{sample_html}</div>
    <div class="fb">
      <div class="fb-q">这两类在业务上可以互换吗？</div>
      <label><input type="radio" name="p_{ci}" value="yes"> ✅ 可以豁免（两种判法都能接受）</label>
      <label><input type="radio" name="p_{ci}" value="no"> ❌ 不能豁免（是两回事，模型判错了）</label>
      <label><input type="radio" name="p_{ci}" value="later"> ⏸ 再看看 / 需要更多样本</label>
      <textarea class="fb-why" rows="2" placeholder="可选：为什么？（会写进报告的口径说明）"></textarea>
    </div>
  </div>"""

    FOOTER = (ROOT / "_pair_footer.html").read_text(encoding="utf-8") \
        .replace("__TOTAL__", str(len(cands))) \
        .replace("__BASE__", f"{base/n:.4f}") \
        .replace("__N__", str(n)) \
        .replace("__BASEN__", str(base))

    doc = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>候选口径豁免对 — 请判定</title>
<style>
  :root {{ --bg:#f7f8fa; --panel:#fff; --ink:#111827; --muted:#6b7280; --line:#e5e7eb;
    --gt:#b91c1c; --gtbg:#fee2e2; --pred:#1d4ed8; --predbg:#dbeafe; --accent:#0f766e;
    --focus:#a16207; --focusbg:#fef9c3; --warn:#b45309; --warnbg:#fef3c7; --ok:#047857; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#0b0f14; --panel:#131a22; --ink:#e5e7eb; --muted:#9ca3af; --line:#263241;
      --gtbg:#3f1d1d; --predbg:#172554; --gt:#fca5a5; --pred:#93c5fd;
      --focusbg:#3b2f0b; --focus:#fde047; --warnbg:#3b2f0b; --warn:#fcd34d; --ok:#6ee7b7; }} }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink); line-height:1.65; padding-bottom:70px;
    font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif; }}
  .wrap {{ max-width:1000px; margin:0 auto; padding:36px 20px 40px; }}
  h1 {{ font-size:25px; margin:0 0 6px; }}
  h2 {{ font-size:19px; margin:36px 0 10px; border-bottom:1px solid var(--line); padding-bottom:8px; }}
  .sub {{ color:var(--muted); margin:0 0 20px; }}
  .alert {{ background:var(--warnbg); color:var(--warn); border:1px solid var(--warn);
    border-radius:12px; padding:14px 18px; margin:14px 0; font-size:14px; }}
  .ask {{ background:rgba(15,118,110,.08); border:1px solid var(--accent);
    border-radius:12px; padding:16px 18px; margin:16px 0; font-size:14px; }}
  .pair {{ background:var(--panel); border:1px solid var(--line); border-radius:14px;
    padding:16px; margin:14px 0; }}
  .pair.done {{ border-color:var(--accent); box-shadow:0 0 0 1px var(--accent) inset; }}
  .phd {{ display:flex; justify-content:space-between; align-items:center;
    gap:10px; flex-wrap:wrap; margin-bottom:12px; }}
  .pname {{ font-size:16px; }} .vs {{ color:var(--muted); margin:0 4px; }}
  .pgain {{ font-size:12px; color:var(--muted); }}
  .smps {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:10px; }}
  .smp {{ background:var(--bg); border:1px solid var(--line); border-radius:10px; padding:10px; }}
  .smp .meta {{ font-size:11px; color:var(--muted); margin-left:6px; }}
  .smp .t {{ font-size:13px; margin:6px 0; }}
  .smp .lb {{ display:flex; gap:5px; flex-wrap:wrap; margin-bottom:6px; }}
  .smp .rsn {{ font-size:11.5px; color:var(--muted); border-top:1px dashed var(--line); padding-top:6px; }}
  a.vid {{ color:var(--accent); text-decoration:none; font-size:12px; font-weight:600;
    border:1px solid var(--accent); padding:2px 8px; border-radius:6px; }}
  a.vid:hover {{ background:var(--accent); color:#fff; }}
  .chip {{ display:inline-block; font-size:11.5px; padding:2px 8px; border-radius:999px; }}
  .chip.gt {{ background:var(--gtbg); color:var(--gt); font-weight:600; }}
  .chip.pred {{ background:var(--predbg); color:var(--pred); font-weight:600; }}
  .chip.focus {{ background:var(--focusbg); color:var(--focus); }}
  .fb {{ border-top:1px solid var(--line); margin-top:12px; padding-top:10px; }}
  .fb-q {{ font-size:12.5px; color:var(--muted); font-weight:600; margin-bottom:6px; }}
  .fb label {{ display:block; font-size:13.5px; margin:4px 0; cursor:pointer; }}
  .fb input[type=radio] {{ margin-right:7px; vertical-align:middle; }}
  .fb-why {{ width:100%; margin-top:7px; font:inherit; font-size:12.5px; padding:6px 8px;
    border:1px solid var(--line); border-radius:6px; background:var(--bg);
    color:var(--ink); resize:vertical; }}
  table {{ width:100%; border-collapse:collapse; font-size:14px; }}
  th,td {{ text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); }}
  th {{ color:var(--muted); font-weight:600; font-size:12px; }}
  td.num,th.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  code {{ background:var(--line); padding:1px 5px; border-radius:4px; font-size:13px; }}
  .bar {{ position:fixed; left:0; right:0; bottom:0; z-index:50; background:var(--panel);
    border-top:1px solid var(--line); padding:10px 20px; display:flex; gap:12px;
    align-items:center; flex-wrap:wrap; box-shadow:0 -2px 12px rgba(0,0,0,.08); }}
  .bar .prog {{ font-size:13px; font-variant-numeric:tabular-nums; }}
  .bar .prog b {{ color:var(--accent); font-size:16px; }}
  .bar .est {{ font-size:13px; color:var(--muted); }}
  .bar .est b {{ color:var(--ok); font-size:15px; }}
  .btn {{ font:inherit; font-size:13px; padding:7px 14px; border-radius:8px;
    border:1px solid var(--accent); background:var(--accent); color:#fff;
    cursor:pointer; font-weight:600; }}
  .btn.ghost {{ background:transparent; color:var(--accent); }}
  .toast {{ position:fixed; bottom:70px; left:50%; transform:translateX(-50%);
    background:var(--ink); color:var(--bg); padding:10px 18px; border-radius:8px;
    font-size:13px; z-index:60; opacity:0; transition:opacity .25s; pointer-events:none; }}
  .toast.show {{ opacity:1; }}
</style></head><body><div class="wrap">
  <h1>候选口径豁免对 — 请判定</h1>
  <p class="sub">B站 500 条样本 · 当前 {base}/{n} = {base/n:.2%} ·
     {len(cands)} 个候选对待判定 · {date.today()}</p>

  <div class="alert">
    <b>这些对我没有自己加，原因是：豁免对的含义是「业务上认为这两类可以互换」。</b><br>
    如果把下面 {len(cands)} 个对全部豁免，准确率会到 <b>{cum/n:.2%}</b>——数字很好看，
    但里面混着 <code>才艺展示／二创类</code>、<code>创意剪辑／颜值COS</code> 这种
    明显不是一回事的对。那样做出来的准确率不再反映分类质量，
    下游拿去做分发会出问题，而且被追问时无法解释。
  </div>

  <div class="ask">
    <b>怎么判：</b>每个对下面有 3 条真实样本（带视频链接、Excel 标签、模型标签、模型依据）。
    点开看完问自己一句——<b>如果一条内容被打成另一个标签，业务上能接受吗？</b>
    <ul>
      <li>能接受 → 可以豁免（像你上次判的「创意整活／游戏实况」那样）</li>
      <li>不能接受 → 不能豁免，这是模型真错，留在错误里，我去改提示词/规则</li>
      <li>拿不准 → 选「再看看」，我多给些样本</li>
    </ul>
    右下角实时显示<b>按你的选择能到多少准确率</b>。填完点导出给我。
  </div>

  <h2>候选对（{len(cands)} 个，按收益排序）</h2>
  {cards}
{FOOTER}"""

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")
    print(f"当前 {base}/{n} = {base/n:.2%}")
    print(f"候选对 {len(cands)} 个，全部采纳可到 {cum/n:.2%}")
    print(f"HTML -> {out}  ({out.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    main()
