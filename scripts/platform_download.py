#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""单平台作品文件下载器（douyin / tiktok / kuaishou）。

由 ``download_videos.py`` 按平台分别拉起（每个平台一个独立进程，因此各自
的并发限制互不干扰），也可单独使用。

平台实现差异：

* ``douyin``：提供两套下载方法，由 ``--douyin-mode`` 切换。

  - ``share``（**默认，推荐**）：走 ``iesdouyin`` 分享站免签接口
    ``/web/api/v2/aweme/slidesinfo/``，**不需要 cookie、不需要签名**，
    可绕过 ArgusSecurityPlugin 风控。见 ``douyin_share_download.py``。
  - ``api``：复用 TikTokDownloader 的 ``src`` 包走 Web 正式接口，需要
    **含完整 UIFID** 的 cookie；只有 ``UIFID_TEMP`` 时会被风控拦截并
    报 "响应内容不是有效的 JSON 数据，请尝试更新 Cookie！"。

* ``tiktok``：复用 TikTokDownloader 的 ``src`` 包。
* ``kuaishou``：仓库 ``src`` 包不含快手实现，因此使用本脚本内置的轻量实现
  ——请求移动端作品页，解析页面内嵌的 ``window.INIT_STATE``，取出
  ``manifest`` 多档直链（``mainMvUrls`` 兜底）后用 ``httpx`` 并发流式下载。
  **不需要 cookie**。

清晰度：``--quality`` 默认 ``lowest``（最低清晰度，省流量、省存储、
送多模态模型分析时上传更快）；需要原画时传 ``--quality highest``。

用法::

    python platform_download.py --platform douyin   --urls urls.txt -o downloads/douyin   -c 10
    python platform_download.py --platform douyin   --douyin-mode api --urls urls.txt -o downloads -c 10
    python platform_download.py --platform tiktok   --urls urls.txt -o downloads/tiktok   -c 10
    python platform_download.py --platform kuaishou --urls urls.txt -o downloads/kuaishou -c 10
    python platform_download.py --platform douyin   --urls urls.txt -o downloads --quality highest
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from time import monotonic, sleep

import httpx

# 允许在任意工作目录下直接运行本脚本（douyin --douyin-mode api / tiktok 依赖仓库 src 包）
_CWD = Path.cwd()
if (_CWD / "src").is_dir() and str(_CWD) not in sys.path:
    sys.path.insert(0, str(_CWD))
# 同目录下的 douyin_share_download 模块
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

PLATFORM_DOUYIN = "douyin"
PLATFORM_TIKTOK = "tiktok"
PLATFORM_KUAISHOU = "kuaishou"
PLATFORMS = (PLATFORM_DOUYIN, PLATFORM_TIKTOK, PLATFORM_KUAISHOU)
PLATFORM_LABEL = {
    PLATFORM_DOUYIN: "抖音",
    PLATFORM_TIKTOK: "TikTok",
    PLATFORM_KUAISHOU: "快手",
}

QUALITY_CHOICES = ("lowest", "highest")
# 清晰度指标缺失时的占位值：最低档模式下把"未知"视为极大值，避免被误判成最低档
UNKNOWN_METRIC = 1 << 40

# ============================================================ 快手（内置实现）

