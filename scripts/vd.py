# -*- coding: utf-8 -*-
"""vd.py — 视频解读流水线（B站 + 抖音）

把一条视频链接变成结构化"解读卡"素材：
  平台识别 -> 下载 -> 语音转写(faster-whisper) -> 抽帧/图文视觉描述(Ollama 可选) ->
  (B站)评论+弹幕 -> report.md

用法:
  python vd.py <url或分享文案> [--out DIR] [--frames 12] [--lang zh] [--max-min 0]
                [--model <whisper模型名或路径>] [--no-vision]

环境变量:
  VD_OUT          输出根目录（默认 ./vd_out）
  VD_MODEL        whisper 模型名或路径（默认 large-v3-turbo）
  VD_VISION_MODEL Ollama 视觉模型名（默认 qwen3-vl:8b，找不到时自动挑含 "vl" 的）
  VD_OLLAMA       Ollama 地址（默认 http://127.0.0.1:11434）
  VD_DY_COOKIE    抖音 cookie 文件路径（默认 <repo>/dy_cookie.txt，见 get_dy_cookie.py）

抖音说明：下载走 f2 引擎，必须提供登录 cookie（见 README「抖音的一次性准备」）。
"""
import argparse, base64, glob, json, os, re, shutil, subprocess, sys, time, zlib
import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
      "Referer": "https://www.bilibili.com/"}
MOBILE_UA = {"User-Agent": "Mozilla/5.0 (Linux; Android 11; SAMSUNG SM-G973U) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/14.2 Chrome/87.0.4280.141 Mobile Safari/537.36"}
PY = sys.executable
MODEL = os.environ.get("VD_MODEL", "")
OLLAMA = os.environ.get("VD_OLLAMA", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("VD_VISION_MODEL", "qwen3-vl:8b")
WORK = os.environ.get("VD_OUT", "vd_out")
DY_COOKIE = os.environ.get("VD_DY_COOKIE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dy_cookie.txt"))


def log(*a):
    print("[vd]", *a, flush=True)


