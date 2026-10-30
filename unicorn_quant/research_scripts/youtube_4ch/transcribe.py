"""没有字幕的视频(交易伦等)：yt-dlp 下音频 → faster-whisper small(多语言, CPU int8) 转文字 → subs/<who>_<id>.whisper.txt"""
import os, subprocess, sys, glob
from faster_whisper import WhisperModel
import imageio_ffmpeg
HERE = os.path.dirname(os.path.abspath(__file__)); A = os.path.join(HERE, "audio"); S = os.path.join(HERE, "subs")
FF = os.path.dirname(imageio_ffmpeg.get_ffmpeg_exe())
todo = [l.split() for l in open(os.path.join(HERE, "need_whisper.txt")) if l.strip()]
m = WhisperModel("small", device="cpu", compute_type="int8", cpu_threads=4)
for who, v in todo:
    out = os.path.join(S, f"{who}_{v}.whisper.txt")
    if os.path.exists(out): continue
    if not glob.glob(os.path.join(A, v + ".*")):
        subprocess.run(["yt-dlp", "-f", "bestaudio[ext=m4a]/bestaudio", "--ffmpeg-location", FF, "-o", os.path.join(A, "%(id)s.%(ext)s"), "--", v],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    f = glob.glob(os.path.join(A, v + ".*"))
    if not f: print("no audio", v, flush=True); continue
    segs, info = m.transcribe(f[0], language="zh", vad_filter=True, beam_size=1)
    txt = " ".join(s.text.strip() for s in segs)
    open(out, "w", encoding="utf-8").write(txt); print("done", who, v, len(txt), flush=True)