KUAISHOU_MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
    "Mobile/15E148 Safari/604.1"
)
KUAISHOU_HEADERS = {
    "User-Agent": KUAISHOU_MOBILE_UA,
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept": "text/html,application/xhtml+xml,*/*",
}
# 移动端作品页，页面内嵌 window.INIT_STATE
KUAISHOU_VIDEO_PAGE = "https://v.m.chenzhongtech.com/fw/photo/{pid}"
# 备用移动端域名，SSR 未返回作品数据时轮换重试
KUAISHOU_VIDEO_PAGES = (
    KUAISHOU_VIDEO_PAGE,
    "https://m.gifshow.com/fw/photo/{pid}",
)
# https://www.kuaishou.com/short-video/<photoId>
# https://v.m.chenzhongtech.com/fw/photo/<photoId>
KUAISHOU_DETAIL_RE = re.compile(
    r"(?:kuaishou\.com|chenzhongtech\.com)/(?:short-video|fw/photo)/([A-Za-z0-9_-]{6,})",
    re.IGNORECASE,
)
# https://v.kuaishou.com/xxxxxx 短链，需要请求重定向后才能拿到 photoId
KUAISHOU_SHORT_RE = re.compile(
    r"https?://[^\s/]*kuaishou\.com/([A-Za-z0-9_-]{6,})/?(?:\?.*)?$", re.IGNORECASE
)
MIN_FILE_SIZE = 10000


class KuaishouUnavailable(RuntimeError):
    """作品不可用（已删除 / 作者注销 / 审核中），重试无意义。"""


def is_highest(quality: str) -> bool:
    """``--quality`` 是否要求最高清晰度。"""
    return str(quality or "lowest").lower() in ("highest", "high", "best", "max")


def quality_metric(value: int, highest: bool) -> int:
    """把清晰度指标归一化：最低档模式下缺失值（0）映射为极大值。"""
    if value:
        return value
    return 0 if highest else UNKNOWN_METRIC


def decode_kuaishou_key(key: str) -> str:
    """快手移动端 INIT_STATE 的键名做过 ASCII +1 位移，这里还原。"""
    return "".join(chr(ord(ch) - 1) for ch in key)


def kuaishou_photo_entry(state):
    """从 INIT_STATE 中取出 ``/rest/wd/ugH5App/photo/simple/info`` 节点。"""
    if not isinstance(state, dict):
        return None
    for key, value in state.items():
        if isinstance(key, str) and "photo/simple/info" in decode_kuaishou_key(key):
            return value
    return None


def find_key(obj, key: str):
    """深度优先查找包含指定 key 的 dict。"""
    stack = [obj]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            if key in current:
                return current
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return None


def parse_kuaishou_state(html: str):
    """解析移动端页面里的 ``window.INIT_STATE``。"""
    index = html.find("window.INIT_STATE")
    if index < 0:
        return None
    start = html.find("{", index)
    if start < 0:
        return None
    try:
        state, _ = json.JSONDecoder().raw_decode(html[start:])
    except ValueError:
        return None
    return state


def resolve_kuaishou_short(url: str) -> str | None:
    """把 v.kuaishou.com 短链解析为 photoId。"""
    try:
        with httpx.Client(follow_redirects=True, timeout=20) as client:
            response = client.get(url, headers=KUAISHOU_HEADERS)
            match = KUAISHOU_DETAIL_RE.search(str(response.url))
            return match.group(1) if match else None
    except Exception:  # noqa: BLE001
        return None


def pick_kuaishou_urls(
    state, node: dict, codec: str = "any", quality: str = "lowest"
) -> tuple[list[str], str]:
    """挑选快手播放直链，默认取**最低清晰度**。

    ``mainMvUrls`` 只是默认档（常见 960x720），而页面里的
    ``manifest.adaptationSet[].representation[]`` 才包含完整档位列表
    （从 540p 到 1080p）。这里按 ``宽*高`` 与 ``avgBitrate`` 排序：

    * ``quality="lowest"``（默认）取最低档，省流量、省存储；
    * ``quality="highest"`` 取最高档（原画）。

    其余档位依次兜底，最后再把 ``mainMvUrls`` 追加为末位兜底。

    ``codec="h264"`` 时优先 ``videoCodec=avc`` 的档位（兼容性最好），
    编码偏好的优先级高于清晰度。
    """
    want_h264 = str(codec or "any").lower() in ("h264", "avc")
    highest = is_highest(quality)
    sign = 1 if highest else -1

    reps: list[dict] = []
    manifest = (node or {}).get("manifest")
    if not isinstance(manifest, dict):
        holder = find_key(state, "manifest") or {}
        manifest = holder.get("manifest") if isinstance(holder, dict) else None
    if not isinstance(manifest, dict):
        holder = find_key(state, "adaptationSet")
        manifest = holder if isinstance(holder, dict) else {}
    for adaptation in (manifest or {}).get("adaptationSet") or []:
        if isinstance(adaptation, dict):
            reps.extend(i for i in (adaptation.get("representation") or []) if isinstance(i, dict))
    if not reps:
        holder = find_key(state, "representation") or {}
        reps = [i for i in (holder.get("representation") or []) if isinstance(i, dict)]

    scored: list[tuple[tuple[int, int, int], dict]] = []
    for rep in reps:
        width = int(rep.get("width") or 0)
        height = int(rep.get("height") or 0)
        bitrate = int(rep.get("avgBitrate") or rep.get("maxBitrate") or 0)
        is_avc = str(rep.get("videoCodec") or "").lower() in ("avc", "h264")
        prefer = 1 if (want_h264 and is_avc) else 0
        scored.append(
            (
                (
                    prefer,
                    sign * quality_metric(width * height, highest),
                    sign * quality_metric(bitrate, highest),
                ),
                rep,
            )
        )
    scored.sort(key=lambda x: x[0], reverse=True)

    urls: list[str] = []
    seen: set[str] = set()

    def push(value) -> None:
        for url in value if isinstance(value, list) else [value]:
            if isinstance(url, str) and url and url not in seen:
                seen.add(url)
                urls.append(url)

    for _, rep in scored:
        push(rep.get("url"))
        push(rep.get("backupUrl"))

    for item in (node or {}).get("mainMvUrls") or []:
        if isinstance(item, dict):
            push(item.get("url"))

    picked = scored[0][1] if scored else None
    if picked and (int(picked.get("width") or 0) or int(picked.get("height") or 0)):
        label = f"{int(picked.get('width') or 0)}x{int(picked.get('height') or 0)}"
    else:
        label = f"{int((node or {}).get('width') or 0)}x{int((node or {}).get('height') or 0)}"
    return urls, label


def fetch_kuaishou_info(
    pid: str,
    page_tpl: str = KUAISHOU_VIDEO_PAGE,
    codec: str = "any",
    quality: str = "lowest",
) -> dict:
    """请求移动端作品页，解析出播放直链与基础信息。"""
    with httpx.Client(follow_redirects=True, timeout=30) as client:
        response = client.get(page_tpl.format(pid=pid), headers=KUAISHOU_HEADERS)
        response.raise_for_status()
        state = parse_kuaishou_state(response.text)
    if not state:
        raise RuntimeError("页面 INIT_STATE 解析失败")
    entry = kuaishou_photo_entry(state)
    if isinstance(entry, dict) and entry.get("result") != 1:
        raise KuaishouUnavailable(
            f"result={entry.get('result')} {entry.get('error_msg') or '作品不可用'}"
        )
    node = find_key(state, "mainMvUrls") or find_key(state, "representation")
    if not node:
        raise RuntimeError("未找到视频节点")

    play_urls, label = pick_kuaishou_urls(state, node, codec, quality)
    if not play_urls:
        raise RuntimeError("未解析到播放地址")

    caption_node = find_key(state, "caption") or {}
    return {
        "play_urls": play_urls,
        "quality": label,
        "desc": node.get("caption") or caption_node.get("caption") or "",
        "author": node.get("userName") or "",
        "duration": node.get("duration") or 0,
        "view_count": node.get("viewCount"),
        "like_count": node.get("likeCount") or node.get("realLikeCount"),
        "comment_count": node.get("commentCount"),
    }


def download_kuaishou_file(url: str, path: Path) -> int:
    """流式下载单个文件，先写 ``.part`` 再改名，避免半成品被当成完整文件。"""
    temp = path.with_suffix(".part")
    total = 0
    headers = {
        "User-Agent": KUAISHOU_MOBILE_UA,
        "Referer": "https://v.m.chenzhongtech.com/",
    }
    with httpx.Client(follow_redirects=True, timeout=120) as client:
        with client.stream("GET", url, headers=headers) as response:
            response.raise_for_status()
            with open(temp, "wb") as handle:
                for chunk in response.iter_bytes(1024 * 256):
                    handle.write(chunk)
                    total += len(chunk)
    if total < MIN_FILE_SIZE:
        temp.unlink(missing_ok=True)
        raise RuntimeError(f"文件过小 {total} 字节")
    temp.replace(path)
    return total


class KuaishouDownload:
    """快手单平台下载（不依赖仓库 ``src`` 包，也不需要 cookie）。"""

    def __init__(self, args: argparse.Namespace):
        self.urls_file = Path(args.urls).expanduser().resolve()
        self.output = Path(args.output).expanduser().resolve()
        self.concurrency = max(1, int(args.concurrency))
        self.retry = max(0, int(args.retry))
        self.retry_delay = max(0.0, float(args.retry_delay))
        self.limit = max(0, int(args.limit))
        self.skip_existing = not args.no_skip_existing
        self.codec = str(getattr(args, "codec", "any") or "any").lower()
        self.quality = str(getattr(args, "quality", "lowest") or "lowest").lower()
        self.total = 0
        self.finished = 0
        self.skipped = 0
        self._counter_lock = threading.Lock()
        self._log_lock = threading.Lock()

    def log(self, message: str) -> None:
        with self._log_lock:
            print(f"[{PLATFORM_LABEL[PLATFORM_KUAISHOU]}] {message}", flush=True)

    # ------------------------------------------------------------ 链接解析

    def read_ids(self) -> list[str]:
        if not self.urls_file.is_file():
            self.log(f"链接文件不存在: {self.urls_file}")
            return []
        text = self.urls_file.read_text(encoding="utf-8-sig")
        seen: set[str] = set()
        ids: list[str] = []
        for line in text.splitlines():
            for pid in self.extract_ids(line):
                if pid not in seen:
                    seen.add(pid)
                    ids.append(pid)
        self.log(f"从 {self.urls_file.name} 解析到 {len(ids)} 个作品 ID")
        return ids

    def extract_ids(self, line: str) -> list[str]:
        line = (line or "").strip()
        if not line:
            return []
        found = KUAISHOU_DETAIL_RE.findall(line)
        if found:
            return found
        if KUAISHOU_SHORT_RE.search(line):
            pid = resolve_kuaishou_short(line)
            return [pid] if pid else []
        return []

    # ------------------------------------------------------------ 断点续传

    def existing_ids(self) -> set[str]:
        """输出目录中已完成的作品 ID（文件名为 ``<photoId>.mp4``）。"""
        if not self.output.is_dir():
            return set()
        try:
            return {
                item.stem
                for item in self.output.glob("*.mp4")
                if item.is_file() and item.stat().st_size > MIN_FILE_SIZE
            }
        except OSError:
            return set()

    # ------------------------------------------------------------ 下载单个作品

    def fetch(self, pid: str) -> dict:
        last_error: Exception | None = None
        pages = KUAISHOU_VIDEO_PAGES
        for attempt in range(self.retry + 1):
            if attempt:
                sleep(self.retry_delay * attempt)
            try:
                return fetch_kuaishou_info(
                    pid, pages[attempt % len(pages)], self.codec, self.quality
                )
            except KuaishouUnavailable:
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        raise RuntimeError(f"获取作品信息失败: {last_error}")

    def download_one(self, pid: str) -> dict:
        target = self.output / f"{pid}.mp4"
        if self.skip_existing and target.is_file() and target.stat().st_size > MIN_FILE_SIZE:
            return {"pid": pid, "skipped": True}

        info = self.fetch(pid)
        last_error: Exception | None = None
        for url in info["play_urls"]:
            try:
                size = download_kuaishou_file(url, target)
                with self._counter_lock:
                    self.finished += 1
                    done, total = self.finished, self.total
                self.log(
                    f"OK [{done}/{total}] {pid} {size / 1048576:.1f}MB "
                    f"{info.get('quality') or '?'} "
                    f"{str(info.get('desc') or '')[:36]}"
                )
                return {"pid": pid, "size": size, **info}
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        raise RuntimeError(f"下载失败: {last_error}")

    # ------------------------------------------------------------ 入口

    def run(self) -> int:
        self.output.mkdir(parents=True, exist_ok=True)
        ids = self.read_ids()
        if not ids:
            return 1

        if self.skip_existing:
            exists = self.existing_ids()
            if exists:
                before = len(ids)
                ids = [i for i in ids if i not in exists]
                self.skipped = before - len(ids)
                self.log(f"断点续传：已存在 {self.skipped} 个作品文件，本次跳过")

        if self.limit:
            ids = ids[: self.limit]
            self.log(f"仅处理前 {len(ids)} 个待下载作品")

        if not ids:
            self.log("所有作品均已下载完成，无需处理")
            return 0

        self.total = len(ids)
        self.log(f"下载并发数已设置为 {self.concurrency}")
        self.log(f"清晰度策略 {self.quality} / 编码偏好 {self.codec}")
        self.log(f"文件保存路径: {self.output}")
        started = monotonic()
        failed = 0

        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futures = {pool.submit(self.download_one, pid): pid for pid in ids}
            for future in as_completed(futures):
                pid = futures[future]
                try:
                    future.result()
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    self.log(f"FAIL {pid} {type(exc).__name__}: {str(exc)[:120]}")

        elapsed = monotonic() - started
        self.log(
            f"[完成] 本次处理 {self.total} 个作品，成功 {self.finished} 个，"
            f"失败 {failed} 个，跳过已存在 {self.skipped} 个，"
            f"用时 {format_duration(elapsed)}"
        )
        self.log(f"[完成] 文件保存路径: {self.output}")
        return 1 if failed else 0


# ==================================================== 抖音 / TikTok（复用 src）


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


class PlatformDownload:
    """仅下载指定平台的作品文件（不含数据记录与 AI 分析）"""

    def __init__(self, args: argparse.Namespace):
        from src.interface import Detail, DetailTikTok
        from src.tools import ColorfulConsole

        self.console = ColorfulConsole(debug=False)
        self.platform = args.platform
        self.tiktok = self.platform == PLATFORM_TIKTOK
        self.processor = DetailTikTok if self.tiktok else Detail
        self.config_path = Path(args.config).expanduser().resolve()
        self.urls_file = Path(args.urls).expanduser().resolve()
        self.output = Path(args.output).expanduser().resolve()
        self.concurrency = max(1, int(args.concurrency))
        self.api_concurrency = max(1, int(args.api_concurrency))
        self.batch_size = max(1, int(args.batch_size))
        self.limit = max(0, int(args.limit))
        self.retry = max(0, int(args.retry))
        self.retry_delay = max(0.0, float(args.retry_delay))
        self.skip_existing = not args.no_skip_existing
        self.batch_config: dict = {}
        self.database = None
        self.parameter = None
        self.start_time = 0.0
        self.total = 0
        self.finished = 0
        self.failed = 0
        self.skipped = 0
        self.failed_ids: list[str] = []

    # ------------------------------------------------------------------ 初始化

    def load_config(self) -> bool:
        if not self.config_path.is_file():
            self.console.error(f"配置文件不存在: {self.config_path}")
            return False
        with self.config_path.open("r", encoding="utf-8") as f:
            self.batch_config = json.load(f)
        self.console.info(f"已加载配置文件: {self.config_path.name}")
        return True

    async def init_downloader(self) -> bool:
        from src.config import Parameter, Settings
        from src.custom import PROJECT_ROOT
        from src.downloader import Downloader
        from src.manager import Database, DownloadRecorder
        from src.module import Cookie
        from src.record import BaseLogger, LoggerManager

        self.database = Database()
        await self.database.__aenter__()
        config = {i["NAME"]: i["VALUE"] for i in await self.database.read_config_data()}

        settings = Settings(PROJECT_ROOT, self.console)
        settings_data = settings.read()

        settings_data["cookie"] = self.batch_config.get("cookie") or settings_data.get(
            "cookie", ""
        )
        settings_data["cookie_tiktok"] = self.batch_config.get("cookie_tiktok", "")
        settings_data["proxy"] = self.batch_config.get("proxy", "")
        settings_data["proxy_tiktok"] = self.batch_config.get("proxy_tiktok", "")

        self.output.mkdir(parents=True, exist_ok=True)
        settings_data["root"] = str(self.output.parent)
        settings_data["folder_name"] = self.output.name
        settings_data["storage_format"] = ""
        settings_data["download"] = True
        settings_data["folder_mode"] = False
        for key in ("name_format", "split", "date_format"):
            if self.batch_config.get(key):
                settings_data[key] = self.batch_config[key]

        cookie_obj = Cookie(settings, self.console)
        logger_class = LoggerManager if config.get("Logger") else BaseLogger
        recorder = DownloadRecorder(self.database, config.get("Record", 0), self.console)

        self.parameter = Parameter(
            settings=settings,
            cookie_object=cookie_obj,
            logger=logger_class,
            console=self.console,
            recorder=recorder,
            **settings_data,
        )
        await self.parameter.update_params()

        Downloader.semaphore = asyncio.Semaphore(self.concurrency)
        self.console.info(
            f"[{PLATFORM_LABEL[self.platform]}] 下载并发数已设置为 {self.concurrency}"
        )
        self.console.info(f"[{PLATFORM_LABEL[self.platform]}] 文件保存路径: {self.output}")
        return True

    # ------------------------------------------------------------------ 链接解析

    def id_patterns(self) -> tuple:
        """按平台返回作品 ID 提取规则。"""
        from src.link import Extractor, ExtractorTikTok

        if self.tiktok:
            return (ExtractorTikTok.detail_link, Extractor.detail_id)
        return (Extractor.detail_link, Extractor.detail_share, Extractor.detail_id)

    def read_ids(self) -> list[str]:
        from src.link import Extractor

        if not self.urls_file.is_file():
            self.console.error(f"链接文件不存在: {self.urls_file}")
            return []
        text = self.urls_file.read_text(encoding="utf-8-sig")
        patterns = self.id_patterns()
        seen: set[str] = set()
        ids: list[str] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            for pattern in patterns:
                found = Extractor.extract_info(pattern, line)
                if found:
                    for detail_id in found:
                        if detail_id not in seen:
                            seen.add(detail_id)
                            ids.append(detail_id)
                    break
        self.console.info(
            f"[{PLATFORM_LABEL[self.platform]}] 从 {self.urls_file.name} 解析到 {len(ids)} 个作品 ID"
        )
        return ids

    # ------------------------------------------------------------------ 进度显示

    def _progress(self):
        from rich.progress import (
            BarColumn,
            Progress,
            SpinnerColumn,
            TextColumn,
            TimeElapsedColumn,
        )

        return Progress(
            TextColumn("[progress.description]{task.description}", justify="left"),
            SpinnerColumn(),
            BarColumn(bar_width=24),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            console=self.console,
            transient=True,
            expand=True,
        )

    def print_overall(self, batch_no: int, total_batches: int, extra: str = ""):
        elapsed = monotonic() - self.start_time
        speed = self.finished / elapsed if elapsed > 0 else 0
        remain = (self.total - self.finished) / speed if speed > 0 else 0
        self.console.info(
            f"[{PLATFORM_LABEL[self.platform]}] [总体进度] 批次 {batch_no}/{total_batches} | "
            f"已下载 {self.finished}/{self.total} | 失败 {self.failed} | "
            f"用时 {format_duration(elapsed)} | 剩余约 {format_duration(remain)} | "
            f"速度 {speed:.2f} 个/秒{extra}"
        )

    # ------------------------------------------------------------------ 下载流程

    def existing_ids(self) -> set[str]:
        """扫描下载目录，收集已下载完成的作品 ID（文件名以 ID 开头）"""
        if not self.output.is_dir():
            return set()
        result = set()
        try:
            for item in self.output.iterdir():
                if item.is_file() and item.stat().st_size > 0:
                    prefix = item.name.split(self.parameter.split, 1)[0]
                    if prefix.isdigit() and len(prefix) >= 15:
                        result.add(prefix)
        except OSError:
            return set()
        return result

    async def fetch_one(self, tiktok, semaphore, progress, task_id, detail_id: str):
        async with semaphore:
            try:
                for attempt in range(self.retry + 1):
                    if attempt:
                        await asyncio.sleep(self.retry_delay * attempt)
                    try:
                        data = await tiktok.handle_detail_single(
                            self.processor, None, None, detail_id
                        )
                    except Exception as e:  # noqa: BLE001
                        if attempt >= self.retry:
                            raise
                        self.console.warning(
                            f"作品 {detail_id} 第 {attempt + 1} 次获取失败，准备重试: {e}"
                        )
                        continue
                    if data:
                        return data
                    if attempt < self.retry:
                        self.console.warning(
                            f"作品 {detail_id} 第 {attempt + 1} 次获取无数据，准备重试"
                        )
                return None
            except Exception as e:  # noqa: BLE001
                self.console.warning(f"作品 {detail_id} 获取数据失败: {e}")
                self.failed_ids.append(detail_id)
                return None
            finally:
                progress.advance(task_id)

    async def fetch_batch(
        self, tiktok, batch: list[str], batch_no: int, total_batches: int
    ) -> list[dict]:
        semaphore = asyncio.Semaphore(self.api_concurrency)
        with self._progress() as progress:
            task_id = progress.add_task(
                f"[{batch_no}/{total_batches}] 获取作品数据",
                total=len(batch),
            )
            result = await asyncio.gather(
                *(self.fetch_one(tiktok, semaphore, progress, task_id, i) for i in batch)
            )
        return [i for i in result if i]

    async def run_download(self, ids: list[str]) -> None:
        from src.application.main_terminal import TikTok
        from src.storage.text import BaseTextLogger

        tiktok = TikTok(self.parameter, self.database, server_mode=False)
        batches = [
            ids[i : i + self.batch_size] for i in range(0, len(ids), self.batch_size)
        ]
        self.total = len(ids)
        self.start_time = monotonic()

        for batch_no, batch in enumerate(batches, 1):
            self.console.info(
                f"\n[{PLATFORM_LABEL[self.platform]}] [批次 {batch_no}/{len(batches)}] "
                f"开始处理 {len(batch)} 个作品"
            )
            detail_data = await self.fetch_batch(tiktok, batch, batch_no, len(batches))
            if not detail_data:
                self.failed += len(batch)
                self.print_overall(batch_no, len(batches), " (本批次无有效数据)")
                continue

            processed = await tiktok.extractor.run(
                detail_data, BaseTextLogger(), tiktok=self.tiktok
            )
            if not processed:
                self.failed += len(batch)
                self.print_overall(batch_no, len(batches), " (本批次数据提取失败)")
                continue

            self.finished += len(processed)
            self.failed += len(batch) - len(processed)

            await tiktok.downloader.run(processed, "detail", tiktok=self.tiktok)

            self.print_overall(batch_no, len(batches))

    # ------------------------------------------------------------------ 入口

    async def run(self) -> int:
        try:
            if not self.load_config():
                return 1
            ids = self.read_ids()
            if not ids:
                return 1
            if not await self.init_downloader():
                return 1

            if self.skip_existing:
                exists = self.existing_ids()
                if exists:
                    before = len(ids)
                    ids = [i for i in ids if i not in exists]
                    self.skipped = before - len(ids)
                    self.console.info(
                        f"[{PLATFORM_LABEL[self.platform]}] 断点续传：已存在 "
                        f"{self.skipped} 个作品文件，本次跳过"
                    )
            if self.limit:
                ids = ids[: self.limit]
                self.console.info(f"仅处理前 {len(ids)} 个待下载作品")
            if not ids:
                self.console.info("所有作品均已下载完成，无需处理")
                return 0

            await self.run_download(ids)

            elapsed = monotonic() - self.start_time
            self.console.info(
                f"\n[{PLATFORM_LABEL[self.platform]}] [完成] 本次处理 {self.total} 个作品，"
                f"成功 {self.finished} 个，失败 {self.failed} 个，"
                f"跳过已存在 {self.skipped} 个，用时 {format_duration(elapsed)}"
            )
            self.console.info(
                f"[{PLATFORM_LABEL[self.platform]}] [完成] 文件保存路径: {self.output}"
            )
            if self.failed_ids:
                self.console.warning(
                    f"[{PLATFORM_LABEL[self.platform]}] [完成] 失败作品数量: {len(self.failed_ids)}"
                )
            return 0
        except KeyboardInterrupt:
            self.console.warning("\n用户中断操作")
            return 1
        except Exception as e:  # noqa: BLE001
            self.console.error(f"下载任务失败: {e}")
            traceback.print_exc()
            return 1
        finally:
            if self.database:
                await self.database.__aexit__(None, None, None)
            if self.parameter:
                await self.parameter.close_client()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="单平台作品批量下载（douyin / tiktok / kuaishou，仅下载文件，不做 AI 分析）"
    )
    parser.add_argument(
        "--platform",
        choices=PLATFORMS,
        default=PLATFORM_DOUYIN,
        help="目标平台，默认 douyin",
    )
    parser.add_argument(
        "--douyin-mode",
        choices=("share", "api"),
        default="share",
        help=(
            "抖音下载方法：share=iesdouyin 免签分享接口（默认，无需 cookie，"
            "绕过 ArgusSecurityPlugin 风控）；api=复用 src 包的 Web 正式接口"
            "（需含完整 UIFID 的 cookie）"
        ),
    )
    parser.add_argument(
        "--config",
        default="batch_config.json",
        help="批处理配置文件路径（读取 cookie / 代理等），默认 batch_config.json；快手不使用",
    )
    parser.add_argument(
        "--urls",
        default="作品链接清单.md",
        help="链接来源文件，默认 作品链接清单.md",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="downloads",
        help="文件保存目录，默认 downloads",
    )
    parser.add_argument("--concurrency", "-c", type=int, default=10, help="文件下载并发数，默认 10")
    parser.add_argument(
        "--api-concurrency", type=int, default=4, help="获取作品数据的并发数，默认 4"
    )
    parser.add_argument("--batch-size", type=int, default=50, help="每批处理的作品数量，默认 50")
    parser.add_argument("--limit", type=int, default=0, help="仅处理前 N 个作品（0 表示全部）")
    parser.add_argument("--retry", type=int, default=3, help="获取作品数据失败时的重试次数")
    parser.add_argument("--retry-delay", type=float, default=2.0, help="重试等待基数（秒）")
    parser.add_argument(
        "--quality",
        choices=QUALITY_CHOICES,
        default="lowest",
        help=(
            "清晰度策略：lowest=最低清晰度（默认，省流量、省存储、分析上传更快）；"
            "highest=最高清晰度（原画，体积大）。仅对 douyin share / kuaishou 生效"
        ),
    )
    parser.add_argument(
        "--codec",
        choices=("any", "h264"),
        default="any",
        help=(
            "编码偏好：any=不限编码（默认，按 --quality 选档）；"
            "h264=优先 H.264 档，兼容性最好，"
            "适合送多模态模型分析或在老旧播放器里播放"
        ),
    )
    parser.add_argument(
        "--no-skip-existing",
        action="store_true",
        help="不跳过下载目录中已存在的作品（默认跳过，便于断点续传）",
    )
    return parser


def run_douyin_share(args: argparse.Namespace) -> int:
    """抖音免签通路（默认）：iesdouyin slidesinfo 接口 + 直链下载。"""
    from douyin_share_download import DouyinShareDownload, extract_douyin_ids

    urls_file = Path(args.urls).expanduser().resolve()
    if not urls_file.is_file():
        print(f"[{PLATFORM_LABEL[PLATFORM_DOUYIN]}] 链接文件不存在: {urls_file}", flush=True)
        return 1
    ids = extract_douyin_ids(urls_file.read_text(encoding="utf-8-sig").splitlines())
    print(
        f"[{PLATFORM_LABEL[PLATFORM_DOUYIN]}] 从 {urls_file.name} 解析到 {len(ids)} 个作品 ID",
        flush=True,
    )
    if args.limit:
        ids = ids[: max(0, int(args.limit))]

    proxy = ""
    config_path = Path(args.config).expanduser().resolve()
    if config_path.is_file():
        try:
            proxy = (json.loads(config_path.read_text(encoding="utf-8")) or {}).get(
                "proxy", ""
            )
        except (OSError, json.JSONDecodeError):
            proxy = ""

    return DouyinShareDownload(
        output=Path(args.output),
        concurrency=max(1, int(args.concurrency)),
        api_batch=10,
        retry=max(0, int(args.retry)),
        retry_delay=max(0.0, float(args.retry_delay)),
        skip_existing=not args.no_skip_existing,
        proxy=proxy or None,
        codec=getattr(args, "codec", "any"),
        quality=getattr(args, "quality", "lowest"),
    ).run(ids)


def main() -> int:
    args = build_parser().parse_args()
    if args.platform == PLATFORM_KUAISHOU:
        return KuaishouDownload(args).run()
    if args.platform == PLATFORM_DOUYIN and args.douyin_mode == "share":
        return run_douyin_share(args)
    return asyncio.run(PlatformDownload(args).run())


if __name__ == "__main__":
    sys.exit(main())
