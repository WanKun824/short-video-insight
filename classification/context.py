# -*- coding: utf-8 -*-
"""为每条待测视频构造检索上下文：话题标签先验 + 相似作品标注参考。

严格 leave-one-out：构造某条视频的上下文时，排除该视频自身。
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

from prompts import LABELS

TAG_RE = re.compile(r"#([^#\s]+)")
EXTRA = Path(__file__).resolve().parent / "data" / "original_hot300.json"


def tags_of(desc: str) -> set[str]:
    return set(TAG_RE.findall(desc or ""))


ANCHOR_LABELS = (
    "直播切片", "实况解说", "游戏实况", "创意整活", "游戏资讯",
    "游戏测评", "游戏吐槽", "活动剪辑", "剧情剪辑", "剧情解读",
)


def duration_bucket(d: float) -> str:
    if d < 30:
        return "0-30秒"
    if d < 90:
        return "30-90秒"
    if d < 180:
        return "90-180秒"
    if d < 600:
        return "3-10分钟"
    return "10分钟以上"


def fmt_duration(d: float) -> str:
    d = int(d or 0)
    return f"{d // 60}:{d % 60:02d}"


class ContextBuilder:
    def __init__(self, rows: list[dict], prior_path: str | None = None,
                 meta_path: str | None = None, reasons_path: str | None = None,
                 similar_k: int = 12):
        # 检索语料只用有真值的行：v3 新表有 94 条无标签样本（暗区 82 + dnfm 12），
        # 若混入会把空字符串当成一个标签，污染话题/作者/时长先验与相似示例。
        self.rows = [r for r in rows if (r.get("label") or "").strip()]
        rows = self.rows
        self.similar_k = similar_k
        # 相似示例的判断依据（来自模型在训练语料上的正确预测）
        self.reasons = {}
        if reasons_path and Path(reasons_path).is_file():
            self.reasons = json.loads(Path(reasons_path).read_text(encoding="utf-8"))
        self.tag_rows: dict[str, list[tuple[str, str]]] = defaultdict(list)
        self.row_by_id = {r["id"]: r for r in rows}
        for r in rows:
            for t in tags_of(r["desc"]):
                self.tag_rows[t].append((r["id"], r["label"]))
        self.prior = {}
        if prior_path:
            p = Path(prior_path)
            if p.is_file():
                self.prior = json.loads(p.read_text(encoding="utf-8"))

        # 视频元信息（ffprobe 提取的时长/分辨率）
        self.meta = {}
        if meta_path and Path(meta_path).is_file():
            self.meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))

        # 同作者先验 / 同时长桶先验（严格排除当前视频）
        self.author_rows: dict[str, list[tuple[str, str]]] = defaultdict(list)
        self.dur_rows: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for r in rows:
            if r.get("author"):
                self.author_rows[r["author"]].append((r["id"], r["label"]))
            d = (self.meta.get(r["id"]) or {}).get("duration")
            if d:
                self.dur_rows[duration_bucket(d)].append((r["id"], r["label"]))

        # 易混淆类的典型标注示例（固定，帮助对齐标注口径）
        self.anchors: dict[str, list[dict]] = {}
        by_label: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            by_label[r["label"]].append(r)
        for lab in ANCHOR_LABELS:
            cands = [x for x in by_label.get(lab, [])
                     if 15 <= len((x.get("desc") or "").strip()) <= 70]
            self.anchors[lab] = cands[:2]
        # 带“创意方向”标注的原始语料（仅保留 16 类）
        self.extra = []
        if EXTRA.is_file():
            for r in json.loads(EXTRA.read_text(encoding="utf-8")):
                if r.get("cat") in LABELS and r.get("dir"):
                    self.extra.append(r)

    def _tag_priors(self, row: dict, max_tags: int = 8) -> list[str]:
        out = []
        for t in tags_of(row["desc"]):
            items = [(i, lab) for i, lab in self.tag_rows.get(t, []) if i != row["id"]]
            if len(items) < 2:
                continue
            c = Counter(lab for _, lab in items)
            lab, cnt = c.most_common(1)[0]
            purity = cnt / len(items)
            if purity < 0.6:
                continue
            out.append((len(items) * purity, t, lab, cnt, len(items), purity))
        out.sort(reverse=True)
        lines = []
        for _, t, lab, cnt, n, purity in out[:max_tags]:
            lines.append(f"- #{t} → {lab}（{cnt}/{n}，{purity:.0%}）")
        return lines

    def _similar(self, row: dict, max_examples: int | None = None) -> list[str]:
        max_examples = max_examples or self.similar_k
        tset = tags_of(row["desc"])
        desc = (row["desc"] or "")[:300]
        scored = []
        seen = set()
        for r in self.rows + self.extra:
            if r["id"] == row["id"] or r["id"] in seen:
                continue
            seen.add(r["id"])
            other_tags = tags_of(r["desc"])
            union = tset | other_tags
            jac = len(tset & other_tags) / len(union) if union else 0.0
            ratio = SequenceMatcher(None, desc, (r["desc"] or "")[:300]).ratio()
            score = 3.0 * jac + 0.6 * ratio
            if score < 0.35:
                continue
            scored.append((score, r))
        scored.sort(key=lambda x: -x[0])
        # 每个标签最多 2 条，避免单一标签刷屏
        picked = []
        per_label = Counter()
        for score, r in scored:
            label = r.get("label") or r.get("cat", "")
            if per_label[label] >= 2:
                continue
            per_label[label] += 1
            picked.append(r)
            if len(picked) >= max_examples:
                break
        lines = []
        for r in picked:
            d = (r["desc"] or "").replace("\n", " ")[:120]
            if r.get("dir"):
                lines.append(f"- 描述：{d} → 创意方向：{r['dir']} → 标签：{r.get('cat', r.get('label',''))}")
            else:
                line = f"- 描述：{d} → 标签：{r.get('label') or r.get('cat','')}"
                reason = self.reasons.get(r["id"], "")
                if reason:
                    line += f"（判断依据：{reason[:100]}）"
                lines.append(line)
        return lines

    def _author_prior(self, row: dict) -> str:
        author = (row.get("author") or "").strip()
        if not author:
            return ""
        items = [(i, lab) for i, lab in self.author_rows.get(author, []) if i != row["id"]]
        if not items:
            return ""
        c = Counter(lab for _, lab in items)
        lines = [f"- 同作者「{author}」另有 {len(items)} 条已标注："
                 + "，".join(f"{lab} {n} 条" for lab, n in c.most_common(4))]
        top, n = c.most_common(1)[0]
        lines.append(f"  该作者历史主标签：{top}（{n}/{len(items)}，{n/len(items):.0%}）")
        return "【同作者历史标签（仅作参考，同一作者的系列内容通常同类）】\n" + "\n".join(lines)

    def _duration_prior(self, row: dict, meta: dict) -> str:
        d = (meta or {}).get("duration")
        if not d:
            return ""
        bucket = duration_bucket(d)
        items = [(i, lab) for i, lab in self.dur_rows.get(bucket, []) if i != row["id"]]
        if len(items) < 15:
            return ""
        c = Counter(lab for _, lab in items)
        top = c.most_common(3)
        return ("【同时长桶语料标签分布（仅作参考）】\n"
                f"- 本视频时长 {fmt_duration(d)}，属于「{bucket}」；该桶已标注 {len(items)} 条，"
                + "，".join(f"{lab} {n} 条（{n/len(items):.0%}）" for lab, n in top))

    def build(self, row: dict) -> str:
        priors = self._tag_priors(row)
        sims = self._similar(row)
        meta = self.meta.get(row["id"]) or {}
        parts = []
        if meta.get("duration"):
            w, h = int(meta.get("width") or 0), int(meta.get("height") or 0)
            orient = "横屏" if w >= h else "竖屏"
            parts.append(f"【视频元信息】时长 {fmt_duration(meta['duration'])}，分辨率 {w}x{h}（{orient}）")
        if priors:
            parts.append("【话题标签先验（来自已标注样本，已排除当前视频）】\n" + "\n".join(priors))
        author_part = self._author_prior(row)
        if author_part:
            parts.append(author_part)
        dur_part = self._duration_prior(row, meta)
        if dur_part:
            parts.append(dur_part)
        cand = self.prior.get(row["id"]) or []
        if cand:
            lines = [f"- {c['label']}（{c['prob']:.2f}）" for c in cand[:5]]
            parts.append("【文本模型候选（leave-one-out，仅作参考）】\n" + "\n".join(lines))
        if sims:
            parts.append("【相似作品标注参考（仅作参考）】\n" + "\n".join(sims))
        if not parts:
            return ""
        parts.append(
            "【使用说明】以上先验来自已标注样本，通常较准，但不是绝对；"
            "请优先在文本候选和相似作品标签中考虑，但如果视频实际形式与先验冲突"
            "（例如有连续解说、直播现场、官方活动等），以视频为准。"
        )
        return "\n\n".join(parts)