def jdump(obj, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


# ================= B站 =================

def resolve_url(url):
    if "b23.tv" in url:
        r = requests.get(url, headers=UA, allow_redirects=True, timeout=25)
        url = r.url
    return url


def parse_ids(url):
    m = re.search(r"(BV[0-9A-Za-z]{10})", url)
    bvid = m.group(1) if m else None
    av = re.search(r"av(\d+)", url)
    p = re.search(r"[?&]p=(\d+)", url)
    return bvid, (av.group(1) if av else None), (int(p.group(1)) if p else 1)


def get_meta(bvid, av):
    if bvid:
        u = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    else:
        u = f"https://api.bilibili.com/x/web-interface/view?aid={av}"
    d = requests.get(u, headers=UA, timeout=25).json()
    if d.get("code") != 0:
        raise SystemExit(f"获取元数据失败: {d}")
    return d["data"]


def download(url, outdir, max_min=0):
    vp = os.path.join(outdir, "video.mp4")
    if os.path.exists(vp) and os.path.getsize(vp) > 1024:
        log("视频已存在，跳过下载")
        return vp
    cmd = [PY, "-m", "yt_dlp", "-f", "bv*+ba/b", "--merge-output-format", "mp4",
           "-o", vp, "--no-playlist", "--no-warnings"]
    if max_min > 0:
        cmd += ["--download-sections", f"*0:00-{max_min}:00", "--force-keyframes-at-cuts"]
    cmd.append(url)
    log("开始下载 ...")
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        log("yt-dlp stderr:", (r.stderr or "")[-500:])
    if not os.path.exists(vp):
        raise SystemExit("下载失败")
    return vp


def extract_audio(video, outdir):
    ap = os.path.join(outdir, "audio.wav")
    if os.path.exists(ap) and os.path.getsize(ap) > 1024:
        return ap
    subprocess.run(["ffmpeg", "-y", "-i", video, "-vn", "-ac", "1", "-ar", "16000",
                    "-c:a", "pcm_s16le", ap], capture_output=True)
    return ap


def transcribe(audio, outdir, model_src="", lang="zh"):
    tj = os.path.join(outdir, "transcript.json")
    if os.path.exists(tj):
        log("转写已存在，跳过")
        return json.load(open(tj, encoding="utf-8"))
    import wave
    import numpy as np
    from faster_whisper import WhisperModel
    # 注意：不直接给 transcribe 传文件路径——faster-whisper 新版与 PyAV 存在
    # "metadata_errors" 兼容性中断，统一改为自行解码为 numpy 数组传入。
    w = wave.open(audio, "rb")
    fr = w.getframerate()
    ch = w.getnchannels()
    arr = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    w.close()
    if ch > 1:
        arr = arr.reshape(-1, ch).mean(axis=1)
    if fr != 16000:
        raise SystemExit(f"音频采样率异常: {fr}")
    model_src = model_src or "large-v3-turbo"
    last_err = None
    for dev, ct in (("cuda", "float16"), ("cpu", "int8")):
        try:
            log(f"ASR 尝试 device={dev} ...")
            model = WhisperModel(model_src, device=dev, compute_type=ct)
            segs_iter, info = model.transcribe(arr, language=lang, vad_filter=True, beam_size=5)
            segs = [{"t": round(s.start, 1), "text": s.text.strip()} for s in segs_iter]
            if not segs:
                log("VAD 结果为空（可能是纯音乐），去 VAD 重跑 ...")
                segs_iter, info = model.transcribe(arr, language=lang, vad_filter=False, beam_size=5)
                segs = [{"t": round(s.start, 1), "text": s.text.strip()} for s in segs_iter]
            full = "".join(s["text"] if s["text"].startswith(("，", "。", "？", "！")) else s["text"] + " " for s in segs).strip()
            data = {"lang": info.language, "dur": round(info.duration, 1), "segments": segs, "text": full}
            jdump(data, tj)
            log(f"ASR 完成: {len(segs)} 段 / {data['dur']}s")
            return data
        except Exception as e:
            last_err = e
            log(f"ASR device={dev} 失败: {repr(e)[:200]}")
    raise SystemExit(f"ASR 全部失败: {last_err}")


def extract_frames(video, outdir, n=12):
    fdir = os.path.join(outdir, "frames")
    os.makedirs(fdir, exist_ok=True)
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nk=1:nw=1", video], capture_output=True, text=True)
    try:
        dur = float((r.stdout or "0").strip() or 0)
    except Exception:
        dur = 0
    if dur <= 0:
        dur = 60
    frames = []
    for i in range(n):
        t = dur * (i + 0.5) / n
        fp = os.path.join(fdir, f"f{i + 1:02d}.jpg")
        if not os.path.exists(fp):
            subprocess.run(["ffmpeg", "-y", "-ss", f"{t:.1f}", "-i", video,
                            "-frames:v", "1", "-q:v", "3", fp], capture_output=True)
        if os.path.exists(fp) and os.path.getsize(fp) > 512:
            frames.append((round(t, 1), fp))
    log(f"抽帧完成: {len(frames)} 张")
    return frames


_started_ollama = [False]   # 记录 Ollama 是否由本脚本拉起（列表以便函数内修改）


def _ensure_ollama():
    """确保 Ollama 在运行（不在则尝试拉起）"""
    try:
        requests.get(f"{OLLAMA}/api/tags", timeout=4)
        return True
    except Exception:
        pass
    log("Ollama 未运行，尝试自动拉起 ...")
    try:
        flags = 0x08000000 if os.name == "nt" else 0
        subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=flags)
    except Exception as e:
        log("拉起 Ollama 失败:", repr(e)[:100])
        return False
    for _ in range(20):
        time.sleep(1)
        try:
            requests.get(f"{OLLAMA}/api/tags", timeout=3)
            _started_ollama[0] = True
            log("Ollama 已拉起 ✓")
            return True
        except Exception:
            pass
    log("Ollama 拉起超时（20s）")
    return False


def _shutdown_ollama():
    """任务结束：仅停本脚本拉起的 Ollama，保持"不用就停" """
    if not _started_ollama[0]:
        return
    log("任务完成，停掉本脚本拉起的 Ollama ...")
    for img in ("ollama app.exe", "ollama.exe", "ollama_llama_server.exe"):
        try:
            subprocess.run(["taskkill", "/IM", img, "/F"], capture_output=True, timeout=15)
        except Exception:
            pass
    _started_ollama[0] = False


