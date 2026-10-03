---
name: video-digest
description: "Use when 用户发来视频链接(B站/抖音)要解读——转写+画面+评论/弹幕→解读卡。"
---

# 视频解读（vd）流水线

## 触发
用户发来视频链接 / 分享卡片 / 分享文案（B站 BV号、b23.tv；抖音 v.douyin.com、douyin.com/video|note），说“看看这个 / 解读一下 / 讲讲这个视频”。

## 一条命令
```bash
export PYTHONIOENCODING=utf-8   # Windows 建议
python scripts/vd.py "<url或分享文案>" --out ./vd_out
```
抖音需先一次性 cookie（见 README「抖音的一次性准备」；`python scripts/get_dy_cookie.py` 生成 dy_cookie.txt）。
可选参数：`--frames 12`｜`--max-min N`（B站，只取前 N 分钟）｜`--lang zh`｜`--model <whisper模型>`｜`--no-vision`。

产出目录 `./vd_out/<id>/`：`report.md`（总素材）、`transcript.json`、`frame_desc.json`、（B站）`comments.json`、`danmaku_stats.json`、`video.mp4`、`frames/`。
重复跑会自动跳过已完成步骤；中断后重跑不浪费。

## 交付格式（解读卡）
读 `report.md` 后编写，分点、口语化：
1. **一句话**：这视频讲了啥 / 什么类型；
2. **内容要点**：按时间轴提炼（长视频给关键时间点，别全量堆）；
3. **关键画面**：从画面描述挑 3-5 个亮点（图文则挑图内重点）；
4. **评论区风向**：热评 + 整体态度（B站；抖音暂无）；
5. **弹幕高能**：刷屏梗 / 名场面（B站）；
6. 有笑点 / 梗点破一下。长视频先给摘要，问用户要不要展开。

## 依赖（首次使用时确认）
- Python 3.10+、`ffmpeg`（在 PATH 中）
- `pip install -r requirements.txt`（含 f2：抖音下载引擎）
- whisper 模型（约 1.5GB）：HF 自动下载 ／ 国内跑 `bash scripts/get_model.sh` 后 `--model` 指定 ／ `VD_MODEL` 环境变量
- （可选）Ollama + 视觉模型：默认找 `qwen3-vl:8b`，找不到时自动选名字含 "vl" 的模型；Ollama 没运行时自动跳过画面部分
- 抖音：登录 cookie（`get_dy_cookie.py`；环境变量 `VD_DY_COOKIE` 可改路径）

## 关键坑（已内置处理，勿重犯）
- **faster-whisper×PyAV 不兼容**：传文件路径会炸（`av.open(metadata_errors=)` TypeError）→ 已改：自行 read wav → numpy float32 传入 transcribe。
- **VAD 会误杀纯音乐视频**（0 段）→ 已内置“0 段自动去 VAD 重跑”。
- **评论 -352 风控**：Session 先访 `bilibili.com/` + `x/frontend/finger/spi` 种 buvid3/buvid4；仍 -352 = 临时冷却，等几分钟重试。匿名一般只有几条热评（够用）。
- **弹幕**：`comment.bilibili.com/{cid}.xml`（可能 deflate，需 zlib 试解压）；大热视频单批 ~1200 条，够做风向。
- **抖音必须登录 cookie**（未登录 f2/yt-dlp 全失败）；抖音无弹幕；评论未接入；f2 的 Bark 通知 405 无害忽略。
- **多P视频**：默认 P1，URL 带 `?p=N` 取对应分P。
- Windows 跑脚本建议带 `PYTHONIOENCODING=utf-8`；路径参数用本地风格。

## 平台状态
- **B站：✅ 全链路**（2026-10 实测：转写 / 画面 / 评论 / 弹幕）。
- **抖音：✅ 视频＋图文**（下载 / 转写 / 画面；需 cookie）；评论 ⏳ 后续。
- **YouTube：⏳ 计划中**（yt-dlp 可下；评论/字幕另接）。

## 验证
跑完检查 `report.md`：视频转写段数>0、画面描述条数≈抽帧数（图文=图片数）、评论≥1（B站）、弹幕 total≥0（部分视频关闭弹幕）。
