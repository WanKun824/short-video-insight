#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""抖音免签直链下载器（``iesdouyin`` 分享通路）。

背景：抖音 Web 的 ``/aweme/v1/web/aweme/detail/`` 接口由 ArgusSecurityPlugin
风控保护，请求必须携带完整的 ``UIFID``。浏览器导出的 cookie 通常只有
``UIFID_TEMP``，因此该接口会直接返回 ``403 blocked``，表现为
"响应内容不是有效的 JSON 数据，请尝试更新 Cookie！"。

本模块改走 ``iesdouyin`` 分享站的免签接口::

    GET https://www.iesdouyin.com/web/api/v2/aweme/slidesinfo/?aweme_ids=[id1,id2,...]

特点：

* **不需要 cookie、不需要 a_bogus / X-Bogus 签名**；
* 单次请求可批量取多个作品（默认 10 个一批）；
* 返回结构与 App 端一致（``aweme_details`` → ``video.play_addr.url_list`` /
  ``images``），可直接拿到无水印播放直链；
* 直链支持 ``Range``，天然可断点续传。

注意：``aweme_ids`` 必须是 ``[123,456]`` 形式的**裸数字数组**，
带引号的 ``["123"]`` 会返回 ``aweme_details: null``。

清晰度：同一作品会返回多档清晰度，``pick_video_urls`` 默认挑**最低清晰度**
（``quality="lowest"``，省流量、省存储、分析时上传更快），
传 ``quality="highest"`` 则挑最高清晰度。
"""

from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from time import monotonic, sleep
from typing import Iterable, Optional
from urllib.parse import quote

import httpx

__all__ = [
    "DouyinShareAPI",
    "DouyinShareDownload",
    "extract_douyin_ids",
    "pick_video_urls",
]

# ----------------------------------------------------------------- 常量

SLIDES_INFO_API = "https://www.iesdouyin.com/web/api/v2/aweme/slidesinfo/?aweme_ids={ids}"

UA_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
    "Mobile/15E148 Safari/604.1"
)
API_HEADERS = {
    "User-Agent": UA_IPHONE,
    "Accept": "application/json,*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://www.douyin.com/",
}
FILE_HEADERS = {
    "User-Agent": UA_IPHONE,
    "Referer": "https://www.douyin.com/",
}

# 支持 /video/ /note/ /slides/ /share/xxx/ 以及 modal_id= 形式
DOUYIN_ID_PATTERNS = (
    re.compile(r"douyin\.com/(?:video|note|slides)/(\d{19})", re.IGNORECASE),
    re.compile(
        r"iesdouyin\.com/share/(?:video|note|slides)/(\d{19})", re.IGNORECASE
    ),
    re.compile(r"douyin\.com/\S*?modal_id=(\d{19})", re.IGNORECASE),
)
BARE_ID_RE = re.compile(r"(?<!\d)(\d{19})(?!\d)")

# 小于该体积视为下载失败（风控页 / 空响应）
MIN_FILE_SIZE = 10000
# 单次批量请求的作品数量，实测 10 稳定
DEFAULT_BATCH = 10
# 指标缺失时的占位权重：lowest 模式下按"最大"处理，避免未知档被误选为最低档
UNKNOWN_METRIC = 1 << 40


def extract_douyin_ids(lines: Iterable[str]) -> list[str]:
    """从任意文本行中提取抖音作品 ID（保序去重）。"""
    seen: set[str] = set()
    result: list[str] = []
    for line in lines:
        line = (line or "").strip()
        if not line:
            continue
        found: list[str] = []
        for pattern in DOUYIN_ID_PATTERNS:
            found.extend(pattern.findall(line))
        if not found and "douyin" not in line.lower():
            # 纯 ID 清单（一行一个 19 位数字）
            found = BARE_ID_RE.findall(line)
        for detail_id in found:
            if detail_id not in seen:
                seen.add(detail_id)
                result.append(detail_id)
    return result


def safe_name(text: str, limit: int = 50) -> str:
    text = re.sub(r"[\\/:*?\"<>|\r\n\t]", "_", str(text or "")).strip(" .")
    text = re.sub(r"\s+", " ", text)
    return text[:limit]


# ----------------------------------------------------------------- 取数据


class DouyinShareAPI:
    """免签批量取作品详情。"""

    def __init__(
        self,
        timeout: float = 40.0,
        retry: int = 3,
        retry_delay: float = 2.0,
        proxy: Optional[str] = None,
    ):
        self.timeout = timeout
        self.retry = max(0, retry)
        self.retry_delay = max(0.0, retry_delay)
        self.proxy = proxy or None

    def _client(self) -> httpx.Client:
        return httpx.Client(
            follow_redirects=True,
            timeout=self.timeout,
            headers=API_HEADERS,
            proxy=self.proxy,
        )

    def fetch_batch(self, ids: list[str]) -> list[dict]:
        """批量取作品详情；返回的条目数可能少于请求数（失效作品被丢弃）。"""
        if not ids:
            return []
        # 关键：裸数字数组，不能带引号
        url = SLIDES_INFO_API.format(ids=quote(f"[{','.join(ids)}]"))
        last_error: Optional[Exception] = None
        for attempt in range(self.retry + 1):
            if attempt:
                sleep(self.retry_delay * attempt)
            try:
                with self._client() as client:
                    response = client.get(url)
                response.raise_for_status()
                data = response.json()
            except (httpx.HTTPError, json.JSONDecodeError) as exc:
                last_error = exc
                continue
            if data.get("status_code") not in (0, None):
                last_error = RuntimeError(
                    f"status_code={data.get('status_code')} {data.get('status_msg')}"
                )
                continue
            return data.get("aweme_details") or []
        raise RuntimeError(f"批量取数失败: {last_error}")

    def fetch_all(self, ids: list[str], batch: int = DEFAULT_BATCH) -> dict[str, dict]:
        """分批取全部作品详情，返回 ``{aweme_id: detail}``。"""
        batch = max(1, batch)
        result: dict[str, dict] = {}
        for index in range(0, len(ids), batch):
            for item in self.fetch_batch(ids[index : index + batch]):
                aweme_id = str(item.get("aweme_id") or "")
                if aweme_id:
                    result[aweme_id] = item
        return result


# ----------------------------------------------------------------- 解析


def _metric(value: int, highest: bool) -> int:
    """把指标归一化为排序权重。

    指标缺失（0，例如 ``download_addr`` 不带 ``width`` / ``height``）时：

    * ``highest`` 模式按 0 处理 → 排在最后，不会被当成最高清档；
    * ``lowest`` 模式按极大值处理 → 同样排在最后，不会被误判为最低清档。
    """
    if value:
        return value
    return 0 if highest else UNKNOWN_METRIC


def _addr_size(node: dict) -> tuple[int, int]:
    """返回播放地址节点的 ``(宽*高, data_size)``。"""
    if not isinstance(node, dict):
        return (0, 0)
    width = int(node.get("width") or 0)
    height = int(node.get("height") or 0)
    return (width * height, int(node.get("data_size") or 0))


def pick_video_urls(
    video: dict, codec: str = "any", quality: str = "lowest"
) -> tuple[list[str], int, str]:
    """按指定清晰度策略挑选播放直链。

    抖音同一作品会给出多档清晰度：顶层的 ``play_addr`` / ``play_addr_h264``
    通常是 540p 左右的兜底档，而 ``bit_rate`` 列表里才包含完整档位
    （``gear_name`` 形如 ``720_1_1``）。本函数把所有候选节点统一按
    ``(宽*高, 码率, data_size)`` 排序：

    * ``quality="lowest"``（默认）：取分辨率最低、同分辨率下码率最小的一档；
    * ``quality="highest"``：取分辨率最高、同分辨率下码率最大的一档。

    其余档位作为兜底直链按序追加，避免首选档 CDN 失效时整条作品失败。

    ``codec`` 控制编码偏好（始终优先级最高，不受 ``quality`` 影响）：

    * ``any``（默认）：不限编码；
    * ``h264``：优先 H.264 档，兼容性最好，适合送多模态模型分析；
      没有 H.264 档时自动回退。

    返回 ``(直链列表, 预期字节数, 清晰度标签)``。
    """
    want_h264 = str(codec or "any").lower() in ("h264", "avc")
    highest = str(quality or "lowest").lower() in ("highest", "high", "best", "max")
    candidates: list[tuple[tuple[int, int, int, int], str, dict]] = []

    for rate in video.get("bit_rate") or []:
        if not isinstance(rate, dict):
            continue
        node = rate.get("play_addr") or {}
        if not (node.get("url_list") or []):
            continue
        pixels, data_size = _addr_size(node)
        bitrate = int(rate.get("bit_rate") or 0)
        label = str(rate.get("gear_name") or "")
        is_h265 = bool(rate.get("is_h265") or rate.get("is_bytevc1"))
        # want_h264 时让 H.264 档排在前面，否则编码不参与排序
        prefer = 1 if (want_h264 and not is_h265) else 0
        key = (
            prefer,
            _metric(pixels, highest),
            _metric(bitrate, highest),
            _metric(data_size, highest),
        )
        candidates.append((key, label, node))

    for name in ("play_addr_265", "play_addr_h264", "play_addr", "download_addr"):
        node = video.get(name) or {}
        if not (node.get("url_list") or []):
            continue
        pixels, data_size = _addr_size(node)
        is_h265 = name == "play_addr_265" or bool(video.get("is_h265"))
        prefer = 1 if (want_h264 and not is_h265) else 0
        key = (
            prefer,
            _metric(pixels, highest),
            _metric(0, highest),
            _metric(data_size, highest),
        )
        candidates.append((key, name, node))

    if not candidates:
        return [], 0, ""

    # 编码偏好恒为降序；清晰度指标在 lowest 模式下取负实现升序
    sign = 1 if highest else -1
    candidates.sort(
        key=lambda item: (item[0][0], *(sign * value for value in item[0][1:])),
        reverse=True,
    )

    urls: list[str] = []
    seen: set[str] = set()
    for _, _, node in candidates:
        for url in node.get("url_list") or []:
            if url and url not in seen:
                seen.add(url)
                urls.append(url)

    _, label, best = candidates[0]
    pixels, data_size = _addr_size(best)
    width = int(best.get("width") or 0)
    height = int(best.get("height") or 0)
    quality_label = f"{width}x{height}" if pixels else (label or "unknown")
    return urls, data_size, quality_label


def parse_detail(item: dict, codec: str = "any", quality: str = "lowest") -> dict:
    """把接口返回的作品详情整理为下载所需的最小结构。"""
    aweme_id = str(item.get("aweme_id") or "")
    desc = item.get("desc") or ""
    author = ((item.get("author") or {}).get("nickname")) or ""
    images = item.get("images") or []
    if images:
        assets = []
        for index, image in enumerate(images, 1):
            urls = [i for i in (image.get("url_list") or []) if i]
            if urls:
                assets.append({"index": index, "urls": urls, "suffix": ".jpeg"})
        return {
            "id": aweme_id,
            "type": "images",
            "desc": desc,
            "author": author,
            "assets": assets,
        }

    video = item.get("video") or {}
    urls, size, label = pick_video_urls(video, codec, quality)
    return {
        "id": aweme_id,
        "type": "video",
        "desc": desc,
        "author": author,
        "duration": video.get("duration") or 0,
        "size": size,
        "quality": label,
        "assets": [{"index": 1, "urls": urls, "suffix": ".mp4"}] if urls else [],
    }


# ----------------------------------------------------------------- 下载


def download_file(
    urls: list[str],
    target: Path,
    timeout: float = 120.0,
    proxy: Optional[str] = None,
    resume: bool = True,
) -> int:
    """依次尝试多条直链，流式写入 ``.part`` 后改名；支持 Range 断点续传。"""
    temp = target.with_suffix(target.suffix + ".part")
    last_error: Optional[Exception] = None
    for url in urls:
        position = temp.stat().st_size if resume and temp.is_file() else 0
        headers = dict(FILE_HEADERS)
        mode = "wb"
        if position:
            headers["Range"] = f"bytes={position}-"
            mode = "ab"
        try:
            with httpx.Client(
                follow_redirects=True, timeout=timeout, proxy=proxy
            ) as client:
                with client.stream("GET", url, headers=headers) as response:
                    if position and response.status_code == 416:
                        # 缓存异常，清掉重下
                        temp.unlink(missing_ok=True)
                        raise RuntimeError("断点缓存异常（416）")
                    if position and response.status_code != 206:
                        # 服务端不支持续传，从头写
                        temp.unlink(missing_ok=True)
                        position, mode = 0, "wb"
                        headers.pop("Range", None)
                    response.raise_for_status()
                    total = position
                    with open(temp, mode) as handle:
                        for chunk in response.iter_bytes(1024 * 256):
                            handle.write(chunk)
                            total += len(chunk)
            if total < MIN_FILE_SIZE:
                temp.unlink(missing_ok=True)
                raise RuntimeError(f"文件过小 {total} 字节")
            temp.replace(target)
            return total
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    raise RuntimeError(f"下载失败: {last_error}")


class DouyinShareDownload:
    """抖音免签下载（无 cookie，绕过 ArgusSecurityPlugin 风控）。"""

    def __init__(
        self,
        output: Path,
        concurrency: int = 10,
        api_batch: int = DEFAULT_BATCH,
        retry: int = 3,
        retry_delay: float = 2.0,
        skip_existing: bool = True,
        proxy: Optional[str] = None,
        name_with_desc: bool = True,
        codec: str = "any",
        quality: str = "lowest",
        log=print,
    ):
        self.output = Path(output).expanduser().resolve()
        self.concurrency = max(1, concurrency)
        self.api_batch = max(1, api_batch)
        self.retry = max(0, retry)
        self.retry_delay = max(0.0, retry_delay)
        self.skip_existing = skip_existing
        self.proxy = proxy
        self.name_with_desc = name_with_desc
        self.codec = str(codec or "any").lower()
        self.quality = str(quality or "lowest").lower()
        self._log = log
        self.api = DouyinShareAPI(
            retry=retry, retry_delay=retry_delay, proxy=proxy
        )
        self.total = 0
        self.finished = 0
        self.failed = 0
        self.skipped = 0
        self._lock = threading.Lock()
        self._log_lock = threading.Lock()

    def log(self, message: str) -> None:
        with self._log_lock:
            self._log(f"[抖音] {message}")

    # ------------------------------------------------------------ 断点续传

    def existing_ids(self) -> set[str]:
        if not self.output.is_dir():
            return set()
        result: set[str] = set()
        try:
            for item in self.output.iterdir():
                if not item.is_file() or item.suffix == ".part":
                    continue
                if item.stat().st_size <= MIN_FILE_SIZE:
                    continue
                match = BARE_ID_RE.match(item.name)
                if match:
                    result.add(match.group(1))
        except OSError:
            return set()
        return result

    # ------------------------------------------------------------ 单个作品

    def target_path(self, info: dict, asset: dict) -> Path:
        parts = [info["id"]]
        if self.name_with_desc:
            label = safe_name(info.get("desc") or info.get("author") or "")
            if label:
                parts.append(label)
        if info["type"] == "images" and len(info["assets"]) > 1:
            parts.append(f"{asset['index']:02d}")
        return self.output / ("-".join(parts) + asset["suffix"])

    def download_one(self, info: dict) -> int:
        if not info["assets"]:
            raise RuntimeError("未解析到下载地址")
        size = 0
        for asset in info["assets"]:
            target = self.target_path(info, asset)
            if (
                self.skip_existing
                and target.is_file()
                and target.stat().st_size > MIN_FILE_SIZE
            ):
                continue
            size += download_file(asset["urls"], target, proxy=self.proxy)
        with self._lock:
            self.finished += 1
            done, total = self.finished, self.total
        self.log(
            f"OK [{done}/{total}] {info['id']} {size / 1048576:.1f}MB "
            f"{info.get('quality') or '?'} "
            f"{(info.get('desc') or '')[:32]}"
        )
        return size

    # ------------------------------------------------------------ 入口

    def run(self, ids: list[str]) -> int:
        self.output.mkdir(parents=True, exist_ok=True)
        if not ids:
            self.log("没有待下载的作品")
            return 1

        if self.skip_existing:
            exists = self.existing_ids()
            if exists:
                before = len(ids)
                ids = [i for i in ids if i not in exists]
                self.skipped = before - len(ids)
                self.log(f"断点续传：已存在 {self.skipped} 个作品文件，本次跳过")
        if not ids:
            self.log("所有作品均已下载完成")
            return 0

        self.log("免签通路：iesdouyin slidesinfo（无需 cookie）")
        self.log(
            f"清晰度策略 {self.quality} / 编码偏好 {self.codec} / "
            f"取数批量 {self.api_batch} / 下载并发 {self.concurrency}"
        )
        self.log(f"文件保存路径: {self.output}")

        started = monotonic()
        details = self.api.fetch_all(ids, self.api_batch)
        missing = [i for i in ids if i not in details]
        if missing:
            self.log(f"{len(missing)} 个作品未返回数据（已删除/私密/审核中）")
        self.total = len(details)
        if not self.total:
            self.log("接口未返回任何作品数据")
            return 1

        infos = [
            parse_detail(details[i], self.codec, self.quality)
            for i in ids
            if i in details
        ]
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futures = {pool.submit(self.download_one, info): info for info in infos}
            for future in as_completed(futures):
                info = futures[future]
                try:
                    future.result()
                except Exception as exc:  # noqa: BLE001
                    self.failed += 1
                    self.log(f"FAIL {info['id']} {type(exc).__name__}: {str(exc)[:120]}")

        elapsed = max(0, int(monotonic() - started))
        self.log(
            f"[完成] 本次处理 {self.total} 个作品，成功 {self.finished} 个，"
            f"失败 {self.failed + len(missing)} 个，跳过已存在 {self.skipped} 个，"
            f"用时 {elapsed // 3600:02d}:{elapsed % 3600 // 60:02d}:{elapsed % 60:02d}"
        )
        self.log(f"[完成] 文件保存路径: {self.output}")
        return 1 if (self.failed or missing) else 0
