#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""视频内容理解分析器（多模态大模型），支持 Gemini / Qwen-VL 两种后端。

本模块**自包含**，只依赖 ``httpx``（qwen 后端额外需要 ``dashscope``），
可独立使用，不依赖任何下载器仓库。

两种模型后端：

* ``gemini``（默认）：走任意兼容 Gemini ``generateContent`` 原生接口的网关，
  视频以 Base64 内联上传。受接口体积限制，**建议单文件 < 20MB**。
  网关地址由 ``GEMINI_API_BASE`` 环境变量或 ``api_base`` 参数指定。
* ``qwen``：通过阿里云百炼 DashScope SDK 的 ``MultiModalConversation``，
  支持 ``file://`` 本地绝对路径直传，**绕过 Base64 体积限制**，更适合大文件。
  需要 ``pip install dashscope``，并提供 API Key（或设置 ``DASHSCOPE_API_KEY``）。

分析维度（见 ``PROMPT_TEXT``）：内容主题分类、内容节奏、是否有 3 秒钩子、
3 秒钩子类型、核心叙事框架、内容视角。模型按固定格式输出
``===标签汇总=== ... ===标签结束===`` 区块，由 ``parse_analysis_result`` 解析成字段。

**与下载环节的配合**：送多模态模型分析时建议用 ``--codec h264`` 下载，
HEVC/H.265 视频部分模型解码失败；同时默认的 ``--quality lowest``
会挑最小体积档位，上传更快、更不容易超出接口体积限制
（见 ``docs/download-mechanics.md``）。
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
from pathlib import Path
from typing import Optional

import httpx

# ──────────────────────────────────────────────────────────── 分析提示词

