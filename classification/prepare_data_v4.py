# -*- coding: utf-8 -*-
"""读取 10月TOP500内容分类.xlsx 的 B站 部分，分层抽样 500 条。

和 v3 的差别
------------
该表 16 个工作表（4 游戏 × 4 平台），这里只取 B站 4 个表（1884 条）。
4 个 B站 表的**列数和列顺序完全不同**（17 / 19 / 26 / 26 列），
所以这里按**列名**定位而不是按下标——与 v3 相反，v3 是因为有重复列名才用下标。

抽样
----
按「工作表 × 内容类型」分层，最大余数法分配 500 条名额，每层至少 1 条。
这样四个游戏的标签分布都能被覆盖，不会让「创意整活」（占 26.5%）吃掉大部分名额。
"""
from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data_v4"
DATA.mkdir(parents=True, exist_ok=True)

DEFAULT_SRC = Path(r"C:\Users\v_wkunnwan\Downloads\10月TOP500内容分类.xlsx")
BV_RE = re.compile(r"(BV[0-9A-Za-z]+)")

# 工作表 -> 游戏代号（prompts 里按这个路由）
GAME_OF = {
    "王者荣耀-B站": ("king", "王者荣耀"),
    "和平精英-B站": ("peace", "和平精英"),
    "蛋仔-B站": ("eggy", "蛋仔派对"),
    "三角洲-B站": ("delta", "三角洲行动"),
}

# 列名可能有的几种写法 -> 统一字段
FIELD_ALIASES = {
    "title": ["视频标题"],
    "desc": ["视频描述"],
    "link": ["视频链接"],
    "label": ["内容类型"],
    "focus": ["创意侧重点"],
    "author_type": ["作者类型"],
    "author": ["UP主名称"],
    "author_fans": ["UP主粉丝数"],
    "duration": ["视频时长"],
    "publish_time": ["发布时间"],
    "zone": ["视频分区"],
    "views": ["播放数"],
    "likes": ["获赞数"],
    "coins": ["投币数"],
    "collects": ["收藏数"],
    "shares": ["分享数"],
    "comments": ["评论数"],
    "danmaku": ["弹幕数"],
    "mid": ["mid"],
}

# 该表里出现的异常标签 -> 归一
LABEL_FIX = {
    "ai": "", "ai吉莉": "", "豆包": "",          # 不是内容类型，是素材/工具名
    "地铁逃生": "", "地铁竞速杯": "",              # 游戏模式名
    "绿洲启元：吃丹修仙": "", "绿洲启元-吃丹修仙": "", "绿州启元-吃丹修仙": "",
    "整活小剧场": "创意整活", "整活向玩法": "创意整活",
    "动画二创": "二创类",
    "武器对比": "武器展示",
    "英雄展示": "角色展示",
    "幕后故事": "游戏资讯",          # 不在 24 类里，按 v3 暗区表口径归资讯
    "其他衍生内容": "", "游戏周边": "",
}


def parse_duration(s: str) -> int:
    """该表的时长格式是 `分:秒:00`，不是 时:分:秒。

    实测：`00:09:00` 是 9 分钟、`01:14:00` 是 74 分钟。
    若按 时:分:秒 解析会得到「中位 2.9 小时」这种不合理的数。
    最终评测用 ffprobe 取真实时长，这里只作参考。
    """
    parts = [x for x in (s or "").split(":") if x.isdigit()]
    if len(parts) >= 2:
        return int(parts[0]) * 60 + int(parts[1])
    return int(parts[0]) if parts else 0


def read_rows(src: Path) -> list[dict]:
    wb = load_workbook(src, read_only=True, data_only=True)
    rows: list[dict] = []
    for sheet, (game, title) in GAME_OF.items():
        if sheet not in wb.sheetnames:
            print(f"  [跳过] {sheet} 不存在")
            continue
        ws = wb[sheet]
        values = list(ws.iter_rows(values_only=True))
        hdr = [str(c).strip() if c is not None else "" for c in values[0]]
        idx = {h: i for i, h in enumerate(hdr)}

        # 建立「统一字段 -> 实际列下标」的映射
        col = {}
        for field, names in FIELD_ALIASES.items():
            for n in names:
                if n in idx:
                    col[field] = idx[n]
                    break

        def cell(r, field):
            i = col.get(field)
            if i is None or i >= len(r) or r[i] is None:
                return ""
            return str(r[i]).strip()

        n_before = len(rows)
        for r in values[1:]:
            if not any(x is not None for x in r):
                continue
            link = cell(r, "link")
            m = BV_RE.search(link)
            if not m:
                continue
            raw = cell(r, "label")
            label = LABEL_FIX.get(raw, raw)

            # 标题 + 描述一起作为 desc：B站 很多视频描述为空，标题信息量更大
            desc = cell(r, "title")
            body = cell(r, "desc")
            if body and body != desc:
                desc = f"{desc}\n{body}" if desc else body

            row = {
                "id": m.group(1),
                "desc": desc,
                "link": link,
                "label": label,
                "platform": "B站",
                "sheet": sheet,
                "game": game,
                "game_title": title,
            }
            if raw and raw != label:
                row["label_raw"] = raw
            for f in ("focus", "author", "author_type", "author_fans", "duration",
                      "publish_time", "zone", "views", "likes", "coins", "collects",
                      "shares", "comments", "danmaku", "mid"):
                v = cell(r, f)
                if v:
                    row[f] = v
            if row.get("duration"):
                row["duration_sec"] = parse_duration(row["duration"])
            rows.append(row)
        print(f"  [{sheet}] {len(values)-1} 行 → {len(rows)-n_before} 条"
              f"（{len(hdr)} 列，已按列名定位）")
    wb.close()

    seen = set()
    uniq = []
    for r in rows:
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        uniq.append(r)
    if len(uniq) != len(rows):
        print(f"  去重 {len(rows)-len(uniq)} 条")
    return uniq


