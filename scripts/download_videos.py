#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""多平台批量下载抖音 / TikTok / 快手作品文件（编排入口）。

并发策略：``--concurrency`` 表示**总并发**，按实际出现链接的平台数均分。

* 只有抖音 → 抖音 20
* 抖音 + TikTok → 抖音 10 + TikTok 10
* 抖音 + TikTok + 快手 → 抖音 7 + TikTok 7 + 快手 6（余数补给靠前的平台）

每个平台由独立的 ``platform_download.py`` 子进程负责，因此各平台的并发
限制互不干扰（``Downloader.semaphore`` 是类属性，同进程内无法隔离）。

依赖说明：

* 抖音 ``share`` 模式（默认）与快手走脚本内置实现，**零依赖、无需 cookie**，
  可直接在任意目录运行；
* 抖音 ``api`` 模式与 TikTok 需要配合
  `TikTokDownloader <https://github.com/JoeanAmier/TikTokDownloader>`_ 仓库，
  用 ``--project-root`` 指定仓库目录，或设置环境变量
  ``TIKTOK_DOWNLOADER_ROOT``。

清晰度：``--quality`` 默认 ``lowest``（最低清晰度，省流量、省存储、
送多模态模型分析时上传更快）；需要原画时传 ``--quality highest``。

用法示例::

    python download_videos.py --urls 作品链接清单.md -o downloads
    python download_videos.py --link https://www.douyin.com/video/<作品ID> -o downloads
    python download_videos.py --urls urls.txt -o downloads -c 20 --limit 20
    python download_videos.py --urls urls.txt -o downloads --quality highest
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional, Sequence

DEFAULT_CONCURRENCY = 20
DEFAULT_PLATFORM = "douyin"
PLATFORMS = ("douyin", "tiktok", "kuaishou")
PLATFORM_LABELS = {"douyin": "抖音", "tiktok": "TikTok", "kuaishou": "快手"}
PLATFORM_PATTERNS = {
    "douyin": re.compile(r"(?:douyin\.com|iesdouyin\.com)", re.IGNORECASE),
    "tiktok": re.compile(r"tiktok\.com", re.IGNORECASE),
    "kuaishou": re.compile(
        r"(?:kuaishou\.com|chenzhongtech\.com|kuaishou\.app|gifshow\.com)",
        re.IGNORECASE,
    ),
}
DOWNLOAD_ENTRY = "download_only.py"
SOURCE_DIR = "src"
PLATFORM_SCRIPT = "platform_download.py"
ENV_PROJECT_ROOT = "TIKTOK_DOWNLOADER_ROOT"
TAG = "[runner]"


def is_project_root(path: Path) -> bool:
    """判断目录是否为 TikTokDownloader 仓库根目录。"""
    return (path / DOWNLOAD_ENTRY).is_file() and (path / SOURCE_DIR).is_dir()


def iter_candidate_roots(explicit: Optional[str]) -> list[Path]:
    """按优先级生成候选仓库根目录。"""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    if env := os.getenv(ENV_PROJECT_ROOT):
        candidates.append(Path(env).expanduser())
    candidates.append(Path.cwd())
    candidates.append(Path(__file__).resolve().parent)
    return candidates


def locate_project_root(explicit: Optional[str]) -> Path:
    """定位 TikTokDownloader 仓库根目录，逐级向上回溯。"""
    seen: set[Path] = set()
    for candidate in iter_candidate_roots(explicit):
        try:
            base = candidate.resolve()
        except OSError:
            continue
        for directory in (base, *base.parents):
            if directory in seen:
                continue
            seen.add(directory)
            if is_project_root(directory):
                return directory
    raise SystemExit(
        "未找到 TikTokDownloader 仓库根目录（抖音 api 模式 / TikTok 需要），"
        f"请使用 --project-root 指定，或设置环境变量 {ENV_PROJECT_ROOT}。"
    )


def read_lines(urls_file: Path) -> list[str]:
    """读取链接来源文件中的非空行。"""
    if not urls_file.is_file():
        raise SystemExit(f"链接文件不存在: {urls_file}")
    return [
        line.strip()
        for line in urls_file.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]


def detect_platforms(line: str) -> list[str]:
    """判断一行文本涉及哪些平台。"""
    return [name for name in PLATFORMS if PLATFORM_PATTERNS[name].search(line)]


def group_lines(
    lines: Sequence[str], default_platform: str, allow: set[str]
) -> dict[str, list[str]]:
    """按平台对链接行分组，无法识别的行归入 default_platform。"""
    grouped: dict[str, list[str]] = {}
    for line in lines:
        targets = [i for i in detect_platforms(line) if i in allow]
        if not targets:
            if default_platform not in allow:
                continue
            targets = [default_platform]
        for name in targets:
            grouped.setdefault(name, []).append(line)
    return grouped


