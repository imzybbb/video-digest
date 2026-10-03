# video-digest · B站 / 抖音 视频解读流水线

把一条视频链接变成"解读卡"素材：**说了什么（语音转写）＋ 画面是什么（视觉理解）＋ 评论区风向 ＋ 弹幕高能**（按平台能力）。

```bash
# B站
python scripts/vd.py "https://www.bilibili.com/video/BVxxxxxxxxxx" --out ./vd_out
# 抖音（需先做一次性 cookie，见下）
python scripts/vd.py "https://v.douyin.com/xxxxxx/"
```

跑完打开 `vd_out/<id>/report.md`，或交给 AI Agent 自动生成一张自然语言"解读卡"（见 SKILL.md）。

## 支持矩阵

| 维度 | B站 | 抖音 |
| --- | --- | --- |
| 视频 / 图文下载 | ✅ yt-dlp | ✅ 视频＋图文（f2 引擎，需登录 cookie） |
| 音频转写 | ✅ faster-whisper | ✅ |
| 画面理解 | ✅ 抽帧 + 视觉模型 | ✅ 视频抽帧；图文直接逐图 |
| 评论区 | ✅ | ⏳ 后续版本 |
| 弹幕 | ✅ | ——（抖音无弹幕） |

实测例：B站一条 213 秒视频 → 50 段转写、12 帧画面描述、1200 条弹幕统计，全程本地运行、零 API 成本（约几分钟，视视频时长与硬件而定）。

## 安装

1. Python 3.10+，且 `ffmpeg` 在 PATH 中
2. `pip install -r requirements.txt`
3. 语音模型（约 1.5GB）三选一：
   - 自动：faster-whisper 首次运行会从 HuggingFace 下载 `large-v3-turbo`
   - 国内推荐：`bash scripts/get_model.sh`（走 ModelScope 镜像），然后 `--model ./models/faster-whisper-large-v3-turbo`
   - 或设置环境变量 `VD_MODEL=<模型名或路径>`
4. （可选）画面理解：安装 [Ollama](https://ollama.com) 并 `ollama pull qwen3-vl:8b`；没装 Ollama 时自动跳过该步骤

## 抖音的一次性准备（需要登录 cookie）

抖音的下载接口要求"新鲜登录 cookie"（未登录会直接失败），一次性准备：

1. 启动一个带调试端口的浏览器窗口（Windows 自带 Edge 即可），扫码登录抖音：
   ```bat
   msedge --remote-debugging-port=9222 --user-data-dir=%TEMP%\dy_profile https://www.douyin.com/
   ```
2. 登录完成后，另开终端运行：
   ```bash
   python scripts/get_dy_cookie.py
   ```
   会在仓库根目录生成 `dy_cookie.txt`（vd.py 自动读取）。
3. 之后正常使用：`python scripts/vd.py "https://v.douyin.com/xxxx/"`——也支持直接粘贴抖音 App「复制链接」的整段分享文字。

cookie 隔一段时间会过期，届时重跑第 2 步即可（浏览器保持登录态）。

## 用法

```bash
python scripts/vd.py <url或分享文案> [选项]

# 快速试跑：只取 B站视频前 2 分钟
python scripts/vd.py "https://www.bilibili.com/video/BVxxxx" --max-min 2

# 关闭画面理解
python scripts/vd.py "..." --no-vision
```

| 选项 | 默认 | 说明 |
| --- | --- | --- |
| `--out` | `./vd_out` | 输出根目录（环境变量 `VD_OUT`） |
| `--frames` | 12 | 抽帧数量（图文则是最多处理图片数） |
| `--lang` | zh | 转写语言 |
| `--max-min` | 0（全量） | B站：只取前 N 分钟 |
| `--model` | large-v3-turbo | whisper 模型名/路径（环境变量 `VD_MODEL`） |
| `--no-vision` | - | 跳过画面描述 |

## 输出结构

```
vd_out/<id>/
├── report.md          # 汇总（元数据/转写/画面/评论/弹幕），Agent 主要读这个
├── transcript.json    # 转写（分段 + 时间轴）
├── frame_desc.json    # 每帧/每图画面描述
├── video.mp4 / frames/
├── （B站）meta.json / comments.json / danmaku_stats.json / danmaku.xml
└── （抖音）dy_meta.json / f2/（原始下载目录）
```

重复运行会跳过已完成步骤（各步骤有缓存），中断后重跑不浪费。

## 踩过的坑（已内置修复）

- **faster-whisper × 新版 PyAV 不兼容**（`TypeError: metadata_errors`）：不把文件路径传入 transcribe，改为自行解码 wav 为 numpy 数组（也更快）。
- **VAD 误杀纯音乐视频**（0 段输出）：检测到空结果自动"去 VAD 重跑"。
- **B站评论 -352 风控**：先访问主站 + `x/frontend/finger/spi` 种 buvid3/buvid4 cookie 再请求；仍失败说明临时冷却，稍等重试。匿名状态下一般只能取到少量热门评论（够看风向）。
- **B站弹幕返回可能为 deflate 压缩**：已做多级解压探测。大热视频单次约 1200 条上限，用于风向统计足够。
- **抖音必须登录 cookie**：未登录时 f2 / yt-dlp 全部失败（"Fresh cookies needed"）；用上文调试口方案取一次即可；cookie 过期后重取。抖音评论接口风控签名较复杂，尚未接入（计划中）。
- **f2 日志里可能提示 Bark 通知失败（405）**：无害，可忽略。

## 给 AI Agent 用

`SKILL.md` 是为 AI Agent 准备的技能说明（Claude Code / Hermes 等可直接放进 skills 目录）。Agent 拿到视频链接后即可自动跑全流程，并按模板输出"解读卡"。

## 路线图

- [x] B站全链路（转写 / 画面 / 评论 / 弹幕）
- [x] 抖音视频＋图文（下载 / 转写 / 画面）
- [ ] 抖音评论 / YouTube 适配

## 免责声明

仅供个人学习与研究使用；请遵守目标平台的服务条款，控制请求频率，勿用于批量抓取或商业用途。

## License

MIT
