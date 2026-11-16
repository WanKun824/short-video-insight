# -*- coding: utf-8 -*-
"""内容类型分类器：视频 + 作品描述 -> 16 类标签。

用法示例：
    python content_eval/classify.py --prompt-version v1 --sample-per-label 2 -c 4
    python content_eval/classify.py --prompt-version v2 --concurrency 6
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import random
import re
import subprocess
import sys
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from time import monotonic

import httpx

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from prompts import LABELS, LABELS_V6, LABELS_V8, PROMPTS  # noqa: E402
from context import ContextBuilder  # noqa: E402

GT = ROOT / "data" / "ground_truth.jsonl"
ORIG = ROOT / "data" / "original_hot300.json"
VIDEOS = ROOT / "videos"
PROXIES = ROOT / "proxies"
FRAMES = ROOT / "frames"
OUT = ROOT / "out"
META: dict = {}

KING_IDS = set()
if ORIG.is_file():
    KING_IDS = {r["id"] for r in json.loads(ORIG.read_text(encoding="utf-8"))}

KING_HINTS = ("王者", "共创之夜", "峡谷", "KPL", "kpl", "梦泪")


# v3 新表的游戏代号（ground_truth 里显式标注，不需要启发式路由）
# ground_truth 里显式标注的游戏代号（不需要启发式路由）
# v3：peace/king/valorant/arena/dnfm/tft；v4 新增：eggy（蛋仔派对）/ delta（三角洲行动）
V3_GAMES = ("peace", "king", "valorant", "arena", "dnfm", "tft", "eggy", "delta")


def detect_game(video_id: str, desc: str, row: dict | None = None) -> str:
    # 新数据集在 ground_truth 里显式标注 game，优先使用
    if row and row.get("game") in V3_GAMES:
        return row["game"]
    if video_id in KING_IDS:
        return "king"
    if any(k in (desc or "") for k in KING_HINTS):
        return "king"
    return "peace"


MEDIA_SUFFIXES = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".flv", ".jpg", ".jpeg", ".png", ".webp"}
MIME = {
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
    ".mkv": "video/x-matroska", ".avi": "video/x-msvideo", ".flv": "video/x-flv",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
}
# 超过该体积就走 ffmpeg 代理，避免 base64 请求过大
DIRECT_LIMIT = 6 * 1024 * 1024


def load_gt() -> list[dict]:
    rows = []
    with GT.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def find_media(video_id: str) -> Path | None:
    if not VIDEOS.is_dir():
        return None
    cands = [
        p for p in VIDEOS.glob(f"{video_id}-*")
        if p.is_file() and p.suffix.lower() in MEDIA_SUFFIXES
    ]
    if not cands:
        return None
    # 图集会有多个文件，取第一个；视频只有一个
    return sorted(cands)[0]


def make_proxy(src: Path) -> Path | None:
    """把大视频转成 360p/12fps 的小代理，控制请求体积。"""
    PROXIES.mkdir(parents=True, exist_ok=True)
    dst = PROXIES / f"{src.stem}.mp4"
    if dst.is_file() and dst.stat().st_size > 10_000:
        return dst
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(src),
        "-t", "120",
        "-vf", "scale=-2:360",
        "-r", "12",
        "-c:v", "libx264", "-crf", "32", "-preset", "veryfast",
        "-c:a", "aac", "-b:a", "64k",
        str(dst),
    ]
    try:
        subprocess.run(cmd, check=True, timeout=300, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except Exception as exc:
        print(f"[proxy] 失败 {src.name}: {str(exc)[:120]}")
        return None
    if dst.is_file() and dst.stat().st_size > 10_000:
        return dst
    return None


def pick_media(src: Path) -> Path | None:
    if src.stat().st_size <= DIRECT_LIMIT:
        return src
    return make_proxy(src)


def extract_frames(src: Path, n: int = 8) -> list[Path]:
    """对长视频均匀抽取 n 帧，覆盖全片（代理只含前 120 秒）。"""
    dst = FRAMES / src.stem
    if dst.is_dir():
        cached = sorted(dst.glob("*.jpg"))
        if len(cached) >= n:
            return cached
    dst.mkdir(parents=True, exist_ok=True)
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(src)],
            capture_output=True, text=True, timeout=60,
        ).stdout.strip()
        dur = float(out)
    except Exception:
        return []
    if dur <= 0:
        return []
    step = dur / n
    frames = []
    for i in range(n):
        t = step * i + step / 2
        f = dst / f"{i:02d}.jpg"
        if not (f.is_file() and f.stat().st_size > 1000):
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(src),
                 "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "5", str(f)],
                timeout=120, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            )
        if f.is_file() and f.stat().st_size > 1000:
            frames.append(f)
    return frames


def parse_response(text: str, labels: tuple[str, ...] = LABELS) -> dict:
    text = text.strip()
    # 去掉 markdown 围栏
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(text[start:end + 1])
            label = str(obj.get("内容类型", "")).strip()
            if label in labels:
                return {
                    "label": label,
                    "form": str(obj.get("内容形态", "")).strip(),
                    "edit": str(obj.get("剪辑形态", "")).strip(),
                    "reason": str(obj.get("判断依据", "")).strip(),
                    "confidence": obj.get("置信度", ""),
                    "raw": text,
                    "parse": "ok",
                }
        except json.JSONDecodeError:
            pass
    # 兜底 1：响应被 max_tokens 截断时 JSON 不完整（没有收尾的 }），
    # 但「内容类型」通常已经输出了，用正则直接取。
    # 实测 bili_v11 的「判断依据」较长，截断率约 5%，不处理会白白丢掉这些样本。
    m = re.search(r'"内容类型"\s*:\s*"([^"]+)"', text)
    if m and m.group(1).strip() in labels:
        def _field(name: str) -> str:
            mm = re.search(rf'"{name}"\s*:\s*"([^"]*)"', text)
            return mm.group(1).strip() if mm else ""
        return {
            "label": m.group(1).strip(),
            "form": _field("内容形态"),
            "edit": _field("剪辑形态"),
            "reason": _field("判断依据"),
            "confidence": "",
            "raw": text,
            "parse": "truncated",
        }
    # 兜底 2：直接找标签（长标签优先，避免「创意剪辑」误命中「创意整活」等）
    for label in sorted(labels, key=len, reverse=True):
        if label in text:
            return {"label": label, "reason": "", "confidence": "", "raw": text, "parse": "fallback"}
    return {"label": "", "reason": "", "confidence": "", "raw": text, "parse": "fail"}


LIVE_PROMPT = """\
请只判断这条短视频的画面中是否存在“直播 UI”，包括：在线人数（如 1.2万/3.3万）、聊天框/弹幕、礼物图标、评论输入框、主播头像/ID、直播平台界面/水印。
普通游戏 HUD（血条、地图、击杀提示、队友列表）不算直播 UI。
只输出 JSON：{"live_ui": true 或 false, "evidence": "看到的具体 UI 元素"}"""


def media_data_url(media: Path) -> str:
    suffix = media.suffix.lower()
    mime = MIME.get(suffix, "application/octet-stream")
    b64 = base64.b64encode(media.read_bytes()).decode()
    return f"data:{mime};base64,{b64}"


def call_live_ui(client: httpx.Client, url: str, key: str, model: str, media: Path) -> dict:
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": LIVE_PROMPT},
                {"type": "image_url", "image_url": {"url": media_data_url(media)}},
            ],
        }],
        "max_tokens": 200,
        "temperature": 0,
    }
    try:
        r = client.post(url, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                        json=payload, timeout=300)
        if r.status_code == 200:
            text = r.json()["choices"][0]["message"]["content"]
            if isinstance(text, list):
                text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
            m = re.search(r"\{.*\}", text, re.S)
            if m:
                obj = json.loads(m.group())
                return {"live_ui": bool(obj.get("live_ui")), "evidence": obj.get("evidence", "")}
            low = text.lower()
            return {"live_ui": ("true" in low or "有" in text), "evidence": text[:200]}
        return {"live_ui": False, "evidence": f"HTTP {r.status_code}"}
    except Exception as exc:
        return {"live_ui": False, "evidence": f"{type(exc).__name__}: {str(exc)[:120]}"}


def call_model(client: httpx.Client, url: str, key: str, model: str,
               prompt: str, desc: str, media: Path | None, context: str = "",
               labels: tuple[str, ...] = LABELS,
               frames: list[Path] | None = None,
               temperature: float = 0.1) -> dict:
    text = prompt
    if context:
        text += f"\n\n{context}"
    if desc:
        text += f"\n\n【作品描述（辅助信息）】\n{desc[:600]}"
    if media is None:
        # 纯文本模式（小红书等无法免签下载的平台）
        text += ("\n\n【注意】本条没有视频/图片素材，请仅根据上面的描述、正文、话题与先验信息判断，"
                 "并在「判断依据」里注明是纯文本推测。")
    text += f"\n\n【最后提醒】只输出 JSON，内容类型必须是上面 {len(labels)} 个标签之一。"
    content: list[dict] = [{"type": "text", "text": text}]
    if media is not None:
        suffix = media.suffix.lower()
        mime = MIME.get(suffix, "application/octet-stream")
        b64 = base64.b64encode(media.read_bytes()).decode()
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"}})
    for f in (frames or []):
        content.append({"type": "image_url", "image_url": {"url": media_data_url(f)}})
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 1200,
        "temperature": temperature,
    }
    last_err = ""
    for attempt in range(1, 6):
        try:
            r = client.post(url, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                            json=payload, timeout=300)
            if r.status_code == 200:
                data = r.json()
                if "choices" not in data:
                    # 网关限流/错误时可能返回 200 + error 体
                    raise RuntimeError(f"bad response: {str(data)[:200]}")
                msg = data["choices"][0]["message"]["content"]
                if isinstance(msg, list):
                    msg = "".join(p.get("text", "") for p in msg if isinstance(p, dict))
                return parse_response(msg, labels)
            last_err = f"HTTP {r.status_code}: {r.text[:300]}"
        except Exception as exc:
            last_err = f"{type(exc).__name__}: {str(exc)[:200]}"
        if attempt < 5:
            threading.Event().wait(3 * attempt)
    return {"label": "", "reason": "", "confidence": "", "raw": last_err, "parse": "error"}


def main() -> None:
    global GT, VIDEOS, META
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt-version", default="v1")
    ap.add_argument("--sample-per-label", type=int, default=0,
                    help="每个标签抽样 N 条；0 表示全量")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--ids", default="", help="逗号分隔的作品 ID")
    ap.add_argument("--only-label", default="")
    ap.add_argument("--game", default="auto",
                    help="按游戏过滤，逗号分隔；auto 表示不过滤。"
                         "可选 " + "/".join(V3_GAMES))
    ap.add_argument("--concurrency", "-c", type=int, default=5)
    ap.add_argument("--model", default="gemini-2.5-pro-stb")
    ap.add_argument("--config", default="", help="配置文件路径（含 llmproxy_base_url / llmproxy_api_key）")
    ap.add_argument("--gt", default=str(GT), help="ground_truth jsonl 路径")
    ap.add_argument("--videos", default=str(VIDEOS), help="视频目录")
    ap.add_argument("--out-tag", default="", help="输出子目录名（默认用 prompt-version）")
    ap.add_argument("--prior", default=str(ROOT / "data" / "text_prior.json"),
                    help="文本先验 json 路径")
    ap.add_argument("--corpus", default="", help="检索语料 jsonl（默认与 --gt 相同）")
    ap.add_argument("--media-meta", default=str(ROOT / "data_v2" / "media_meta.json"),
                    help="ffprobe 元数据 json（时长/分辨率）")
    ap.add_argument("--reasons", default=str(ROOT / "data_v2" / "reasons.json"),
                    help="训练语料正确预测的判断依据 json（用于相似示例）")
    ap.add_argument("--frames", type=int, default=0,
                    help="长视频（>120s）全片均匀抽帧数，0 表示关闭")
    ap.add_argument("--temperature", type=float, default=0.1)
    ap.add_argument("--similar-k", type=int, default=12,
                    help="检索到的相似作品示例数")
    ap.add_argument("--no-context", action="store_true")
    ap.add_argument("--verify-live", action="store_true",
                    help="对预测为实况解说/游戏实况/创意整活/活动剪辑的结果做直播 UI 复核")
    ap.add_argument("--verify-model", default="gemini-2.5-flash-stb")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--allow-no-media", action="store_true",
                    help="未下载到素材的样本也送模型（纯文本），用于小红书等平台")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    if args.prompt_version not in PROMPTS:
        raise SystemExit(f"未知提示词版本: {args.prompt_version}")
    prompt = PROMPTS[args.prompt_version]
    # 标签集按提示词版本路由。注意这里用「版本号前缀匹配」而不是 endswith：
    # bili_v11 这类带平台前缀的版本名不以 _v8 结尾，早期用 endswith 会静默退回
    # 16 类 LABELS，导致「游戏测评/皮肤展示/游戏吐槽」等被判为非法标签而整条失败。
    # 用正则取版本号，避免每加一个版本就要改枚举（v12 就是这么漏掉的：
    # 枚举里只写到 _v11，bili_v12 静默退回 16 类 LABELS，34 条整条失败）
    _v = args.prompt_version
    _m = re.search(r"_v(\d+)$", _v)
    _n = int(_m.group(1)) if _m else 0
    if _n >= 8:
        labels = LABELS_V8
    elif _n >= 6:
        labels = LABELS_V6
    else:
        labels = LABELS
    print(f"提示词 {_v}（版本号 {_n or '无'}）-> 标签集 {len(labels)} 类")

    cfg_path = None
    if args.config:
        cfg_path = Path(args.config)
    elif os.getenv("CONTENT_EVAL_CONFIG"):
        cfg_path = Path(os.getenv("CONTENT_EVAL_CONFIG"))
    else:
        for cand in (ROOT / "config.json", ROOT.parent / "config.json", ROOT.parent / "batch_config.json"):
            if cand.is_file():
                cfg_path = cand
                break
    if not cfg_path or not cfg_path.is_file():
        raise SystemExit("未找到配置文件：请用 --config 指定，或放置 content_eval/config.json")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    url = (cfg.get("llmproxy_base_url") or os.getenv("LLM_URL")
           or "http://api.timiai.woa.com/ai_api_manage/llmproxy/chat/completions")
    key = cfg.get("llmproxy_api_key") or os.getenv("LLM_KEY", "")
    if not key:
        raise SystemExit("未配置 llmproxy_api_key")

    GT = Path(args.gt)
    VIDEOS = Path(args.videos)
    rows = load_gt()
    corpus_rows = rows
    if args.corpus:
        corpus_rows = [json.loads(l) for l in Path(args.corpus).read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.media_meta and Path(args.media_meta).is_file():
        META = json.loads(Path(args.media_meta).read_text(encoding="utf-8"))
    builder = None if args.no_context else ContextBuilder(
        corpus_rows, args.prior, args.media_meta, args.reasons, args.similar_k)
    id_filter = {i.strip() for i in args.ids.split(",") if i.strip()}
    if id_filter:
        rows = [r for r in rows if r["id"] in id_filter]
    if args.only_label:
        rows = [r for r in rows if r["label"] == args.only_label]
    if args.game != "auto":
        want_games = {g.strip() for g in args.game.split(",") if g.strip()}
        rows = [r for r in rows if detect_game(r["id"], r["desc"], r) in want_games]

    # 只保留已下载的（--allow-no-media 时保留全部，缺素材的走纯文本）
    prepared = []
    missing = 0
    for r in rows:
        src = find_media(r["id"])
        if not src:
            missing += 1
            if not args.allow_no_media:
                continue
        prepared.append((r, src))
    mode = "（缺素材者走纯文本）" if args.allow_no_media else ""
    print(f"真值 {len(rows)} 条，已下载 {len(rows) - missing} 条，未下载 {missing} 条{mode}")

    # 分层抽样
    if args.sample_per_label > 0:
        rng = random.Random(args.seed)
        by = defaultdict(list)
        for r, src in prepared:
            by[r["label"]].append((r, src))
        picked = []
        for label, items in by.items():
            items = sorted(items, key=lambda x: x[0]["id"])
            rng.shuffle(items)
            picked.extend(items[:args.sample_per_label])
        prepared = picked
        print(f"分层抽样后 {len(prepared)} 条")
    if args.limit > 0:
        prepared = prepared[:args.limit]

    out_dir = OUT / (args.out_tag or (args.prompt_version + ("_nc" if args.no_context else "")))
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = out_dir / "items"
    cache_dir.mkdir(exist_ok=True)

    todo = []
    for r, src in prepared:
        cache = cache_dir / f"{r['id']}.json"
        if cache.is_file() and not args.force:
            try:
                obj = json.loads(cache.read_text(encoding="utf-8"))
                if obj.get("label"):
                    continue
            except Exception:
                pass
        todo.append((r, src))
    print(f"待分析 {len(todo)} 条（已有缓存 {len(prepared) - len(todo)} 条）")

    lock = threading.Lock()
    done = 0
    errors = 0
    started = monotonic()

    def work(item):
        r, src = item
        media = pick_media(src) if src is not None else None
        if media is None and src is not None:
            return r, {"label": "", "reason": "", "confidence": "", "raw": "no media/proxy", "parse": "error"}
        ctx = builder.build(r) if builder else ""
        dur = (META.get(r["id"]) or {}).get("duration") or 0
        frames = extract_frames(src, args.frames) if (src is not None and args.frames > 0 and dur > 120) else []
        # generic_v8 的提示词按条注入游戏名
        row_prompt = prompt.replace("__GAME__", r.get("game_title") or "该游戏")
        with httpx.Client() as client:
            res = call_model(client, url, key, args.model, row_prompt, r["desc"], media, ctx,
                             labels, frames, args.temperature)
            if args.verify_live and res.get("label") in {"实况解说", "游戏实况", "创意整活", "活动剪辑"}:
                live = call_live_ui(client, url, key, args.verify_model, media)
                res["live_ui"] = live["live_ui"]
                res["live_evidence"] = live["evidence"]
                if live["live_ui"]:
                    res["label_before_live"] = res["label"]
                    res["label"] = "直播切片"
                    res["reason"] = (res.get("reason", "") + " | 直播 UI 复核：" + live["evidence"])[:500]
        res.update({"id": r["id"], "gt": r["label"], "media": str(media) if media else "",
                    "desc": r["desc"], "model": args.model,
                    "prompt_version": args.prompt_version, "context": ctx[:3000]})
        with lock:
            (cache_dir / f"{r['id']}.json").write_text(
                json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        return r, res

    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        futures = {pool.submit(work, item): item for item in todo}
        for fut in as_completed(futures):
            r, res = fut.result()
            done += 1
            if not res.get("label"):
                errors += 1
            mark = "OK " if res.get("label") == r["label"] else "ERR"
            print(f"[{done}/{len(todo)}] {mark} gt={r['label']} pred={res.get('label') or '-'} "
                  f"{r['id']} {str(res.get('reason',''))[:60]}")
    elapsed = int(monotonic() - started)
    print(f"完成 {done} 条，失败 {errors} 条，用时 {elapsed}s -> {cache_dir}")

    # 汇总本版本预测
    preds = []
    for p in sorted(cache_dir.glob("*.json")):
        try:
            preds.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            pass
    pred_path = out_dir / "predictions.jsonl"
    with pred_path.open("w", encoding="utf-8") as f:
        for obj in preds:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    print(f"汇总 {len(preds)} 条 -> {pred_path}")


if __name__ == "__main__":
    main()
