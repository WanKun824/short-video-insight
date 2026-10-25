#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""批量分析已下载的视频，输出结构化标签 CSV / Excel。

与 ``download_videos.py`` 配套：先把作品下载到本地，再用本脚本调用多模态
大模型逐个分析，产出每个视频的六维标签（内容主题分类、内容节奏、
是否有 3 秒钩子、3 秒钩子类型、核心叙事框架、内容视角）。

特性：

* **断点续传**：每个视频单独写一份 ``<视频ID>.csv``，重跑时自动跳过已有结果；
* **并发控制**：``--concurrent`` 控制同时在飞的分析请求数（默认 20）；
* **文件名解析**：自动识别抖音 / 快手 / B 站 / 通用四种命名格式，
  还原视频 ID、标题、发布时间与作品链接；
* **汇总输出**：目录分析完成后生成 ``_汇总_<目录名>.csv`` 和同名 ``.xlsx``。

配置（``--config`` 指向的 JSON，默认 ``batch_config.json``，
可从仓库里的 ``config.example.json`` 复制）::

    {
      "model_provider": "qwen",            // qwen 或 gemini
      "qwen_api_key": "<YOUR_API_KEY>",    // 也可用环境变量 DASHSCOPE_API_KEY
      "gemini_api_key": "<YOUR_API_KEY>",  // gemini 后端用，或环境变量 GEMINI_API_KEY
      "gemini_api_base": "",               // 自建/第三方网关地址，留空走官方
      "model_name": "",                    // 留空走默认模型
      "max_concurrent_analysis": 20
    }

**切勿把真实 API Key 提交到版本库**，推荐只用环境变量传入。

用法示例::

    python analyze_videos.py --dir downloads/douyin
    python analyze_videos.py --dir downloads/douyin --dir downloads/kuaishou
    python analyze_videos.py --dir downloads --output result --concurrent 10
    python analyze_videos.py --dir downloads --provider qwen

注意：送多模态模型分析的视频建议用 ``download_videos.py --codec h264`` 下载
（默认 ``--quality lowest`` 已经是小体积档位，上传更快），
HEVC/H.265 编码的视频部分模型无法解码。
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from video_analyzer import VideoAnalyzer  # noqa: E402

VIDEO_EXTENSIONS = (".mp4", ".webm", ".avi", ".mov", ".mkv", ".flv")
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
FIELDNAMES = [
    "视频ID",
    "内容类型",
    "视频标题",
    "发布时间",
    "视频链接",
    "内容主题分类",
    "内容节奏",
    "是否有3秒钩子",
    "3秒钩子类型",
    "核心叙事框架",
    "内容视角",
    "原始分析",
    "原始文件名",
]
COLUMN_WIDTHS = {
    "视频ID": 22,
    "内容类型": 10,
    "视频标题": 40,
    "发布时间": 18,
    "视频链接": 45,
    "内容主题分类": 18,
    "内容节奏": 10,
    "是否有3秒钩子": 14,
    "3秒钩子类型": 14,
    "核心叙事框架": 14,
    "内容视角": 12,
    "原始分析": 60,
    "原始文件名": 45,
}


def safe_print(text: str) -> None:
    """兼容 Windows GBK 终端的打印。"""
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        print(text.encode("gbk", errors="ignore").decode("gbk"), flush=True)


def safe_name(value: str) -> str:
    """把字符串转成合法的 Windows 文件名。"""
    return re.sub(r'[<>:"/\\|?*]', "_", value or "")


