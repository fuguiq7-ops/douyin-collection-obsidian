# -*- coding: utf-8 -*-
"""深挖：下载标星条目的原片/图集，并用本地 GPU 转写。"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import requests

from . import common, covers, db, notes, rawimport
from . import run as runmod

# 国内网络访问 HuggingFace 官方源较慢，默认走镜像；设置 HF_ENDPOINT 可覆盖
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
# 模型缓存跟项目走（默认在 C 盘用户目录会占几 GB）；可用 HF_HOME 覆盖
os.environ.setdefault("HF_HOME", str(common.REPO_ROOT / "hf-cache"))


def _register_cuda_dlls():
    """把 pip 安装的 cuBLAS / cuDNN DLL 目录注册进搜索路径（Windows 上 ctranslate2 需要）。"""
    import glob
    import site

    dirs = []
    bases = set()
    try:
        bases.update(site.getsitepackages())
    except Exception:
        pass
    try:
        bases.add(site.getusersitepackages())
    except Exception:
        pass
    for base in bases:
        dirs.extend(glob.glob(os.path.join(base, "nvidia", "*", "bin")))
    for d in dirs:
        try:
            os.add_dll_directory(d)
        except (AttributeError, OSError):
            pass
    if dirs:
        os.environ["PATH"] = os.pathsep.join(dirs) + os.pathsep + os.environ.get("PATH", "")
    return dirs


CUDA_DLL_DIRS = _register_cuda_dlls()


MEDIA_HEADERS = {
    "User-Agent": covers.HEADERS["User-Agent"],
    "Referer": "https://www.douyin.com/",
}

# 已经处理到底的状态：默认跳过，不再每轮重复下载+加载模型
DONE_STATES = ("已转写", "已存图", "无语音内容")


def _extract_urls(rec):
    video = rec.get("video") or {}
    videos = []

    def push(node):
        for u in (node or {}).get("url_list") or []:
            if isinstance(u, str) and u.startswith("http"):
                videos.append(u)

    for key in ("play_addr_h264", "play_addr", "play_addr_265", "download_addr"):
        push(video.get(key))
    for br in video.get("bit_rate") or []:
        if isinstance(br, dict):
            push(br.get("play_addr"))
    images = []
    for img in rec.get("images") or []:
        for u in (img or {}).get("url_list") or []:
            if isinstance(u, str) and u.startswith("http"):
                images.append(u)
    return {"videos": list(dict.fromkeys(videos)), "images": list(dict.fromkeys(images))}


def media_urls_from_raw(cfg, aweme_id):
    """从最近几次抓取的原始 JSON 里找该条目的直链（签名通常几小时内有效）。"""
    raw_root = Path(cfg["paths"]["raw"])
    if not raw_root.exists():
        return {}
    runs = sorted([d for d in raw_root.iterdir() if d.is_dir()], key=lambda d: d.stat().st_mtime, reverse=True)
    for run in runs[:3]:
        for pattern in ("watchlater.*.json", "listcollection.*.json", "collects-video.*.json"):
            for f in sorted(run.glob(pattern)):
                for rec in rawimport.extract_aweme_list(f):
                    if str(rec.get("aweme_id")) == str(aweme_id):
                        urls = _extract_urls(rec)
                        if urls["videos"] or urls["images"]:
                            return urls
    return {}


def refresh_media_urls(cfg, aweme_id):
    """原始数据里没找到（或都已过期）时，用 Edge 重新抓一份 detail。"""
    out = Path(cfg["paths"]["raw"]).parent / "deepdive" / f"{aweme_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        common.node_exe(),
        str(Path(cfg["repo_root"]) / "fetcher" / "fetch-item-detail.mjs"),
        "--id",
        str(aweme_id),
        "--out",
        str(out),
    ]
    proc = subprocess.run(cmd, cwd=str(cfg["repo_root"]), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        print(f"  ⚠ 刷新直链失败: {(proc.stderr or proc.stdout or '').strip()[-200:]}")
        return {}
    return common.read_json(out, {}) or {}


def download_file(url, dest, label="文件"):
    with requests.get(url, headers=MEDIA_HEADERS, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        last_report = 0
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(1024 * 256):
                if not chunk:
                    continue
                f.write(chunk)
                done += len(chunk)
                if total and done - last_report >= 20 * 1024 * 1024:
                    last_report = done
                    print(f"    … {label} {done / 1048576:.0f}/{total / 1048576:.0f} MB")
    if dest.stat().st_size <= 0:
        raise RuntimeError("下载得到空文件")
    return dest


def probe_streams(path):
    """用 ffmpeg 读流信息，返回 {'video': bool, 'audio': bool, 'duration': float}。

    有的播放地址只给音频轨道（拿到的是 BGM/音频直链），不校验就会把纯音频当成原片，
    后面必然转写不出内容。这里用它做一道闸。
    """
    info = {"video": False, "audio": False, "duration": 0.0}
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        return info
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    text = (proc.stderr or "") + (proc.stdout or "")
    info["video"] = bool(re.search(r"Stream #\S+.*: Video:", text))
    info["audio"] = bool(re.search(r"Stream #\S+.*: Audio:", text))
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", text)
    if m:
        info["duration"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    return info


def download_video(item, cfg):
    media = Path(cfg["media_dir"])
    media.mkdir(parents=True, exist_ok=True)
    base = f"{common.id8(item['aweme_id'])}_{common.sanitize_filename(item.get('title') or 'video', 40)}"
    dest = media / f"{base}.mp4"
    if dest.exists() and dest.stat().st_size > 0:
        cached = probe_streams(dest)
        if cached["video"]:
            return dest, None
        # 缓存里是纯音频（早前版本抓错轨道），删掉重下
        print(f"  ⚠ 已有缓存只有音频轨（无画面），重新下载")
        dest.unlink(missing_ok=True)
    last_err = ""
    audio_fallback = media / f"{base}.audio.mp4"
    for round_no in (1, 2):
        urls = media_urls_from_raw(cfg, item["aweme_id"]) if round_no == 1 else refresh_media_urls(cfg, item["aweme_id"])
        candidates = (urls or {}).get("videos") or []
        if not candidates:
            last_err = last_err or "没有找到播放地址"
            continue
        for url in candidates[:8]:
            try:
                print(f"  · 下载直链（{round_no == 1 and '原始数据' or '刷新'}）: {url[:80]}...")
                download_file(url, dest, label="下载")
                streams = probe_streams(dest)
                if not streams["video"]:
                    print(f"  ⚠ 这个地址只有音频轨（{streams['duration']:.0f}s），先留作音频兜底，换下一个")
                    last_err = "直链只有音频轨道"
                    if not audio_fallback.exists():
                        dest.replace(audio_fallback)
                    else:
                        dest.unlink(missing_ok=True)
                    continue
                return dest, None
            except Exception as exc:  # noqa: BLE001
                last_err = str(exc)
                dest.unlink(missing_ok=True)
                continue
    # 所有地址都只有音频轨时，退一步用音频转写（口播类音频贴也能出逐字稿）
    if audio_fallback and audio_fallback.exists():
        print(f"  ⚠ 该条拿不到画面，只有音频轨，直接对音频转写")
        return audio_fallback, None
    return None, f"下载失败: {last_err}"


def resolve_ffmpeg():
    """定位 ffmpeg：先查 PATH，再回退工具目录自带静态二进制，最后用 imageio-ffmpeg 轮子。"""
    found = shutil.which("ffmpeg")
    if found:
        return found
    exe_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    local = common.REPO_ROOT / "tools" / "ffmpeg" / "bin" / exe_name
    if local.exists():
        return str(local)
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def cuda_available():
    try:
        import ctranslate2

        # 直接查询可见 CUDA 设备数；驱动不匹配 / 无显卡时返回 0 或抛异常，都按不可用处理
        if hasattr(ctranslate2, "get_cuda_device_count"):
            return ctranslate2.get_cuda_device_count() > 0
        return bool(ctranslate2.get_supported_compute_types("cuda"))
    except Exception:
        return False


def download_images(item, cfg):
    out_dir = Path(cfg["media_dir"]) / common.id8(item["aweme_id"])
    out_dir.mkdir(parents=True, exist_ok=True)
    urls = media_urls_from_raw(cfg, item["aweme_id"]).get("images") or []
    if not urls:
        urls = (refresh_media_urls(cfg, item["aweme_id"]) or {}).get("images") or []
    ok = 0
    for i, url in enumerate(urls, 1):
        try:
            resp = requests.get(url, headers=MEDIA_HEADERS, timeout=30)
            resp.raise_for_status()
            (out_dir / f"{i:02d}.jpg").write_bytes(resp.content)
            ok += 1
        except Exception:
            continue
    if ok:
        return out_dir, None
    return None, "图片下载失败"


def _cjk_count(text):
    return len(re.findall(r"[\u4e00-\u9fff]", text or ""))


def _looks_like_speech(text, min_chars=40, min_cjk_ratio=0.55):
    """判定转写结果是不是「真的有人讲中文」。

    纯背景音乐 whisper 也会硬凑内容：要么极短（"Body set the clock" 这种曲名碎片），
    要么把中英歌词混着听出来（中文占比不到三成）。实测 61 篇真实口播逐字稿的中文占比
    都在 0.72 以上，所以用「够长 + 中文占比达标」两条卡住 BGM 幻觉。
    """
    plain = re.sub(r"\s+", "", _strip_ts(text))
    if not common.clean_content_text(plain) or len(plain) < min_chars:
        return False
    return _cjk_count(plain) / len(plain) >= min_cjk_ratio


def _run_whisper(model, audio, vad, vad_threshold):
    """跑一遍转写，返回 (文本, 片段数)。"""
    kwargs = {
        "language": "zh",
        "beam_size": 1,
        "condition_on_previous_text": False,
        "vad_filter": vad,
    }
    if vad:
        # 默认 VAD（threshold 0.5）会把唱歌、轻声、远景对白整段判成静音，
        # 这里放宽阈值并缩短最短静音判定，避免真有人声却识别为空
        kwargs["vad_parameters"] = dict(
            threshold=vad_threshold,
            min_silence_duration_ms=600,
            speech_pad_ms=300,
        )
    segments, _info = model.transcribe(str(audio), **kwargs)
    lines = []
    count = 0
    for seg in segments:
        count += 1
        ts = f"[{int(seg.start) // 60:02d}:{int(seg.start) % 60:02d}]"
        lines.append(f"{ts} {seg.text.strip()}")
        if count % 100 == 0:
            print(f"    … 已转写到 {int(seg.start) // 60} 分钟")
    return "\n".join(lines), count


def transcribe(video_path):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None, "faster-whisper 未安装（重跑「准备.cmd」）"
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        return None, "找不到 ffmpeg（PATH 或 tools/ffmpeg/bin 均无）"
    audio = Path(video_path).with_suffix(".wav")
    proc = subprocess.run(
        [ffmpeg, "-y", "-i", str(video_path), "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", str(audio)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        return None, f"ffmpeg 提取音频失败: {(proc.stderr or '')[-300:]}"
    model_pref = (os.environ.get("WHISPER_MODEL") or "").strip()
    # CPU 下默认 small（速度/精度平衡）；可用 WHISPER_MODEL 覆盖为 base/medium 等
    cpu_model = model_pref or "small"
    vad_threshold = 0.2
    try:
        vad_threshold = float(os.environ.get("WHISPER_VAD_THRESHOLD") or 0.2)
    except ValueError:
        pass
    attempts = []
    if cuda_available():
        gpu_model = model_pref or "large-v3"
        attempts.append((gpu_model, "cuda", "int8_float16"))
        if gpu_model != "medium":
            attempts.append(("medium", "cuda", "int8_float16"))
        print(f"  · 检测到 CUDA，尝试 GPU 转写（{gpu_model}）")
    else:
        print(f"  · 未检测到可用 CUDA，改用 CPU + {cpu_model} 模型")
    # 始终追加 CPU 兜底：CUDA 驱动不匹配 / 显存不足 / 加载失败时自动回落 CPU
    attempts.append((cpu_model, "cpu", "int8"))
    last_err = ""
    for model_name, device, compute in attempts:
        try:
            print(f"  · 加载模型 {model_name}/{device}（首次会自动下载，1.5–3GB）...")
            model = WhisperModel(model_name, device=device, compute_type=compute)
            # 第一遍：放宽 VAD
            text, segs = _run_whisper(model, audio, True, vad_threshold)
            if not _looks_like_speech(text):
                # 第二遍：完全关闭 VAD 兜底（唱歌 / 轻声 / 背景噪声大时 VAD 会整段滤掉）
                print("  · 放宽 VAD 后仍无有效中文口播，关闭 VAD 再试一遍")
                text2, segs2 = _run_whisper(model, audio, False, vad_threshold)
                text = text2 if _looks_like_speech(text2) else ""
            try:
                audio.unlink()
            except OSError:
                pass
            return text, None
        except Exception as exc:  # noqa: BLE001
            last_err = f"{model_name}/{device}: {exc}"
    return None, f"转写失败（{last_err}）"


def _strip_ts(text):
    return "\n".join(re.sub(r"^\[\d{2}:\d{2}\]\s*", "", ln).strip() for ln in (text or "").splitlines())


def main(argv=None):
    parser = argparse.ArgumentParser(description="深挖标星条目（下载 + 转写）")
    parser.add_argument("--id", help="只处理指定 aweme_id")
    parser.add_argument("--refresh", action="store_true", help="已完成转写的也重新处理")
    parser.add_argument("--settings")
    args = parser.parse_args(argv)

    cfg = common.load_config(args.settings)
    con = db.connect(cfg["paths"]["db"])
    runmod.adopt_note_edits(con, cfg)

    targets = []
    for row in db.all_items(con):
        starred = row["starred_override"] if row["starred_override"] is not None else row["starred"]
        liked = bool(row["liked"])
        if args.id and row["aweme_id"] != args.id:
            continue
        if not args.id and not (starred or liked):
            continue
        if not args.refresh and not args.id and row["deep_state"] in DONE_STATES:
            continue
        targets.append(row)

    if not targets:
        print("没有待深挖的条目。点赞过的视频会自动进入队列；也可在笔记里把「深挖: true」手动补充。")
        return 0

    print(f"待深挖：{len(targets)} 条")
    transcripts_dir = Path(cfg["paths"]["db"]).parent / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    done = 0
    for row in targets:
        item = runmod.row_to_item(row)
        # 抖音「图文长文」作品把整篇正文塞在标题/desc 里，原样打印会让 chain.log 瞬间膨胀
        raw_title = (item["title"] or "").replace("\n", " ").strip()
        short = raw_title[:40] + ("…" if len(raw_title) > 40 else "")
        label = f"{short or item['aweme_id']}（{item['aweme_id']}）"
        print(f"\n→ {label}")
        state = row["deep_state"] or ""
        if item["kind"] == "images":
            path, err = download_images(item, cfg)
            if err:
                print(f"  ✗ {err}")
                continue
            state = "已存图"
        else:
            video, err = download_video(item, cfg)
            if err:
                print(f"  ✗ 下载失败: {err}")
                continue
            print(f"  ✓ 原片: {video}")
            text, terr = transcribe(video)
            if terr:
                print(f"  ⚠ 转写失败: {terr}")
                state = "已下载"
            else:
                plain = _strip_ts(text)
                if not common.clean_content_text(plain):
                    state = "无语音内容"
                    print("  · 未识别到有效语音（已放宽 VAD 并关闭 VAD 复测，判定为纯背景音乐）")
                    # 之前版本可能留下过幻觉逐字稿，一并清掉，免得被提炼成空话速查稿
                    old_tp = row["transcript_path"] if "transcript_path" in row.keys() else ""
                    if old_tp:
                        Path(old_tp).unlink(missing_ok=True)
                        db.set_fields(con, item["aweme_id"], transcript_path="")
                else:
                    tpath = transcripts_dir / f"{common.id8(item['aweme_id'])}.md"
                    tpath.write_text(text, encoding="utf-8", newline="\n")
                    db.set_fields(con, item["aweme_id"], transcript_path=str(tpath))
                    state = "已转写"
                    print(f"  ✓ 逐字稿: {tpath}（{len(text)} 字）")
                if not cfg.get("deepdive", {}).get("keep_video", False):
                    try:
                        video.unlink()
                        print("  ✓ 已删除临时原片（只保留逐字稿）")
                    except OSError:
                        pass
        db.set_fields(con, item["aweme_id"], deep_state=state)
        fresh = db.get_item(con, item["aweme_id"])
        item2 = runmod.row_to_item(fresh)
        transcript = ""
        if fresh["transcript_path"] and Path(fresh["transcript_path"]).exists():
            transcript = Path(fresh["transcript_path"]).read_text(encoding="utf-8")
        notes_status, note_path, fm = notes.write_note(item2, cfg, transcript)
        last_auto = {k: fm[k] for k in ("分类", "标签", "状态", "深挖")}
        db.set_fields(con, item["aweme_id"], note_path=str(note_path), last_auto=json.dumps(last_auto, ensure_ascii=False))
        done += 1

    rows = [runmod.row_to_item(r) for r in db.all_items(con)]
    pending = [it for it in rows if it["deep_state"] == "已转写"]
    out = Path(cfg["paths"]["db"]).parent / "待摘要清单.md"
    lines = ["# 待摘要清单", "", f"共 {len(pending)} 条（逐字稿已就绪，可让 Codex 批量写摘要）", ""]
    for it in pending:
        lines.append(f"- {it['title'] or it['aweme_id']} — {it['author_name']}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    notes.write_transcribed_index(rows, cfg)
    print(f"\n深挖完成 {done}/{len(targets)} 条；待摘要清单: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
