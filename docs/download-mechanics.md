# 下载机制详解

本文档说明批量下载作品时的并发、休眠与清晰度选档行为，便于判断"卡住"是正常等待还是异常。

通路划分：

| 平台 / 模式 | 实现位置 | 是否需要 cookie | 是否依赖 TikTokDownloader 仓库 |
| --- | --- | --- | --- |
| 抖音 `share`（默认） | `scripts/douyin_share_download.py` | 不需要 | 不依赖 |
| 快手 | `scripts/platform_download.py` 内置 `KuaishouDownload` | 不需要 | 不依赖 |
| 抖音 `api` | 复用仓库 `src` 包 | 需要（含完整 UIFID） | 依赖 |
| TikTok | 复用仓库 `src` 包 | 需要 | 依赖 |

## 一、清晰度选档机制（默认最低清晰度）

接口返回的"默认直链"既不是最高清档、也不一定是最低清档，两个平台都需要主动遍历档位列表。

本项目 **默认 `--quality lowest`（最低清晰度）**，原因：

1. 省流量、省存储，批量几百条作品时体积差距可达 5~10 倍；
2. 送多模态模型分析时上传更快，也更不容易触碰接口的单文件体积限制；
3. 内容理解类分析（主题、节奏、钩子、叙事框架）对画质不敏感。

需要原画时显式传 `--quality highest`。

### 排序键与"未知档"陷阱

两个平台的排序键结构一致：

```
(编码偏好, 宽×高, 码率, data_size)
```

* `--quality highest`：各指标取原值，倒序排列 → 选最大；
* `--quality lowest`：各指标**取负值**后倒序排列 → 等价于升序，选最小。

**陷阱**：部分候选节点（如抖音顶层 `download_addr`）不返回 `width` / `height` / `bit_rate`，缺失时为 `0`。若直接按 `0` 参与升序排序，这些"未知档"会被误判成最低清晰度。脚本用统一的归一化函数处理：

```python
UNKNOWN_METRIC = 1 << 40

def quality_metric(value, highest):
    if value:
        return value
    return 0 if highest else UNKNOWN_METRIC
```

即：最高档模式下未知指标当 `0`（排末位），最低档模式下未知指标当极大值（同样排末位）。两种模式下未知档都只作为兜底，不会被优先选中。

### 抖音候选来源

`aweme_details[].video` 节点：

| 字段 | 常见分辨率 | 说明 |
| --- | --- | --- |
| `play_addr_h264` | 1024x576 | 名字像主直链，实际常是 540p H.264 兜底档 |
| `play_addr` | 1024x576 | 同上 |
| `play_addr_265` | 1024x576 | HEVC 版兜底档 |
| `download_addr` | 不定 | 部分作品带水印，常缺分辨率字段 |
| `bit_rate[].play_addr` | 540p ~ 1080p 多档 | **完整档位列表在这里** |

`bit_rate[]` 每个元素的关键字段：

```json
{
  "gear_name": "720_1_1",
  "bit_rate": 1894046,
  "is_h265": 1,
  "is_bytevc1": 1,
  "FPS": 30,
  "quality_type": 2,
  "play_addr": { "width": 1280, "height": 720, "data_size": 8829413, "url_list": ["..."] }
}
```

`pick_video_urls()` 负责选档，签名为 `pick_video_urls(video, codec="any", quality="lowest")`。

### 快手候选来源

`window.INIT_STATE`（键名做过 ASCII +1 位移，脚本内 `decode_kuaishou_key()` 还原）：

| 路径 | 常见分辨率 | 说明 |
| --- | --- | --- |
| `mainMvUrls[].url` | 960x720 | 默认档，单条直链 |
| `manifest.adaptationSet[].representation[]` | 540p ~ 1080p 多档 | **完整档位列表在这里** |

`representation[]` 的关键字段：

```json
{
  "qualityType": "1080p",
  "qualityLabel": "1080p",
  "width": 1440,
  "height": 1080,
  "avgBitrate": 2145000,
  "maxBitrate": 3500000,
  "videoCodec": "hevc",
  "url": "https://...",
  "backupUrl": ["https://..."]
}
```

`pick_kuaishou_urls()` 的排序键为 `(编码偏好, 宽×高, avgBitrate)`。`manifest` 在 `INIT_STATE` 里的位置不固定，脚本依次尝试 `node.manifest` → 全树搜 `manifest` → 全树搜 `adaptationSet` → 全树搜 `representation`。

### 编码偏好 `--codec`

`--codec h264` 时，H.264 / AVC 档位在排序键首位 `+1`，即**编码偏好的优先级高于清晰度**。用途是规避部分多模态模型无法解码 HEVC/H.265 的问题。