PROMPT_TEXT = """请分析这个游戏相关视频，并按以下维度进行分类。

【分类维度与详细说明】

## 1. 内容主题分类（必选一项）：

- **游戏实况与剪辑**：以局内实况录制为主，基本无剪辑无bgm，与高光剪辑区分；随游戏过程发出的一些自然反应比如感叹之类的语言。有一定的游戏时长，一段完整连续的游戏过程，整个过程可能并非全程紧张刺激；或者为视频时长短，以游戏内击杀boss高光、游戏CG剪辑、击败/击杀对手为主，无太多其他游戏实况内容。强调bgm搭配。混剪精彩对战、操作、走位、击杀瞬间等；第一人称射击类游戏的击杀集锦，操作娴熟，多次成功击杀敌人，展现出高超的游戏技巧和掌控感，短时间内展示了多回合的操作。聚焦于高超游戏技巧、击杀集锦、击杀时刻的游戏实况属于高光剪辑，视频内容全是对战时刻

- **游戏内资产展示**：强调专注于分享、展示、锐评游戏皮肤的视频内容；强调以游戏内武器主体的内容，重点展示检视动作、击杀演示等；展示游戏角色造型、技能展示等游戏角色内容，重点在对角色进行展示介绍，对某一角色单独的游戏攻略属于角色展示

- **游戏攻略**：详细测评/介绍游戏的武器、角色、Boss等，通常时长、深度拉满；非常规的战术操作解读，包含创意、自嘲或幽默式等非常规的教学攻略，视频可以起到告诉大家游戏技巧的性质，可能为游戏套路

- **游戏资讯**：以资讯news、top N tips等轻度解说内容为主，与深度攻略对应，例如版本更新等信息

- **游戏测评**：点评游戏内容（游戏剧情、角色、场景、玩法）的好坏或体验游戏更新玩法；或对游戏某个技能，某种情况进行评价

- **游戏宣传**：游戏CG、PV等

- **创意整活与二创**：通过游戏内容展示创意，而非其他动画等二创形式展示创意，主打搞笑，展示乐子。部分也会结合表情包表达情绪和抽象状况。且这类视频可能会带 #meme 的tag；也可通过非游戏形式展示二次创作内容/剧情，特指所有不依赖游戏画面，通过手绘、漫画、动画、影视、电影等风格进行的游戏内容二创，演绎游戏中的梗，或者是IP角色故事。可能存在无厘头创意剧情，如创意类动画、电影、手绘、漫画；一般含有对话剧情，可能使用AI生成技术

- **创意剪辑**：视频时长短的非高光视频，非创意整活类的短视频，视频中穿插搞笑片段、动画，或有频繁场景转换的游戏实况、多创意场景组合；视频通过非游戏核心玩法展示创意，而通过多场景展示创意。视频较短，有插入其他镜头行为，以及通过游戏内画面展示的创作内容

- **剧情剪辑**：为当前游戏剧情片段，非游戏实况内容，非二次创作及生活中发生的剧情

- **实况解说**：对于打游戏的视频，做一个配音解说（一般有语音及字幕对游戏进行解说）；解说可以是有专业性的解读操作、地图、游戏机制等等；也可以是包括趣味性、创意非常规式的解说，游戏实况的加上解说语音文字属于实况解说，创意整活类的解说视频归为实况解说

- **游戏世界观解读**：对游戏世界观进行解读，包括剧情、英雄、角色等背景故事的讲述

- **才艺展示**：歌曲改编翻唱、乐器演奏、舞蹈、真人肢体动作手势舞等各种才艺展示

- **直播切片**：包含主播真人镜头+直播游戏画面 或 主播直播的游戏画面+主播解说，可以说有主播就可以确定为直播切片；也是主打乐子，但更多是直播的切片，典型会有个主播脸的镜头展示表情和反馈。可以是知名主播的高光/趣味切片，也可以是普通koc的组队连麦乐子。视频中左下角或右下角插入了主播或主播操作的实拍镜头

- **真人演绎**：以真人实拍为主体，演绎剧情段子or游戏memes

- **颜值COS**：COSPLAY，且视频全部内容为cosplay，而非部分出现cosplay

- **明星娱乐**：娱乐明星出场

- **电竞相关**：电竞比赛

- **玩家故事**：与当前游戏画面内容无关。是玩家游戏生涯中某一段经历，与自己或身边真人相关的现实故事；通常有相关情感故事的提及和玩家日常生活相关的人生感悟或感想

- **幕后故事**：游戏的幕后故事

- **文化相关**：中国文化、历史文化和游戏剧情相结合

## 2. 内容节奏（必选一项）：快 / 中 / 慢

## 3. 是否有3秒钩子（必选一项）：有 / 无

## 4. 3秒钩子类型（必选一项）：

- **冲突反差**：用反常识、反认知的画面或台词打破预期，例如："0战备也能进航天绝密，0成本得吃"
- **利益前置**：直接抛出用户能获得的好处，戳中刚需痛点，例如："S16用这套冷门阵容，妥妥上分"
- **悬念提问**：用具体问题或悬念勾住用户，引导其继续观看找答案，例如："为什么一板一眼堪称最强海克斯科技，一分钟告诉你"
- **视觉冲击**：用夸张、吸睛的画面快速抓住注意力，例如精彩击杀等
- **无**：没有明显钩子

## 5. 核心叙事框架（必选一项）：

- **逆风翻盘**：逆袭爽感，结局前置+反差台词，节奏紧凑，关键操作清晰
- **搞笑整活**：反差出糗，反常识台词+搞笑画面，梗密集，节奏快
- **硬核攻略**：价值前置，痛点+收益（如"3招上王者"），步骤清晰，标注直观
- **剧情演绎**：人设冲突，角色台词+冲突画面，情感连贯，有代入感
- **高光集锦**：视觉冲击，最炸画面+高燃BGM，卡点精准，无尿点
- **悬念解谜**：好奇钩子，设问+线索画面，推理不拖沓，答案有惊喜
- **对比测评**：决策帮助，问题+对比预告，数据直观，结论明确
- **视觉吸睛**：视觉冲击强烈，以颜值相关内容为主

## 6. 内容视角（必选一项）：第一人称 / 第三人称

【输出格式要求】

请严格按以下格式输出，先输出详细分析，再输出标签汇总：

---

【详细分析】

1. **内容主题分类**：[分析视频属于哪种类型，结合上述定义说明判断依据]

2. **内容节奏**：[分析视频的整体节奏快慢，说明判断依据]

3. **是否有3秒钩子**：[分析视频开头3秒是否有吸引注意力的钩子，说明判断依据]

4. **3秒钩子类型**：[如果有钩子，分析属于哪种类型，说明判断依据]

5. **核心叙事框架**：[分析视频的核心叙事方式，结合上述框架定义说明判断依据]

6. **内容视角**：[分析视频的拍摄/录制视角，说明判断依据]

===标签汇总===
内容主题分类: [游戏实况与剪辑/游戏内资产展示/游戏攻略/游戏资讯/游戏测评/游戏宣传/创意整活与二创/创意剪辑/剧情剪辑/实况解说/游戏世界观解读/才艺展示/直播切片/真人演绎/颜值COS/明星娱乐/电竞相关/玩家故事/幕后故事/文化相关]
内容节奏: [快/中/慢]
是否有3秒钩子: [有/无]
3秒钩子类型: [冲突反差/利益前置/悬念提问/视觉冲击/无]
核心叙事框架: [逆风翻盘/搞笑整活/硬核攻略/剧情演绎/高光集锦/悬念解谜/对比测评/视觉吸睛]
内容视角: [第一人称/第三人称]
===标签结束==="""