def vision(frames, outdir, enabled=True, prompt=None):
    vj = os.path.join(outdir, "frame_desc.json")
    if os.path.exists(vj):
        try:
            prev = json.load(open(vj, encoding="utf-8"))
        except Exception:
            prev = []
        if prev:
            log("视觉描述已存在，跳过")
            return prev
    out = []
    if not enabled:
        jdump(out, vj)
        return out
    ollama_ok = False
    names = []
    try:
        if _ensure_ollama():
            tags = requests.get(f"{OLLAMA}/api/tags", timeout=5).json()
            names = [m["name"] for m in tags.get("models", [])]
            ollama_ok = True
            log("Ollama 模型:", ", ".join(names[:6]))
        else:
            log("Ollama 不可用，跳过画面描述")
    except Exception as e:
        log("Ollama 状态异常，跳过画面描述:", repr(e)[:100])
    if ollama_ok:
        use_model = OLLAMA_MODEL
        if use_model not in names:
            cand = [n for n in names if "vl" in n.lower()] or names
            use_model = cand[0] if cand else use_model
        for t, fp in frames:
            try:
                b64 = base64.b64encode(open(fp, "rb").read()).decode()
                resp = requests.post(f"{OLLAMA}/api/generate", json={
                    "model": use_model,
                    "prompt": prompt or "这是一段视频的截图。用1-2句中文描述画面内容（人物/物体/场景/动作）；如果画面里有字幕或文字，原文照录。",
                    "images": [b64], "stream": False,
                    "options": {"num_ctx": 16384, "num_predict": 400}}, timeout=180).json()
                if resp.get("error"):
                    raise RuntimeError(str(resp["error"])[:140])
                out.append({"t": t, "desc": (resp.get("response") or "").strip()[:400]})
                log(f"视觉 {t}s ok")
            except Exception as e:
                out.append({"t": t, "desc": f"[失败] {repr(e)[:80]}"})
                log(f"视觉 {t}s 失败", repr(e)[:120])
    if ollama_ok:
        jdump(out, vj)
        try:  # 用完立刻卸载模型（不占显存/内存）
            requests.post(f"{OLLAMA}/api/generate", json={"model": use_model, "keep_alive": 0}, timeout=10)
            log("视觉模型已卸载（不用即释放）")
        except Exception:
            pass
    return out


def get_comments(aid, outdir):
    cj = os.path.join(outdir, "comments.json")
    if os.path.exists(cj):
        prev = json.load(open(cj, encoding="utf-8"))
        if prev:
            log("评论已存在，跳过")
            return prev
    s = requests.Session()
    s.headers.update(UA)
    try:
        s.get("https://www.bilibili.com/", timeout=20)
        spi = s.get("https://api.bilibili.com/x/frontend/finger/spi", timeout=20).json()
        d = spi.get("data") or {}
        if d.get("b_3"):
            s.cookies.set("buvid3", d["b_3"])
        if d.get("b_4"):
            s.cookies.set("buvid4", d["b_4"])
        log("cookie 预置完成:", bool(d.get("b_3")))
    except Exception as e:
        log("cookie 预置失败", repr(e)[:100])
    got = []
    urls = [
        f"https://api.bilibili.com/x/v2/reply/main?type=1&oid={aid}&mode=3&plat=1",
        f"https://api.bilibili.com/x/v2/reply/main?type=1&oid={aid}&mode=2&plat=1",
        f"https://api.bilibili.com/x/v2/reply?type=1&oid={aid}&sort=2&ps=20&pn=1",
    ]
    for u in urls:
        try:
            r = s.get(u, timeout=25).json()
            log("评论接口 ->", r.get("code"), u.split("?")[1][:46])
            replies = ((r.get("data") or {}).get("replies") or [])
            for it in replies:
                got.append({
                    "user": (it.get("member") or {}).get("uname"),
                    "content": ((it.get("content") or {}).get("message") or "")[:300],
                    "like": it.get("like", 0),
                    "rcount": it.get("rcount", 0),
                })
            if got:
                break
        except Exception as e:
            log("评论异常", repr(e)[:100])
    got.sort(key=lambda x: -(x.get("like") or 0))
    jdump(got, cj)
    log(f"评论获取: {len(got)} 条")
    return got


