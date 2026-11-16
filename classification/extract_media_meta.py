# -*- coding: utf-8 -*-
"""用 ffprobe 提取本地视频的时长、分辨率、帧率，供分类上下文使用。"""
from __future__ import annotations

import argparse
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def probe(path: Path) -> dict | None:
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,avg_frame_rate",
        "-show_entries", "format=duration",
        "-of", "json", str(path),
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=60, check=True).stdout
        obj = json.loads(out)
    except Exception:
        return None
    streams = obj.get("streams") or [{}]
    s = streams[0]
    dur = obj.get("format", {}).get("duration")
    try:
        duration = float(dur) if dur else 0.0
    except ValueError:
        duration = 0.0
    return {
        "duration": round(duration, 1),
        "width": int(s.get("width") or 0),
        "height": int(s.get("height") or 0),
        "fps": s.get("avg_frame_rate") or "",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--videos", default=str(ROOT / "videos_v2"))
    ap.add_argument("--out", default=str(ROOT / "data_v2" / "media_meta.json"))
    ap.add_argument("--concurrency", "-c", type=int, default=8)
    args = ap.parse_args()

    videos = Path(args.videos)
    files = [p for p in videos.iterdir() if p.is_file() and p.suffix.lower() in
             {".mp4", ".mov", ".webm", ".mkv", ".avi", ".flv"}]
    print(f"待提取 {len(files)} 个视频")

    out = {}
    done = 0

    def work(p: Path):
        meta = probe(p)
        return p.name.split("-", 1)[0], meta

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {pool.submit(work, p): p for p in files}
        for fut in as_completed(futures):
            vid, meta = fut.result()
            done += 1
            if meta:
                out[vid] = meta
            if done % 100 == 0:
                print(f"  {done}/{len(files)}")

    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"写入 {len(out)} 条 -> {args.out}")


if __name__ == "__main__":
    main()