def extract_title(filename: str) -> dict:
    """从文件名解析作品信息，自动识别五种命名格式。

    1. 本项目 ``platform_download.py`` 的抖音命名：``<19位ID>-<标题>.mp4``
       （图集多图时再带 ``-01``/``-02`` 序号后缀）；
    2. TikTokDownloader 主程序命名：``<19位ID>-----<标题>-----<发布时间>.mp4``；
       图集文件名前缀含 ``【图集】``，并带 ``_1``/``_2`` 序号后缀；
    3. B 站：``<标题>_BVxxxxxxxxxx.mp4``；
    4. 快手：``<标题>_<15~20位字母数字ID>.mp4``
       （本项目快手下载默认直接以 ``<photoId>.mp4`` 命名，也能命中这条规则）；
    5. 通用：无法识别时用去掉话题标签的文件名作标题，ID 用哈希占位。
    """
    name = Path(filename).stem
    name = re.sub(r"_\d+$", "", name)  # 去掉 TikTokDownloader 图集序号后缀

    parts = name.split("-----")
    if len(parts) >= 2:
        raw_id = parts[0].strip()
        title = parts[1].strip()
        publish_time = parts[2].strip() if len(parts) >= 3 else ""
        matched = re.search(r"(\d{19})", raw_id)
        video_id = matched.group(1) if matched else raw_id
        return {
            "视频ID": video_id,
            "内容类型": "图集" if "【图集】" in raw_id else "视频",
            "视频标题": title,
            "发布时间": publish_time,
            "视频链接": (
                f"https://www.douyin.com/video/{video_id}" if matched else ""
            ),
            "原始文件名": filename,
        }

    # 本项目默认命名：<19位ID>-<标题>[-<两位图集序号>]
    native = re.match(r"^(\d{19})(?:-(.*?))?(?:-(\d{2}))?$", name)
    if native:
        return {
            "视频ID": native.group(1),
            "内容类型": "图集" if native.group(3) else "视频",
            "视频标题": (native.group(2) or "").strip(),
            "发布时间": "",
            "视频链接": f"https://www.douyin.com/video/{native.group(1)}",
            "原始文件名": filename,
        }

    bv = re.search(r"^(.+)_(BV[a-zA-Z0-9]{10,12})$", name)
    if bv:
        return {
            "视频ID": bv.group(2),
            "内容类型": "视频",
            "视频标题": bv.group(1).strip(),
            "发布时间": "",
            "视频链接": f"https://www.bilibili.com/video/{bv.group(2)}",
            "原始文件名": filename,
        }

    ks = re.search(r"^(?:(.+)_)?([a-z0-9]{15,20})$", name, re.IGNORECASE)
    if ks:
        title = (ks.group(1) or "").strip()
        video_id = ks.group(2)
        return {
            "视频ID": video_id,
            "内容类型": "视频",
            "视频标题": title.split("_")[0] if title else video_id,
            "发布时间": "",
            "视频链接": f"https://www.kuaishou.com/short-video/{video_id}",
            "原始文件名": filename,
        }

    return {
        "视频ID": f"video_{abs(hash(name)) % 10**10:010d}",
        "内容类型": "视频",
        "视频标题": re.sub(r"#\S+\s*", "", name).strip() or name,
        "发布时间": "",
        "视频链接": "",
        "原始文件名": filename,
    }


def collect_media(video_dir: Path, include_images: bool) -> list[Path]:
    """列出目录下待分析的媒体文件。"""
    suffixes = VIDEO_EXTENSIONS + (IMAGE_EXTENSIONS if include_images else ())
    return sorted(
        item
        for item in video_dir.iterdir()
        if item.is_file() and item.suffix.lower() in suffixes
    )