def split_concurrency(total: int, parts: int) -> list[int]:
    """把总并发均分到各平台，余数依次补给前面的平台。"""
    total = max(1, total)
    parts = max(1, parts)
    base, extra = divmod(total, parts)
    return [base + (1 if index < extra else 0) for index in range(parts)] or [total]


def write_links_file(platform: str, lines: Sequence[str]) -> Path:
    """为单个平台写入临时链接文件。"""
    handle, path = tempfile.mkstemp(prefix=f"{platform}_links_", suffix=".txt")
    with os.fdopen(handle, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return Path(path)


def build_command(
    args: argparse.Namespace,
    script: Path,
    platform: str,
    urls: Path,
    output: Path,
    concurrency: int,
) -> list[str]:
    """构造 platform_download.py 的命令行参数。"""
    command = [
        sys.executable,
        str(script),
        "--platform",
        platform,
        "--config",
        args.config,
        "--urls",
        str(urls),
        "--output",
        str(output),
        "--concurrency",
        str(concurrency),
        "--api-concurrency",
        str(args.api_concurrency),
        "--batch-size",
        str(args.batch_size),
        "--retry",
        str(args.retry),
        "--retry-delay",
        str(args.retry_delay),
        "--quality",
        args.quality,
        "--codec",
        args.codec,
    ]
    if args.limit:
        command += ["--limit", str(args.limit)]
    if args.no_skip_existing:
        command += ["--no-skip-existing"]
    if platform == "douyin":
        command += ["--douyin-mode", args.douyin_mode]
    return command


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="多平台批量下载抖音/TikTok/快手作品文件（总并发默认 20，按平台均分）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--urls", default="作品链接清单.md", help="链接来源文件")
    parser.add_argument(
        "--douyin-mode",
        choices=("share", "api"),
        default="share",
        help=(
            "抖音下载方法：share=iesdouyin 免签分享接口（默认，无需 cookie，"
            "绕过 ArgusSecurityPlugin 风控）；api=Web 正式接口（需含完整 UIFID 的 cookie）"
        ),
    )
    parser.add_argument(
        "--no-urls",
        action="store_true",
        help="忽略 --urls 指向的链接文件，只使用 --link 提供的链接",
    )
    parser.add_argument(
        "--link",
        action="append",
        default=[],
        metavar="URL",
        help="直接指定作品链接，可重复；与 --urls 的内容合并",
    )
    parser.add_argument("--output", "-o", default="downloads", help="文件保存根目录")
    parser.add_argument(
        "--concurrency",
        "-c",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help="总下载并发数，按平台数量均分",
    )
    parser.add_argument(
        "--platform",
        action="append",
        choices=PLATFORMS,
        default=[],
        help="仅处理指定平台，可重复；默认根据链接自动判断",
    )
    parser.add_argument(
        "--default-platform",
        choices=PLATFORMS,
        default=DEFAULT_PLATFORM,
        help="无法从链接判断平台时的归属",
    )
    parser.add_argument(
        "--merge-output",
        action="store_true",
        help="所有平台共用同一个输出目录（默认按平台拆分子目录）",
    )
    parser.add_argument(
        "--foreground",
        action="store_true",
        help="子进程输出直接打印到当前终端（默认写入平台日志文件）",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="只打印将要执行的命令，不实际下载"
    )
    parser.add_argument("--api-concurrency", type=int, default=4, help="获取作品数据的并发数")
    parser.add_argument("--batch-size", type=int, default=50, help="每批处理的作品数量")
    parser.add_argument("--limit", type=int, default=0, help="每个平台仅处理前 N 个作品（0 表示全部）")
    parser.add_argument("--retry", type=int, default=3, help="获取作品数据失败时的重试次数")
    parser.add_argument("--retry-delay", type=float, default=2.0, help="重试等待基数（秒）")
    parser.add_argument(
        "--quality",
        choices=("lowest", "highest"),
        default="lowest",
        help=(
            "清晰度策略：lowest=最低清晰度（默认，省流量、省存储、分析上传更快）；"
            "highest=最高清晰度（原画，体积大）"
        ),
    )
    parser.add_argument(
        "--codec",
        choices=("any", "h264"),
        default="any",
        help=(
            "编码偏好：any=不限编码（默认，按 --quality 选档）；"
            "h264=优先 H.264 档，兼容性最好，适合送多模态模型分析"
        ),
    )
    parser.add_argument(
        "--no-skip-existing",
        action="store_true",
        help="不跳过输出目录中已存在的作品（默认跳过，便于断点续传）",
    )
    parser.add_argument("--config", default="batch_config.json", help="批处理配置文件路径")
    parser.add_argument(
        "--project-root",
        default=None,
        help=(
            "TikTokDownloader 仓库根目录（仅抖音 api 模式 / TikTok 需要），"
            f"默认自动探测或读取 {ENV_PROJECT_ROOT}"
        ),
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    allow = set(args.platform) or set(PLATFORMS)
    # 不依赖仓库 src 包的平台：快手（内置实现）、抖音 share 模式（免签直链）
    standalone = {"kuaishou"}
    if args.douyin_mode == "share":
        standalone.add("douyin")
    try:
        project_root = locate_project_root(args.project_root)
    except SystemExit:
        if allow - standalone:
            raise
        project_root = Path.cwd().resolve()
        print(
            f"{TAG} 未找到 TikTokDownloader 仓库，当前所选平台不依赖仓库，"
            "按当前目录运行"
        )
    script = Path(__file__).resolve().parent / PLATFORM_SCRIPT
    if not script.is_file():
        raise SystemExit(f"缺少平台下载脚本: {script}")

    lines = list(args.link)
    if args.urls and not args.no_urls:
        urls_path = Path(args.urls).expanduser()
        if not urls_path.is_absolute():
            urls_path = project_root / urls_path
        lines += read_lines(urls_path)
    if not lines:
        raise SystemExit("未提供任何作品链接")

    grouped = group_lines(lines, args.default_platform, allow)
    if not grouped:
        raise SystemExit(
            f"没有匹配到任何受支持平台的作品链接（当前限定: {', '.join(sorted(allow))}）"
        )

    order = [name for name in PLATFORMS if name in grouped]
    concurrency_list = split_concurrency(args.concurrency, len(order))

    output_root = Path(args.output).expanduser()
    if not output_root.is_absolute():
        output_root = project_root / output_root

    print(f"{TAG} 工作根目录: {project_root}")
    print(f"{TAG} 平台数量: {len(order)}  总并发: {args.concurrency}")
    print(f"{TAG} 清晰度策略: {args.quality}  编码偏好: {args.codec}")
    for name, quota in zip(order, concurrency_list):
        print(
            f"{TAG}   {PLATFORM_LABELS[name]}: {len(grouped[name])} 行链接，并发 {quota}"
        )

    plan: list[tuple[str, Path, Path, list[str], int]] = []
    for name, quota in zip(order, concurrency_list):
        temp_urls = write_links_file(name, grouped[name])
        output = output_root if args.merge_output else output_root / name
        command = build_command(args, script, name, temp_urls, output, quota)
        plan.append((name, temp_urls, output, command, quota))

    if args.dry_run:
        for name, _, output, command, _ in plan:
            print(f"{TAG} {PLATFORM_LABELS[name]} -> {output}")
            print("        " + " ".join(command))
        for _, temp_urls, _, _, _ in plan:
            temp_urls.unlink(missing_ok=True)
        return 0

    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(project_root), env.get("PYTHONPATH", "")])
    )
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    processes: list[tuple[str, subprocess.Popen, object, Path]] = []
    for name, temp_urls, output, command, _ in plan:
        output.mkdir(parents=True, exist_ok=True)
        if args.foreground:
            handle = None
            stdout = None
        else:
            log_path = output_root / f"{name}_download.log"
            handle = log_path.open("w", encoding="utf-8")
            stdout = handle
            print(f"{TAG} {PLATFORM_LABELS[name]} 日志: {log_path}")
        process = subprocess.Popen(
            command,
            cwd=str(project_root),
            env=env,
            stdout=stdout,
            stderr=subprocess.STDOUT,
        )
        processes.append((name, process, handle, temp_urls))

    results: list[tuple[str, int]] = []
    for name, process, handle, _ in processes:
        code = process.wait()
        if handle is not None:
            handle.close()
        results.append((name, code))
        label = PLATFORM_LABELS[name]
        print(f"{TAG} {label} 结束，退出码 {code}（{'成功' if code == 0 else '存在失败'}）")

    for _, _, _, temp_urls in processes:
        temp_urls.unlink(missing_ok=True)

    failed = [PLATFORM_LABELS[name] for name, code in results if code != 0]
    if failed:
        print(f"{TAG} 存在失败的平台: {', '.join(failed)}")
        return 1
    print(f"{TAG} 全部完成，文件保存于: {output_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