def get_danmaku(cid, outdir):
    dj = os.path.join(outdir, "danmaku_stats.json")
    djx = os.path.join(outdir, "danmaku.xml")
    if os.path.exists(dj):
        log("弹幕已存在，跳过")
        return json.load(open(dj, encoding="utf-8"))
    text = None
    for u in (f"https://comment.bilibili.com/{cid}.xml",
              f"https://api.bilibili.com/x/v1/dm/list.so?oid={cid}"):
        try:
            raw = requests.get(u, headers=UA, timeout=30).content
            for dec in ("raw", "zlib", "deflate"):
                try:
                    if dec == "raw":
                        t = raw.decode("utf-8")
                    elif dec == "zlib":
                        t = zlib.decompress(raw).decode("utf-8")
                    else:
                        t = zlib.decompress(raw, -15).decode("utf-8")
                    if "<d " in t:
                        text = t
                        break
                except Exception:
                    continue
            if text:
                break
        except Exception as e:
            log("弹幕接口失败", u[:60], repr(e)[:100])
    if not text:
        log("弹幕获取失败")
        jdump({"total": 0, "top": [], "hot_min": []}, dj)
        return json.load(open(dj, encoding="utf-8"))
    with open(djx, "w", encoding="utf-8") as f:
        f.write(text)
    items = re.findall(r'<d p="([^"]+)">([^<]*)</d>', text)
    from collections import Counter
    texts = [t2 for _, t2 in items]
    cnt = Counter(texts)
    mins = Counter()
    for p, _ in items:
        try:
            sec = float(p.split(",")[0])
            mins[int(sec // 60)] += 1
        except Exception:
            pass
    data = {
        "total": len(items),
        "top": [{"text": k, "n": v} for k, v in cnt.most_common(40) if v >= 2],
        "hot_min": [{"min": m, "n": n} for m, n in mins.most_common(15)],
    }
    jdump(data, dj)
    log(f"弹幕统计: 共 {len(items)} 条")
    return data


def build_report(meta, page, outdir, tr, frames, vd, comments, danmaku):
    lines = []
    d = meta
    lines.append(f"# 视频解读素材：{d.get('title')}")
    lines.append(f"- 作者: {(d.get('owner') or {}).get('name')} | 时长: {d.get('duration')}s | 播放: {(d.get('stat') or {}).get('view')} | 点赞: {(d.get('stat') or {}).get('like')}")
    lines.append(f"- 链接: https://www.bilibili.com/video/{d.get('bvid')}" + (f"?p={page}" if page > 1 else ""))
    desc = (d.get("desc") or "").strip()
    if desc:
        lines.append(f"- 简介: {desc[:500]}")
    lines.append("")
    lines.append("## 音频转写（语音内容）")
    if tr:
        for s in tr["segments"]:
            mm, ss = divmod(int(s["t"]), 60)
            lines.append(f"[{mm:02d}:{ss:02d}] {s['text']}")
    lines.append("")
    lines.append("## 画面描述（抽帧）")
    for v in vd:
        mm, ss = divmod(int(v["t"]), 60)
        lines.append(f"[{mm:02d}:{ss:02d}] {v['desc']}")
    lines.append("")
    lines.append("## 热门评论（按赞）")
    for c in comments[:25]:
        lines.append(f"- (👍{c['like']}) {c['user']}: {c['content']}")
    lines.append("")
    lines.append("## 弹幕风向")
    lines.append(f"- 弹幕总数: {danmaku['total']}")
    lines.append("- 刷屏最多的:" + " / ".join(f"{t['text']}(x{t['n']})" for t in danmaku["top"][:15]))
    lines.append("- 最热分钟段: " + " / ".join(f"{m['min']}min({m['n']}条)" for m in danmaku["hot_min"][:8]))
    rp = os.path.join(outdir, "report.md")
    with open(rp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log("报告生成:", rp)
    return rp


# ================= 抖音（f2 引擎）=================

def find_douyin_url(text):
    for m in re.finditer(r"https?://[^\s\u4e00-\u9fff\"'<>]+", text or ""):
        u = m.group(0).rstrip("，。！？,.;)）】]")
        if "douyin" in u:
            return u
    m = re.search(r"(v\.douyin\.com/[A-Za-z0-9_\-]+)", text or "")
    if m:
        return "https://" + m.group(1)
    return None


def resolve_dy(url):
    final = url
    try:
        r = requests.get(url, headers=MOBILE_UA, allow_redirects=True, timeout=20)
        final = r.url
    except Exception as e:
        log("短链解析异常（用原链继续）:", repr(e)[:80])
    m = re.search(r"/(?:video|note|slides)/(\d+)", final) or re.search(r"/(\d{15,})", final)
    aid = m.group(1) if m else None
    typ = "note" if ("/note/" in final or "/slides/" in final) else "video"
    return final, aid, typ


def _dy_media(f2dir):
    mp4 = glob.glob(os.path.join(f2dir, "**", "*.mp4"), recursive=True)
    mp4.sort(key=os.path.getsize, reverse=True)
    imgs = [p for p in glob.glob(os.path.join(f2dir, "**", "*"), recursive=True)
            if os.path.splitext(p)[1].lower() in (".jpg", ".jpeg", ".png", ".webp")]
    imgs.sort()
    return mp4, imgs


def fetch_douyin(url, outdir):
    f2dir = os.path.join(outdir, "f2")
    os.makedirs(f2dir, exist_ok=True)
    mp4, imgs = _dy_media(f2dir)
    if not mp4 and not imgs:
        cookie = ""
        if os.path.exists(DY_COOKIE):
            cookie = open(DY_COOKIE, encoding="utf-8").read().strip()
        cmd = [PY, "-m", "f2", "dy", "-M", "one", "-u", url, "-p", f2dir, "-d", "true"]
        if cookie:
            cmd += ["-k", cookie]
        log("抖音下载中（f2）...")
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=900)
        except subprocess.TimeoutExpired:
            raise SystemExit("抖音下载超时")
        mp4, imgs = _dy_media(f2dir)
        if not mp4 and not imgs:
            tail = ((r.stdout or "") + "\n" + (r.stderr or ""))[-500:]
            raise SystemExit("抖音下载失败（检查 dy_cookie.txt 是否有效）:\n" + tail)
    video = mp4[0] if mp4 else ""
    src = video or (imgs[0] if imgs else "")
    author = ""
    parts = src.replace("\\", "/").split("/")
    for i, p in enumerate(parts):
        if p == "one" and i + 1 <= len(parts) - 2:
            author = parts[i + 1]
            break
    descs = glob.glob(os.path.join(f2dir, "**", "*_desc.txt"), recursive=True)
    caption = ""
    if descs:
        caption = open(descs[0], encoding="utf-8", errors="replace").read().strip()
    title = ""
    base = os.path.basename(src) if src else ""
    m = re.match(r"\d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2}_(.*?)(?:_video|_image_\d+)?\.[A-Za-z0-9]+$", base)
    if m:
        title = m.group(1)
    return {"video": video, "images": imgs, "caption": caption, "author": author,
            "title": title or caption[:60]}


def douyin_flow(text, out_root, frames_n=12, vision_on=True, model_src="", lang="zh"):
    url = find_douyin_url(text) or text
    final, aid, typ = resolve_dy(url)
    key = "dy" + (aid or time.strftime("%Y%m%d_%H%M%S"))
    outdir = os.path.join(out_root, key)
    os.makedirs(outdir, exist_ok=True)
    log(f"抖音作品: id={aid or '?'} 类型={typ} | {final[:90]}")
    info = fetch_douyin(url, outdir)
    jdump(info, os.path.join(outdir, "dy_meta.json"))
    tr = None
    if info["video"]:
        audio = extract_audio(info["video"], outdir)
        tr = transcribe(audio, outdir, model_src, lang)
        frames = extract_frames(info["video"], outdir, frames_n)
        vd = vision(frames, outdir, enabled=vision_on)
    else:
        imgs = info["images"]
        fdir = os.path.join(outdir, "frames")
        os.makedirs(fdir, exist_ok=True)
        fl = []
        for i, p in enumerate(imgs[:frames_n]):
            # 统一转 JPEG（抖音图片常为 webp，Ollama 不支持）
            dst = os.path.join(fdir, f"img{i + 1:02d}.jpg")
            if not os.path.exists(dst):
                subprocess.run(["ffmpeg", "-y", "-i", p, "-q:v", "3", dst], capture_output=True)
                if not (os.path.exists(dst) and os.path.getsize(dst) > 512):
                    shutil.copy(p, dst)
            fl.append((i + 1, dst))
        log(f"图文作品: {len(imgs)} 张图（前 {len(fl)} 张做视觉描述）")
        vd = vision(fl, outdir, enabled=vision_on,
                    prompt="这是一条图文作品的图片之一。用1-2句中文描述画面内容（人物/场景/物品/穿搭）；如果画面里有文字，原文照录。")
    rp = build_report_dy(info, aid, outdir, tr, vd)
    log(f"VD_DONE {outdir}")
    print("REPORT:", rp)
    _shutdown_ollama()


def build_report_dy(info, aid, outdir, tr, vd):
    is_video = bool(info.get("video"))
    lines = []
    lines.append(f"# 抖音{'视频' if is_video else '图文'}解读素材：{info.get('title') or aid}")
    lines.append(f"- 作者: {info.get('author') or '?'} | 链接: https://www.douyin.com/{'video' if is_video else 'note'}/{aid or '?'}")
    if info.get("caption"):
        lines.append(f"- 文案: {info['caption'][:500]}")
    lines.append("")
    if tr:
        lines.append("## 音频转写（语音内容）")
        for s in tr["segments"]:
            mm, ss = divmod(int(s["t"]), 60)
            lines.append(f"[{mm:02d}:{ss:02d}] {s['text']}")
        lines.append("")
    lines.append("## 画面描述" + ("（抽帧）" if is_video else "（图片逐张）"))
    for v in vd:
        if is_video:
            mm, ss = divmod(int(v["t"]), 60)
            lines.append(f"[{mm:02d}:{ss:02d}] {v['desc']}")
        else:
            lines.append(f"[图{int(v['t']):02d}] {v['desc']}")
    lines.append("")
    lines.append("## 备注")
    lines.append("- 抖音无弹幕；评论区暂未接入（后续版本）")
    rp = os.path.join(outdir, "report.md")
    with open(rp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log("报告生成:", rp)
    return rp


def main():
    ap = argparse.ArgumentParser(description="视频解读流水线（B站 + 抖音）")
    ap.add_argument("url")
    ap.add_argument("--out", default=WORK)
    ap.add_argument("--frames", type=int, default=12)
    ap.add_argument("--max-min", type=int, default=0)
    ap.add_argument("--lang", default="zh")
    ap.add_argument("--model", default=MODEL, help="whisper 模型名或本地路径（默认 large-v3-turbo）")
    ap.add_argument("--no-vision", action="store_true")
    args = ap.parse_args()

    target = args.url.strip()
    if "douyin" in target:
        douyin_flow(target, args.out, frames_n=args.frames, vision_on=not args.no_vision,
                    model_src=args.model, lang=args.lang)
        _shutdown_ollama()
        return

    url = resolve_url(target)
    bvid, av, page = parse_ids(url)
    if not bvid and not av:
        raise SystemExit("无法识别视频链接: " + url)
    meta = get_meta(bvid, av)
    pages = meta.get("pages") or []
    pg = pages[page - 1] if 0 < page <= len(pages) else pages[0]
    cid = pg.get("cid")
    key = meta.get("bvid") or ("av" + av)
    outdir = os.path.join(args.out, key)
    os.makedirs(outdir, exist_ok=True)
    jdump(meta, os.path.join(outdir, "meta.json"))
    log(f"视频: {meta.get('title')} | {len(pages)}P | 使用 P{page} cid={cid}")

    video = download(url, outdir, args.max_min)
    audio = extract_audio(video, outdir)
    tr = transcribe(audio, outdir, args.model, args.lang)
    frames = extract_frames(video, outdir, args.frames)
    vd = vision(frames, outdir, enabled=not args.no_vision)
    comments = get_comments(meta["aid"], outdir)
    danmaku = get_danmaku(cid, outdir)
    rp = build_report(meta, page, outdir, tr, frames, vd, comments, danmaku)
    log(f"VD_DONE {outdir}")
    print("REPORT:", rp)
    _shutdown_ollama()


if __name__ == "__main__":
    main()
