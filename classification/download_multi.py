# -*- coding: utf-8 -*-
"""快手 / B站 公开视频下载器。

用法：
    python content_eval/download_multi.py --items content_eval/data_v2/ground_truth.jsonl \
        --output content_eval/videos_v2 --concurrency 10

只处理 platform 为 快手 / B站 的条目；抖音仍用 download_all.py。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from time import monotonic

import httpx

UA_PC = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
UA_M = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")

KS_MP4_RE = re.compile(r'"mainMvUrls"\s*:\s*\[\s*\{[^\]]*?"url"\s*:\s*"([^"]+)"', re.S)
BV_RE = re.compile(r"(BV[0-9A-Za-z]+)")


def safe_name(s: str, n: int = 40) -> str:
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", s or "").strip()
    return s[:n].strip()


KS_PAGE_TPLS = (
    "https://v.m.chenzhongtech.com/fw/photo/{pid}",
    "https://www.kuaishou.com/short-video/{pid}",
    "https://m.gifshow.com/fw/photo/{pid}",
)
KS_ID_RE = re.compile(r"(?:short-video|fw/photo)/([A-Za-z0-9_-]{6,})")


def resolve_kuaishou(client: httpx.Client, link: str) -> str | None:
    """快手移动端页面内嵌 INIT_STATE；优先取 mainMvUrls 的 mp4 直链。

    注意：manifest 里可能给出 m3u8 切片，直接下载会得到几十 KB 的播放列表，
    因此这里只接受 .mp4 地址。
    """
    m = KS_ID_RE.search(link or "")
    if not m:
        return None
    pid = m.group(1)
    for tpl in KS_PAGE_TPLS:
        try:
            r = client.get(tpl.format(pid=pid), headers={"User-Agent": UA_M},
                           follow_redirects=True, timeout=30)
        except Exception:
            continue
        if r.status_code != 200:
            continue
        found = KS_MP4_RE.search(r.text)
        if found:
            url = found.group(1).replace("\\u002F", "/")
            if ".mp4" in url:
                return url
        # 退而求其次：页面里任意 kwimgs/kwaicdn 的 mp4 直链
        alt = re.search(r'https://[^"\\\s]+?(?:kwimgs|kwaicdn)\.com/[^"\\\s]+?\.mp4[^"\\\s]*', r.text)
        if alt:
            return alt.group(0)
    return None


def resolve_bilibili(client: httpx.Client, link: str) -> tuple[str, dict] | None:
    m = BV_RE.search(link)
    if not m:
        return None
    bv = m.group(1)
    h = {"User-Agent": UA_PC, "Referer": "https://www.bilibili.com/"}
    j = client.get("https://api.bilibili.com/x/web-interface/view",
                   params={"bvid": bv}, headers=h, timeout=30).json()
    if j.get("code") != 0:
        return None
    data = j.get("data") or {}
    cid = data.get("cid")
    if not cid:
        return None
    j2 = client.get("https://api.bilibili.com/x/player/playurl",
                    params={"bvid": bv, "cid": cid, "qn": 16, "fnval": 1, "platform": "html5"},
                    headers=h, timeout=30).json()
    durl = (j2.get("data") or {}).get("durl") or []
    if not durl:
        return None
    return durl[0]["url"], h


def download(client: httpx.Client, url: str, dst: Path, headers: dict) -> bool:
    tmp = dst.with_suffix(dst.suffix + ".part")
    try:
        with client.stream("GET", url, headers=headers, timeout=300) as r:
            if r.status_code != 200:
                return False
            size = 0
            with open(tmp, "wb") as f:
                for chunk in r.iter_bytes(1 << 16):
                    f.write(chunk)
                    size += len(chunk)
        if size < 10_000:
            tmp.unlink(missing_ok=True)
            return False
        tmp.replace(dst)
        return True
    except Exception:
        tmp.unlink(missing_ok=True)
        return False


def existing_ids(output: Path) -> set[str]:
    ids = set()
    if output.is_dir():
        for p in output.iterdir():
            if p.is_file() and not p.name.endswith(".part"):
                ids.add(p.name.split("-", 1)[0])
    return ids


def main() -> None:
    # Windows 控制台默认 GBK，作品描述含 emoji 时会 UnicodeEncodeError 中断主进程
    try:
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--items", required=True, help="含 platform/id/link/desc 的 jsonl")
    ap.add_argument("--output", required=True)
    ap.add_argument("--concurrency", "-c", type=int, default=10)
    ap.add_argument("--platforms", default="快手,B站")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    want = {p.strip() for p in args.platforms.split(",") if p.strip()}
    rows = [json.loads(l) for l in Path(args.items).read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = [r for r in rows if r.get("platform") in want]
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    have = existing_ids(output)
    todo = [r for r in rows if r["id"] not in have]
    already = len(rows) - len(todo)
    if args.limit:
        todo = todo[:args.limit]
    print(f"待下载 {len(todo)} 条（已存在 {already} 条）-> {output}")

    lock = threading.Lock()
    done = ok = fail = 0
    fails = []
    started = monotonic()

    def work(row: dict) -> tuple[dict, bool, str]:
        with httpx.Client(follow_redirects=True) as client:
            try:
                if row["platform"] == "快手":
                    url = resolve_kuaishou(client, row["link"])
                    headers = {"User-Agent": UA_M}
                else:
                    res = resolve_bilibili(client, row["link"])
                    url, headers = res if res else (None, {})
                if not url:
                    return row, False, "no url"
                dst = output / f"{row['id']}-{safe_name(row.get('desc', ''), 40)}.mp4"
                if download(client, url, dst, headers):
                    return row, True, str(dst)
                return row, False, "download failed"
            except Exception as exc:
                return row, False, f"{type(exc).__name__}: {str(exc)[:120]}"

    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        futures = {pool.submit(work, r): r for r in todo}
        for fut in as_completed(futures):
            row, success, msg = fut.result()
            with lock:
                done += 1
                if success:
                    ok += 1
                else:
                    fail += 1
                    fails.append((row["id"], row["platform"], msg))
                print(f"[{done}/{len(todo)}] {'OK ' if success else 'ERR'} {row['platform']} {row['id']} {msg[:80]}")

    elapsed = int(monotonic() - started)
    print(f"完成：成功 {ok}，失败 {fail}，用时 {elapsed}s")
    if fails:
        print("失败清单：")
        for vid, plat, msg in fails[:50]:
            print(f"  {plat} {vid} {msg}")


if __name__ == "__main__":
    main()