async def analyze_folder(
    video_dir: Path,
    output_dir: Path,
    analyzer: VideoAnalyzer,
    max_concurrent: int = 20,
    include_images: bool = False,
    limit: int = 0,
) -> tuple[int, int]:
    """分析单个目录，返回 ``(成功数, 失败数)``。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    folder = video_dir.name
    media_files = collect_media(video_dir, include_images)
    if not media_files:
        safe_print(f"[{folder}] 未发现可分析的媒体文件")
        return 0, 0

    existing = {
        item.stem for item in output_dir.glob("*.csv") if not item.name.startswith("_")
    }
    pending = [
        item
        for item in media_files
        if safe_name(extract_title(item.name)["视频ID"]) not in existing
    ]
    if limit:
        pending = pending[:limit]

    if not pending:
        safe_print(f"[{folder}] 全部 {len(media_files)} 个文件已分析，跳过")
        return len(media_files), 0

    safe_print(
        f"\n[{folder}] 共 {len(media_files)} 个文件，待分析 {len(pending)} 个"
        f"（已跳过 {len(media_files) - len(pending)} 个）"
    )

    semaphore = asyncio.Semaphore(max(1, max_concurrent))
    success = 0
    failed = 0
    total = len(pending)

    async def worker(path: Path, index: int) -> None:
        nonlocal success, failed
        async with semaphore:
            safe_print(f"[{folder}] [{index}/{total}] {path.name[:40]} ...")
            try:
                info = extract_title(path.name)
                result = {**info, **await analyzer.analyze(str(path))}
                raw = str(result.get("原始分析") or "")
                if raw.startswith("Error:"):
                    failed += 1
                    safe_print(f"[{folder}] [{index}/{total}] [FAIL] {raw[:70]}")
                    return
                target = output_dir / f"{safe_name(result.get('视频ID', 'unknown'))}.csv"
                with target.open("w", newline="", encoding="utf-8-sig") as handle:
                    writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
                    writer.writeheader()
                    writer.writerow({k: result.get(k, "") for k in FIELDNAMES})
                success += 1
                safe_print(
                    f"[{folder}] [{index}/{total}] [OK] {target.stem[:25]} "
                    f"{result.get('内容主题分类', '')}"
                )
            except Exception as exc:  # noqa: BLE001
                failed += 1
                safe_print(f"[{folder}] [{index}/{total}] [FAIL] {str(exc)[:70]}")

    await asyncio.gather(
        *(asyncio.create_task(worker(path, i)) for i, path in enumerate(pending, 1))
    )
    return success, failed


def generate_summary(output_dir: Path, folder_name: str) -> int:
    """把目录内所有单条结果汇总成 CSV + Excel，返回记录数。"""
    files = [
        item for item in output_dir.glob("*.csv") if not item.name.startswith("_")
    ]
    if not files:
        return 0

    rows: list[dict] = []
    for item in files:
        try:
            with item.open("r", encoding="utf-8-sig") as handle:
                rows.extend(dict(row) for row in csv.DictReader(handle))
        except OSError:
            continue
    if not rows:
        return 0
    rows.sort(key=lambda x: x.get("视频ID", ""))

    csv_path = output_dir / f"_汇总_{folder_name}.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in FIELDNAMES})

    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter

        book = Workbook()
        sheet = book.active
        sheet.title = folder_name[:31] or "结果"
        fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        font = Font(bold=True, color="FFFFFF", size=11)
        for col, field in enumerate(FIELDNAMES, 1):
            cell = sheet.cell(row=1, column=col, value=field)
            cell.fill = fill
            cell.font = font
            cell.alignment = Alignment(horizontal="center", vertical="center")
            sheet.column_dimensions[get_column_letter(col)].width = COLUMN_WIDTHS.get(
                field, 15
            )
        for row_index, row in enumerate(rows, 2):
            for col, field in enumerate(FIELDNAMES, 1):
                cell = sheet.cell(row=row_index, column=col, value=row.get(field, ""))
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        sheet.freeze_panes = "A2"
        excel_path = output_dir / f"_汇总_{folder_name}.xlsx"
        book.save(excel_path)
        safe_print(f"  Excel: {excel_path}")
    except ImportError:
        safe_print("  [WARN] 未安装 openpyxl，跳过 Excel 生成（pip install openpyxl）")

    safe_print(f"\n[{folder_name}] 汇总完成，共 {len(rows)} 条记录")
    safe_print(f"  CSV: {csv_path}")

    counter: dict[str, int] = {}
    for row in rows:
        key = row.get("内容主题分类") or ""
        if key:
            counter[key] = counter.get(key, 0) + 1
    if counter:
        safe_print(f"\n[{folder_name}] 内容主题分类 Top 5:")
        for key, count in sorted(counter.items(), key=lambda x: -x[1])[:5]:
            safe_print(f"    - {key}: {count}")
    return len(rows)


def load_config(path: Path) -> dict:
    """读取配置文件，不存在或解析失败时返回空配置。"""
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except (OSError, json.JSONDecodeError):
        safe_print(f"[WARN] 配置文件解析失败: {path}")
        return {}


def resolve_api_key(provider: str, config: dict, explicit: Optional[str]) -> str:
    """按 命令行 > 配置文件 > 环境变量 的优先级取 API Key。"""
    if explicit:
        return explicit
    if provider == "qwen":
        return config.get("qwen_api_key") or os.getenv("DASHSCOPE_API_KEY", "")
    return (
        config.get("gemini_api_key")
        or os.getenv("GEMINI_API_KEY", "")
        or os.getenv("GEMINI_API_BASE_KEY", "")
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="批量分析已下载视频，输出六维内容标签 CSV/Excel",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--dir", action="append", required=True, help="视频目录，可重复指定"
    )
    parser.add_argument(
        "--output",
        default=None,
        help="结果输出目录，默认 <视频目录的父目录>/result/<目录名>_result",
    )
    parser.add_argument("--config", default="batch_config.json", help="配置文件路径")
    parser.add_argument(
        "--provider", choices=("gemini", "qwen"), default=None, help="模型后端，覆盖配置文件"
    )
    parser.add_argument("--model", default=None, help="模型名，覆盖配置文件")
    parser.add_argument("--api-key", default=None, help="API Key，覆盖配置文件与环境变量")
    parser.add_argument("--concurrent", type=int, default=None, help="最大并发分析数")
    parser.add_argument(
        "--limit", type=int, default=0, help="每个目录仅分析前 N 个文件（0 表示全部）"
    )
    parser.add_argument(
        "--include-images", action="store_true", help="同时分析图集图片（jpg/png/webp）"
    )
    parser.add_argument(
        "--summary-only", action="store_true", help="跳过分析，只重新生成汇总文件"
    )
    return parser


async def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    config = load_config(Path(args.config).expanduser())
    provider = (args.provider or config.get("model_provider") or "gemini").lower()
    model = args.model or config.get("model_name") or ""
    concurrent = args.concurrent or int(config.get("max_concurrent_analysis") or 20)

    targets: list[tuple[Path, Path]] = []
    for item in args.dir:
        video_dir = Path(item).expanduser()
        if not video_dir.is_dir():
            safe_print(f"[WARN] 目录不存在: {video_dir}")
            continue
        output_dir = (
            Path(args.output).expanduser()
            if args.output
            else video_dir.parent / "result" / f"{video_dir.name}_result"
        )
        targets.append((video_dir, output_dir))
    if not targets:
        safe_print("[ERROR] 没有有效的视频目录")
        return 1

    if args.summary_only:
        for video_dir, output_dir in targets:
            generate_summary(output_dir, video_dir.name)
        return 0

    api_key = resolve_api_key(provider, config, args.api_key)
    if not api_key:
        hint = (
            "qwen_api_key / 环境变量 DASHSCOPE_API_KEY"
            if provider == "qwen"
            else "gemini_api_key / 环境变量 GEMINI_API_KEY"
        )
        safe_print(f"[ERROR] 未找到 API Key，请在 {args.config} 中配置 {hint}，或用 --api-key 传入")
        return 1

    analyzer = VideoAnalyzer(
        api_key=api_key,
        model=model,
        model_provider=provider,
        api_base=config.get("gemini_api_base") or "",
    )
    safe_print("=" * 70)
    safe_print(f"[CONFIG] 模型后端: {provider}  模型: {analyzer.model}  并发: {concurrent}")
    total_files = 0
    for video_dir, output_dir in targets:
        count = len(collect_media(video_dir, args.include_images))
        total_files += count
        safe_print(f"  - {video_dir} ({count} 个文件) -> {output_dir}")
    safe_print(f"[INFO] 待处理文件总数: {total_files}")
    safe_print("=" * 70)

    started = datetime.now()
    total_success = 0
    total_failed = 0
    for video_dir, output_dir in targets:
        safe_print(f"\n{'=' * 70}\n[START] {video_dir.name}\n{'=' * 70}")
        success, failed = await analyze_folder(
            video_dir,
            output_dir,
            analyzer,
            concurrent,
            args.include_images,
            args.limit,
        )
        total_success += success
        total_failed += failed
        if success or any(output_dir.glob("*.csv")):
            generate_summary(output_dir, video_dir.name)

    elapsed = (datetime.now() - started).total_seconds()
    safe_print("\n" + "=" * 70)
    safe_print(f"[DONE] 成功 {total_success} 个，失败 {total_failed} 个")
    safe_print(f"[TIME] {elapsed:.1f} 秒（{elapsed / 60:.1f} 分钟）")
    safe_print("=" * 70)
    return 1 if total_failed and not total_success else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