def allocate(total: int, counts: Counter) -> dict:
    """最大余数法按比例分配，每层至少 1 条。"""
    keys = list(counts)
    n = sum(counts.values())
    raw = {k: total * counts[k] / n for k in keys}
    alloc = {k: int(raw[k]) for k in keys}
    for k in keys:
        if alloc[k] == 0:
            alloc[k] = 1
    while sum(alloc.values()) > total:
        cand = [k for k in keys if alloc[k] > 1]
        if not cand:
            break
        alloc[max(cand, key=lambda x: alloc[x] - raw[x])] -= 1
    while sum(alloc.values()) < total:
        alloc[max(keys, key=lambda x: raw[x] - alloc[x])] += 1
    return alloc


def sample(rows: list[dict], n: int, seed: int = 42) -> list[dict]:
    """按「工作表 × 内容类型」分层抽样。"""
    labeled = [r for r in rows if r["label"]]
    strata = defaultdict(list)
    for r in labeled:
        strata[(r["sheet"], r["label"])].append(r)
    counts = Counter({k: len(v) for k, v in strata.items()})
    alloc = allocate(n, counts)
    rng = random.Random(seed)
    picked = []
    for k, quota in alloc.items():
        items = sorted(strata[k], key=lambda x: x["id"])
        rng.shuffle(items)
        picked.extend(items[:min(quota, len(items))])
    # 名额没用满时（某些层样本不足），从剩余样本里按原分布补齐
    if len(picked) < n:
        rest = [r for r in labeled if r not in picked]
        rng.shuffle(rest)
        picked.extend(rest[:n - len(picked)])
    picked.sort(key=lambda x: (x["sheet"], x["label"], x["id"]))
    return picked[:n]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", default=str(DEFAULT_SRC))
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    src = Path(args.xlsx)
    if not src.is_file():
        raise SystemExit(f"找不到 Excel: {src}")
    print(f"读取 {src}")
    rows = read_rows(src)
    labeled = [r for r in rows if r["label"]]
    print(f"\nB站全量 {len(rows)} 条，有效标签 {len(labeled)} 条")

    (DATA / "all.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")

    picked = sample(rows, args.n, args.seed)
    gt = DATA / "ground_truth.jsonl"
    gt.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in picked),
                  encoding="utf-8")
    (DATA / "ids.txt").write_text("\n".join(r["id"] for r in picked), encoding="utf-8")

    print(f"\n分层抽样 {len(picked)} 条 -> {gt}")
    print(f"\n{'工作表':<16}{'抽样':>5}{'全量':>6}  标签分布")
    for sh in GAME_OF:
        sub = [r for r in picked if r["sheet"] == sh]
        full = [r for r in labeled if r["sheet"] == sh]
        if not sub:
            continue
        c = Counter(r["label"] for r in sub)
        print(f"{sh:<16}{len(sub):>5}{len(full):>6}  "
              + "、".join(f"{k}×{v}" for k, v in c.most_common(5)))

    print(f"\n抽样标签分布（{len(set(r['label'] for r in picked))} 类）：")
    for k, v in Counter(r["label"] for r in picked).most_common():
        full_n = sum(1 for r in labeled if r["label"] == k)
        print(f"  {k:<10}{v:>4}  （全量 {full_n}，抽样率 {v/full_n:.0%}）")

    (DATA / "stats.json").write_text(json.dumps({
        "source": str(src), "total_bilibili": len(rows), "labeled": len(labeled),
        "sampled": len(picked), "seed": args.seed,
        "by_sheet": {sh: sum(1 for r in picked if r["sheet"] == sh) for sh in GAME_OF},
        "by_label": dict(Counter(r["label"] for r in picked).most_common()),
        "full_by_label": dict(Counter(r["label"] for r in labeled).most_common()),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n-> {DATA / 'stats.json'}")


if __name__ == "__main__":
    main()
