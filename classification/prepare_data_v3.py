# -*- coding: utf-8 -*-
"""读取 v3 多游戏多平台 工作簿1.xlsx，输出统一样本集。

6 个 sheet（646 条）：
    2月抖音-地铁       159  抖音    和平精英·地铁逃生
    1月抖音-无畏契约   167  抖音    无畏契约
    1月快手-暗区突围    62  快手    暗区突围   （标签全为「创意整活」，无区分度）
    dnfm              100  抖音    DNF手游    （12 条标签为 "/"，无真值）
    暗区               82  抖音    暗区突围   （无内容类型列，纯预测）
    金铲铲             76  小红书  金铲铲之战 （平台暂无下载器）

输出：
    data_v3/ground_truth.jsonl   全量统一格式（label 为空表示无真值）
    data_v3/ids_all.txt          抖音 ID（供 download_all.py）
    data_v3/stats.json           分 sheet / 分标签统计
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data_v3"
DATA.mkdir(parents=True, exist_ok=True)

DEFAULT_SRC = Path(r"C:\Users\v_wkunnwan\Downloads\工作簿1.xlsx")

# 列用「下标」定位，不用列名：「1月快手-暗区突围」有两个同名「视频类型」列
# (idx 3 = 内容标签，idx 12 = 视频/图文形式)，按列名取会拿到后者。
SHEETS = {
    "2月抖音-地铁": dict(
        platform="抖音", game="peace", title="和平精英地铁逃生",
        desc=1, link=3, label=4,
        extra={"作品分类": 2, "作者属性": 5, "likes": 6, "comments": 7, "shares": 8,
               "collects": 9, "author": 10, "author_id": 11, "author_cat": 12,
               "author_fans": 14},
    ),
    "1月抖音-无畏契约": dict(
        platform="抖音", game="valorant", title="无畏契约",
        desc=1, link=3, label=4,
        extra={"作品分类": 2, "作者属性": 5, "likes": 6, "comments": 7, "shares": 8,
               "collects": 9, "author": 10, "author_id": 11, "author_fans": 12,
               "author_cat": 13},
    ),
    "1月快手-暗区突围": dict(
        platform="快手", game="arena", title="暗区突围",
        desc=1, link=2, label=3,
        extra={"作者属性": 4, "author": 5, "author_id": 6, "author_fans": 8,
               "duration": 13, "publish_time": 14, "comments": 16, "shares": 17,
               "likes": 18, "views": 19, "collects": 20},
    ),
    "dnfm": dict(
        platform="抖音", game="dnfm", title="DNF手游",
        desc=0, link=2, label=3,
        extra={"author": 1},
    ),
    "暗区": dict(
        platform="抖音", game="arena", title="暗区突围",
        desc=4, link=10, label=None,  # 无内容类型列，改从「内容精炼（人工)」解析
        refined=5,                    # 「内容精炼（人工)」
        extra={"author": 1, "author_id": 2, "作品分类": 7, "duration": 8,
               "商业属性": 13, "publish_time": 14, "collects": 15, "shares": 16,
               "comments": 17, "likes": 18},
    ),
    "金铲铲": dict(
        platform="小红书", game="tft", title="金铲铲之战",
        desc=0, link=1, label=2,
        desc_extra=[17, 18],          # 笔记内容 + 话题并入描述（标题太短）
        extra={"创意侧重点": 3, "作者属性": 4, "publish_time": 5, "author": 6,
               "author_id": 7, "author_fans": 9, "collects": 11, "comments": 12,
               "shares": 13, "likes": 14, "笔记内容标签": 19},
    ),
}

ID_PATTERNS = {
    "抖音": re.compile(r"(\d{15,20})"),
    "快手": re.compile(r"/short-video/([A-Za-z0-9_-]+)"),
    "小红书": re.compile(r"/(?:item|explore)/([0-9a-f]{20,32})"),
}

# 新表出现的非法/异常标签归一化
LABEL_FIX = {
    "/": "",                      # dnfm 未标注
    "游戏实况与剪辑": "游戏实况",   # dnfm 合并口径 → 取主类
}

# 「暗区」表没有内容类型列，但「内容精炼（人工)」是 `类型：描述` 格式，
# 冒号前就是人工标的内容类型。但那是一套「官方物料视角」的分类体系，
# 9 个取值里只有 3 个能和 24 类标签直接对应，其余需要映射。
#
# 格式：人工类型 -> (24 类标签, 是否推断映射, 是否宽口径)
# 「宽口径」指该人工类型是个大筐，内部横跨多个 24 类标签，不应当成精确真值：
#   例如人工「游戏宣传」35 条里，描述自己写明了子类型——
#   「剧情PV+4月29日正式上线资讯」「定档发布会活动现场混剪+现场采访」
#   「创意整活-游戏创意剪辑」「访谈动画二创小剧场」，
#   在 24 类体系下分别是游戏资讯 / 活动剪辑 / 创意整活 / 二创类。
#   这类样本的「错误」大多是体系差异而非模型理解错，报告里会单独标出。
ANQU_LABEL_MAP = {
    # 直接对应
    "游戏攻略": ("游戏攻略", False, False),
    "创意整活": ("创意整活", False, False),
    # 宽口径：官方物料统称，内部横跨多个 24 类标签
    "游戏宣传": ("游戏宣传", False, True),
    "幕后故事": ("游戏资讯", True, True),    # 主策访谈/开发进度/玩家群像 vlog
    # 推断映射
    "赛事宣传": ("电竞相关", True, False),    # 表演赛/选手/战队预告
    "图文资讯类": ("游戏资讯", True, False),  # 招募/赛事预告图文
    "制作人有问必答": ("游戏资讯", True, False),
    "内容征集": ("游戏资讯", True, False),
    "直播活动": ("直播切片", True, False),    # 官方直播间
}
# 有 1 条漏了前缀，正文和「赛事宣传」那 10 条格式完全一致
ANQU_NO_PREFIX_RE = re.compile(r"表演赛|赛事|明星摸金")
ANQU_PREFIX_RE = re.compile(r"^([^：:]{2,8})[：:]")


def parse_anqu_label(refined: str) -> tuple[str, str, bool, bool]:
    """解析「内容精炼（人工)」→ (24 类标签, 原始人工类型, 是否推断映射, 是否宽口径)。"""
    first = (refined or "").split("\n")[0].strip()
    m = ANQU_PREFIX_RE.match(first)
    if m:
        raw = m.group(1).strip()
        if raw in ANQU_LABEL_MAP:
            lab, inferred, broad = ANQU_LABEL_MAP[raw]
            return lab, raw, inferred, broad
        return "", raw, False, False
    if ANQU_NO_PREFIX_RE.search(first):
        return "电竞相关", "赛事宣传(无前缀)", True, False
    return "", "", False, False


def strip_anqu_prefix(refined: str) -> str:
    """剥掉精炼文本首行的「类型：」前缀，只保留内容正文。

    必须做这一步：该前缀就是标签来源，若原样并入 desc，模型不需要看视频，
    只要把「游戏宣传：」抄出来就能答对，评测结果会被严重高估。
    """
    if not refined:
        return ""
    lines = refined.split("\n")
    m = ANQU_PREFIX_RE.match(lines[0].strip())
    if m:
        lines[0] = lines[0].strip()[m.end():].strip()
    return "\n".join(l for l in lines if l.strip())


def extract_id(link: str, platform: str) -> str:
    m = ID_PATTERNS[platform].search(link or "")
    return m.group(1) if m else ""


def read_rows(src: Path) -> list[dict]:
    wb = load_workbook(src, read_only=True, data_only=True)
    rows: list[dict] = []
    for sheet, cfg in SHEETS.items():
        ws = wb[sheet]
        values = list(ws.iter_rows(values_only=True))
        header = [str(c).strip() if c is not None else "" for c in values[0]]

        def cell(r, i):
            if i is None or i >= len(r):
                return ""
            v = r[i]
            return str(v).strip() if v is not None else ""

        for r in values[1:]:
            if not any(x is not None for x in r):
                continue
            link = cell(r, cfg["link"])
            if not link:
                continue
            vid = extract_id(link, cfg["platform"])
            if not vid:
                continue

            desc = cell(r, cfg["desc"])
            for i in cfg.get("desc_extra", []):
                v = cell(r, i)
                if v:
                    desc = f"{desc}\n{v}" if desc else v

            raw_label = cell(r, cfg["label"]) if cfg["label"] is not None else ""
            label = LABEL_FIX.get(raw_label, raw_label)
            label_raw_human = ""
            label_inferred = False
            label_broad = False
            refined = ""

            # 「暗区」表：标签从「内容精炼（人工)」的冒号前缀解析
            if cfg.get("refined") is not None:
                refined = cell(r, cfg["refined"])
                label, label_raw_human, label_inferred, label_broad = parse_anqu_label(refined)
                # 精炼文本的正文信息量大，并入描述供模型参考，
                # 但必须剥掉首行的「类型：」前缀——那就是标签本身，
                # 直接并入会造成标签泄漏（模型只需对「游戏宣传：」做字符串匹配）。
                body = strip_anqu_prefix(refined)
                if body:
                    desc = f"{desc}\n{body}" if desc else body

            row = {
                "id": vid,
                "desc": desc,
                "link": link,
                "label": label,
                "platform": cfg["platform"],
                "sheet": sheet,
                "game": cfg["game"],
                "game_title": cfg["title"],
            }
            if raw_label and raw_label != label:
                row["label_raw"] = raw_label
            if label_raw_human:
                row["label_raw"] = label_raw_human
                row["label_source"] = "内容精炼（人工)"
                if label_inferred:
                    row["label_inferred"] = True
                if label_broad:
                    row["label_broad"] = True
            for k, i in cfg["extra"].items():
                v = cell(r, i)
                if v:
                    row[k] = v
            rows.append(row)
        print(f"  [{sheet}] {len(values) - 1} 行 -> 表头 {len(header)} 列")
    wb.close()

    # 去重（同平台内按 ID；暗区 / 1月快手-暗区突围 两表可能与其它表重叠）
    seen: dict[tuple, dict] = {}
    uniq = []
    dups = 0
    for r in rows:
        key = (r["platform"], r["id"])
        if key in seen:
            dups += 1
            # 有真值的版本优先保留
            if not seen[key]["label"] and r["label"]:
                seen[key].update({k: v for k, v in r.items() if v})
            continue
        seen[key] = r
        uniq.append(r)
    if dups:
        print(f"  去重 {dups} 条")
    return uniq


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", default=str(DEFAULT_SRC))
    args = ap.parse_args()

    src = Path(args.xlsx)
    if not src.is_file():
        raise SystemExit(f"找不到 Excel: {src}")

    print(f"读取 {src}")
    rows = read_rows(src)
    print(f"统一读取 {len(rows)} 条")

    gt = DATA / "ground_truth.jsonl"
    with gt.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"-> {gt}")

    dy = [r["id"] for r in rows if r["platform"] == "抖音"]
    (DATA / "ids_all.txt").write_text("\n".join(dy), encoding="utf-8")
    print(f"-> {DATA / 'ids_all.txt'}（抖音 {len(dy)} 个）")

    # 统计
    by_sheet = defaultdict(list)
    for r in rows:
        by_sheet[r["sheet"]].append(r)
    stats = {}
    print("\n分 sheet 统计：")
    print(f"{'sheet':<18}{'平台':<8}{'游戏':<16}{'样本':>5}{'有真值':>7}")
    for sheet, items in by_sheet.items():
        labeled = [r for r in items if r["label"]]
        c = Counter(r["label"] for r in labeled)
        stats[sheet] = {
            "platform": items[0]["platform"], "game": items[0]["game"],
            "game_title": items[0]["game_title"], "n": len(items),
            "labeled": len(labeled), "labels": dict(c.most_common()),
        }
        print(f"{sheet:<18}{items[0]['platform']:<8}{items[0]['game_title']:<16}"
              f"{len(items):>5}{len(labeled):>7}")

    print("\n平台分布：", dict(Counter(r["platform"] for r in rows)))
    all_c = Counter(r["label"] for r in rows if r["label"])
    print(f"全量标签（{sum(all_c.values())} 条有真值，{len(all_c)} 类）：")
    for k, v in all_c.most_common():
        print(f"  {k:<10}{v:>4}")

    (DATA / "stats.json").write_text(
        json.dumps({"total": len(rows), "sheets": stats,
                    "labels": dict(all_c.most_common())},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n-> {DATA / 'stats.json'}")


if __name__ == "__main__":
    main()