实测限制：某些作品 `bit_rate` 里的所有档位都是 HEVC，唯一的 H.264 来源就是顶层 `play_addr_h264`。此时 `--codec h264` 只能拿到那一档，这是接口限制而非脚本问题。

### 兜底策略

两个平台都把**所有档位的直链按排序后顺序拼成一个列表**（去重保序），下载时依次尝试：

1. 首选档的 `url_list` / `url` + `backupUrl`；
2. 次选档、再次选档……；
3. 快手额外把 `mainMvUrls` 追加在最末位。

因此首选档 CDN 失效时会自动降级，不会整条作品失败。日志打印的分辨率是**选中的首选档**，而非实际落盘的那一条——若发生降级，实际文件可能与日志值不同，用 `ffprobe` 可核对：

```bash
ffprobe -v error -select_streams v:0 -show_entries stream=width,height,codec_name -of csv=p=0 <文件>.mp4
```

## 二、并发模型

### 编排层：`download_videos.py`

`--concurrency` 是**总并发**，按实际出现链接的平台数均分，余数补给靠前的平台：

| 链接涉及平台 | 默认 20 的分配 |
| --- | --- |
| 仅抖音 | 抖音 20 |
| 抖音 + TikTok | 10 + 10 |
| 抖音 + TikTok + 快手 | 7 + 7 + 6 |

每个平台由独立的 `platform_download.py` 子进程负责。这样设计是因为仓库 `src` 包的 `Downloader.semaphore` 是**类属性**，同进程内无法按平台隔离。

### 抖音 share 模式

| 项 | 说明 |
| --- | --- |
| 取数据 | `slidesinfo` 接口支持批量，每次最多 10 个 ID（`api_batch`） |
| 文件下载 | `ThreadPoolExecutor(max_workers=--concurrency)` |
| 休眠 | 无随机休眠；仅失败重试时 `retry_delay * attempt` 秒 |
| cookie | 不需要 |

### 快手内置实现

| 项 | 说明 |
| --- | --- |
| 并发控制 | `ThreadPoolExecutor(max_workers=--concurrency)` |
| 取数据 | 每个作品 1 次请求（移动端作品页），无随机休眠 |
| 重试 | 失败重试 `--retry` 次，间隔 `retry_delay * attempt` 秒，并轮换备用域名 |
| 文件下载 | `httpx` 流式写入 `.part`，完成后改名；小于 10 KB 视为失败 |
| 依赖 | 仅 `httpx` |

### 抖音 api / TikTok（依赖仓库 src 包）

两层并发：

| 层 | 控制点 | 默认值 | 作用 |
| --- | --- | --- | --- |
| 取数据并发 | `--api-concurrency`（`asyncio.Semaphore`） | 4 | 调用作品详情接口，过高会被风控 |
| 文件下载并发 | `Downloader.semaphore` | 由 `--concurrency` 覆盖 | 同时进行的 HTTP 流式下载数 |

休眠点：

1. `src/custom/function.py` 的 `wait()` —— 每次接口请求后随机 `0.5 ~ 2.0` 秒，注释已明确"仅对获取数据生效，不影响下载文件"；
2. 重试休眠 —— 取数据失败后 `asyncio.sleep(retry_delay * attempt)`；
3. `suspend()` 批量节流（每 10 个账号/合集暂停 5 分钟）—— **详情批量下载模式不会触发**。

因此抖音 share / 快手在同等并发下接口节奏明显快于抖音 api / TikTok。

## 三、断点续传

| 通路 | 跳过判定 |
| --- | --- |
| 抖音 share | 输出目录存在 `<作品ID>` 前缀的非空文件 |
| 快手 | 输出目录存在 `<photoId>.mp4` 且大于 10 KB |
| 抖音 api / TikTok | 输出目录中文件名前缀为 19 位数字且文件非空；另有 `cache/` 临时文件配合 `Range: bytes=` 续传，416 响应视为缓存异常并重下 |

`--no-skip-existing` 可关闭跳过逻辑。

**注意**：换清晰度或换编码重新下载时，必须先删除已有文件，否则会被断点续传跳过。

## 四、调优建议

| 目标 | 做法 |
| --- | --- |
| 提高下载吞吐 | 增大 `--concurrency`（20 → 40），注意磁盘与带宽 |
| 降低风控风险 | 减小 `--api-concurrency`，增大 `--retry-delay` |
| 进一步省流量 | 保持默认 `--quality lowest`，并限制 `--limit` 分批跑 |
| 需要原画 | `--quality highest`（先清空旧文件） |
| 分析报错 / 画面无关 | `--codec h264` 重新下载 |