TAG_FIELDS = (
    "内容主题分类",
    "内容节奏",
    "是否有3秒钩子",
    "3秒钩子类型",
    "核心叙事框架",
    "内容视角",
)

MIME_TYPES = {
    ".mp4": "video/mp4",
    ".avi": "video/x-msvideo",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".flv": "video/x-flv",
    ".wmv": "video/x-ms-wmv",
    ".webm": "video/webm",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


class VideoAnalyzer:
    """视频/图片内容理解分析器。

    :param api_key: 模型 API Key。``qwen`` 后端缺省时回退到 ``DASHSCOPE_API_KEY``。
    :param model: 模型名。默认 gemini 用 ``gemini-3-flash-preview``，
        qwen 用 ``qwen-vl-max``。
    :param model_provider: ``gemini``（默认）或 ``qwen``。
    :param prompt: 自定义提示词，默认使用内置的游戏视频六维分类提示词。
    :param api_base: ``gemini`` 后端的网关根地址，缺省时读取环境变量
        ``GEMINI_API_BASE``，再缺省时使用 Google 官方地址。
    """

    QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    GEMINI_DEFAULT_BASE = "https://generativelanguage.googleapis.com"

    def __init__(
        self,
        api_key: str = "",
        model: str = "",
        model_provider: str = "gemini",
        prompt: Optional[str] = None,
        api_base: str = "",
    ):
        self.model_provider = (model_provider or "gemini").lower()
        self.prompt = prompt or PROMPT_TEXT

        if self.model_provider == "qwen":
            self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY", "")
            self.model = model or "qwen-vl-max"
            self.api_base = ""
            self.api_url = ""
        else:
            self.api_key = api_key
            self.model = model or "gemini-3-flash-preview"
            self.api_base = (
                api_base
                or os.getenv("GEMINI_API_BASE", "")
                or self.GEMINI_DEFAULT_BASE
            ).rstrip("/")
            self.api_url = (
                f"{self.api_base}/v1beta/models/{self.model}:generateContent"
            )

    # ──────────────────────────────────────────────────────── 工具方法

    @staticmethod
    def encode_to_base64(path: str) -> Optional[str]:
        """把文件读成 Base64 字符串，失败返回 ``None``。"""
        try:
            with open(path, "rb") as handle:
                return base64.b64encode(handle.read()).decode("utf-8")
        except OSError:
            return None

    # 兼容旧名
    encode_video_to_base64 = encode_to_base64

    @staticmethod
    def get_mime_type(path: str) -> str:
        """按扩展名推断 MIME 类型。"""
        return MIME_TYPES.get(Path(path).suffix.lower(), "application/octet-stream")

    @staticmethod
    def parse_analysis_result(text: str) -> dict:
        """从模型回答里抽取 ``===标签汇总===`` 区块中的结构化标签。"""
        result: dict[str, str] = {key: "" for key in TAG_FIELDS}
        result["原始分析"] = text
        block = re.search(r"===标签汇总===(.*?)===标签结束===", text, re.DOTALL)
        if block:
            for line in block.group(1).splitlines():
                line = line.strip()
                if not line:
                    continue
                for key in TAG_FIELDS:
                    if line.startswith(key):
                        result[key] = re.sub(r"^[^:：]+[:：]\s*", "", line).strip()
                        break
        return result

    # ──────────────────────────────────────────────────────── 分析入口

    async def analyze(self, media_path: str) -> dict:
        """分析一个视频/图片文件，返回含标签字段与 ``原始分析`` 的字典。"""
        if not self.api_key:
            return {"原始分析": "Error: API Key 未配置"}

        mime_type = self.get_mime_type(media_path)
        if self.model_provider == "qwen":
            return await self._analyze_qwen(media_path, mime_type)

        media_data = self.encode_to_base64(media_path)
        if not media_data:
            return {"原始分析": "Error: 文件 Base64 编码失败"}
        return await self._analyze_gemini(media_data, mime_type)

    async def _analyze_gemini(self, media_data: str, mime_type: str) -> dict:
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"inlineData": {"mimeType": mime_type, "data": media_data}},
                        {"text": self.prompt},
                    ],
                }
            ]
        }
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                response = await client.post(
                    self.api_url,
                    headers={"Content-Type": "application/json"},
                    params={"key": self.api_key},
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
            candidates = data.get("candidates") or []
            if candidates:
                parts = (candidates[0].get("content") or {}).get("parts") or [{}]
                return self.parse_analysis_result(parts[0].get("text", ""))
            return {"原始分析": f"Error: Gemini API 返回异常 - {data}"}
        except Exception as exc:  # noqa: BLE001
            return {"原始分析": f"Error: Gemini 请求失败 - {exc}"}

    async def _analyze_qwen(self, media_path: str, mime_type: str) -> dict:
        """DashScope SDK 通路：``file://`` 直传本地文件，不受 Base64 体积限制。"""
        try:
            import dashscope
        except ImportError:
            return {"原始分析": "Error: 未安装 dashscope，请先 pip install dashscope"}

        dashscope.api_key = self.api_key
        media_key = "video" if mime_type.startswith("video/") else "image"
        file_uri = f"file://{Path(media_path).absolute().as_posix()}"
        messages = [
            {
                "role": "user",
                "content": [{media_key: file_uri}, {"text": self.prompt}],
            }
        ]

        def call_sdk():
            return dashscope.MultiModalConversation.call(
                model=self.model, messages=messages
            )

        try:
            response = None
            max_retries = 3
            for attempt in range(max_retries):
                response = await asyncio.to_thread(call_sdk)
                if response.status_code == 200:
                    break
                # 限流/服务端错误才退避重试，4xx 直接返回
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2**attempt)
                        continue
                break

            if response is not None and response.status_code == 200:
                text = ""
                for item in response.output.choices[0].message.content:
                    if isinstance(item, dict) and "text" in item:
                        text += item["text"]
                return self.parse_analysis_result(text)
            code = getattr(response, "status_code", "?")
            message = getattr(response, "message", "")
            return {"原始分析": f"Error: Qwen 状态异常 {code} - {message}"}
        except Exception as exc:  # noqa: BLE001
            return {"原始分析": f"Error: Qwen 请求失败 - {exc}"}
