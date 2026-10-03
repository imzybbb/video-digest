# video-digest · B站视频解读流水线

把一条视频链接变成"解读卡"素材：**说了什么（语音转写）＋ 画面是什么（视觉理解）＋ 评论区风向 ＋ 弹幕高能**。

```bash
python scripts/vd.py "https://www.bilibili.com/video/BVxxxxxxxxxx" --out ./vd_out
```

跑完打开 `vd_out/<BV号>/report.md`，或交给 AI Agent 自动生成一张自然语言"解读卡"（见 SKILL.md）。

## 它读取什么

| 维度 | 来源 | 说明 |
| --- | --- | --- |
| 音频内容 | faster-whisper 语音转写 | 本地运行，中文效果好，带时间轴 |
| 视频画面 | ffmpeg 抽帧 + Ollama 视觉模型 | 每帧 1-2 句描述（可选步骤） |
| 评论区 | B站接口 | 热门评论（按赞排序） |
| 弹幕 | B站弹幕接口 | 总数 / 刷屏榜 / 最热分钟段 |

实测例：Never Gonna Give You Up（213 秒）→ 50 段转写、12 帧画面描述、1200 条弹幕统计，全程本地运行、零 API 成本（约几分钟，视视频时长与硬件而定）。

## 安装

1. Python 3.10+，且 `ffmpeg` 在 PATH 中
2. `pip install -r requirements.txt`
3. 语音模型（约 1.5GB）三选一：
   - 自动：faster-whisper 首次运行会从 HuggingFace 下载 `large-v3-turbo`
   - 国内推荐：`bash scripts/get_model.sh`（走 ModelScope 镜像），然后 `--model ./models/faster-whisper-large-v3-turbo`
   - 或设置环境变量 `VD_MODEL=<模型名或路径>`
4. （可选）画面理解：安装 [Ollama](https://ollama.com) 并 `ollama pull qwen3-vl:8b`；没装 Ollama 时自动跳过该步骤

## 用法

```bash
python scripts/vd.py <url> [选项]

# 快速试跑：只取前 2 分钟
python scripts/vd.py "https://www.bilibili.com/video/BVxxxx" --max-min 2

# 关闭画面理解
python scripts/vd.py "..." --no-vision
```

| 选项 | 默认 | 说明 |
| --- | --- | --- |
| `--out` | `./vd_out` | 输出根目录（环境变量 `VD_OUT`） |
| `--frames` | 12 | 抽帧数量 |
| `--lang` | zh | 转写语言 |
| `--max-min` | 0（全量） | 只取前 N 分钟 |
| `--model` | large-v3-turbo | whisper 模型名/路径（环境变量 `VD_MODEL`） |
| `--no-vision` | - | 跳过画面描述 |
| `--no-dl` | - | 跳过下载（需目录里已有 video.mp4） |

## 输出结构

```
vd_out/<BV号>/
├── report.md          # 汇总（元数据/转写/画面/评论/弹幕），Agent 主要读这个
├── meta.json          # 视频元数据
├── transcript.json    # 转写（分段 + 时间轴）
├── frame_desc.json    # 每帧画面描述
├── comments.json      # 热门评论
├── danmaku_stats.json # 弹幕统计
├── danmaku.xml        # 弹幕原文
├── video.mp4 / audio.wav / frames/
```

重复运行会跳过已完成步骤（各步骤有缓存），中断后重跑不浪费。

## 踩过的坑（已内置修复）

- **faster-whisper × 新版 PyAV 不兼容**（`TypeError: metadata_errors`）：不把文件路径传入 transcribe，改为自行解码 wav 为 numpy 数组（也更快）。
- **VAD 误杀纯音乐视频**（0 段输出）：检测到空结果自动"去 VAD 重跑"。
- **评论区 -352 风控**：先访问主站 + `x/frontend/finger/spi` 种 buvid3/buvid4 cookie 再请求；仍失败说明临时冷却，稍等重试。匿名状态下一般只能取到少量热门评论（够看风向）。
- **弹幕返回可能为 deflate 压缩**：已做多级解压探测。大热视频单次约 1200 条上限，用于风向统计足够。

## 给 AI Agent 用

`SKILL.md` 是为 AI Agent 准备的技能说明（Claude Code / Hermes 等可直接放进 skills 目录）。Agent 拿到视频链接后即可自动跑全流程，并按模板输出"解读卡"。

## 路线图

- [x] B站全链路（转写 / 画面 / 评论 / 弹幕）
- [ ] 抖音（专用下载器 + 评论风控适配；抖音无弹幕）
- [ ] YouTube（yt-dlp 下载 + 评论/字幕适配）

## 免责声明

仅供个人学习与研究使用；请遵守目标平台的服务条款，控制请求频率，勿用于批量抓取或商业用途。

## License

MIT
