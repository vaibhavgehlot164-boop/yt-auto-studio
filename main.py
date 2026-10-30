"""YT Auto Studio: topic -> script -> images -> voiceover -> captions -> video (-> upload). All free."""
import argparse, asyncio, json, os, random, re, sys, time, urllib.parse
from datetime import datetime
from pathlib import Path

import numpy as np
import requests
import edge_tts
from PIL import Image, ImageDraw, ImageFont, ImageOps

if not hasattr(Image, "ANTIALIAS"):          # moviepy 1.0.3 + new Pillow fix
    Image.ANTIALIAS = Image.LANCZOS

from moviepy.editor import (AudioFileClip, CompositeAudioClip, CompositeVideoClip,
                            ImageClip, concatenate_videoclips, afx)

VOICES = {"hinglish": "en-IN-PrabhatNeural", "hindi": "hi-IN-MadhurNeural", "english": "en-US-AndrewNeural"}
SIZES = {"short": (720, 1280), "long": (1280, 720)}
FONT_DEV = ["/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf", "C:/Windows/Fonts/Nirmala.ttf"]
FONT_LAT = ["/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "C:/Windows/Fonts/arialbd.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"]


def log(msg): print(f"▶ {msg}", flush=True)


# ---------- 1. SCRIPT (Gemini free tier) ----------
def gemini_json(prompt):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY set karo (free: https://aistudio.google.com/apikey)")
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.9}}
    for attempt in range(5):
        r = requests.post(url, headers={"x-goog-api-key": key}, json=body, timeout=180)
        if r.status_code == 200:
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
            return json.loads(text)
        if r.status_code in (429, 500, 503):
            time.sleep(8 * (attempt + 1)); continue
        sys.exit(f"Gemini error {r.status_code}: {r.text[:300]}")
    sys.exit("Gemini busy hai, thodi der baad try karo.")


def write_script(topic, style, lang, fmt, n):
    words = "100-130 words total" if fmt == "short" else "500-650 words total"
    script_lang = {"hinglish": "Hinglish (Hindi written in Roman/English letters)",
                   "hindi": "Hindi in Devanagari script", "english": "English"}[lang]
    prompt = f"""You are a YouTube scriptwriter. Topic: "{topic}". Style: {style}. Narration language: {script_lang}.
Format: {'YouTube Short (vertical, under 50 seconds)' if fmt == 'short' else 'YouTube video 3-4 minutes'}.
Make exactly {n} scenes, {words}. Scene 1 narration MUST open with a strong hook in the first 3 seconds.
Narration is read by a text-to-speech voice: plain spoken sentences only, no emojis, no markdown, no stage directions.
Return ONLY JSON:
{{"title": str (catchy, under 70 chars),
 "description": str (2-4 lines, in narration language),
 "tags": [8 short strings],
 "thumbnail_text": str (max 4 words, punchy),
 "thumbnail_prompt": str (English, eye-catching image prompt, no text in image),
 "scenes": [{{"narration": str, "image_prompt": str (English, detailed visual, same consistent art style in every scene, no text in image)}}]}}
Be original, factual, and avoid copying existing videos."""
    return gemini_json(prompt)


# ---------- 2. IMAGES (Pollinations, free, no key) ----------
def make_image(prompt, path, w, h, seed):
    suffix = ", cinematic, highly detailed, dramatic lighting, no text, no watermark"
    url = ("https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt + suffix)
           + f"?width={w}&height={h}&nologo=true&seed={seed}")
    for attempt in range(4):
        try:
            r = requests.get(url, timeout=150)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
                path.write_bytes(r.content)
                img = ImageOps.fit(Image.open(path).convert("RGB"), (w, h), Image.LANCZOS)
                img.save(path, quality=92)
                return
        except Exception as e:
            print("  image retry:", e)
        time.sleep(5 * (attempt + 1))
    print("  ⚠ image fail, placeholder use kar raha hu")
    rnd = random.Random(seed)
    top, bot = [tuple(rnd.randint(20, 120) for _ in range(3)) for _ in range(2)]
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    for c in range(3):
        arr[:, :, c] = np.linspace(top[c], bot[c], h)[:, None]
    Image.fromarray(arr).save(path, quality=92)


# ---------- 3. VOICEOVER (edge-tts, free) ----------
async def tts(text, voice, out):
    await edge_tts.Communicate(text, voice, rate=os.environ.get("TTS_RATE", "+0%")).save(str(out))


# ---------- 4. CAPTIONS + VIDEO ----------
def pick_font(text, size):
    dev = any("\u0900" <= ch <= "\u097F" for ch in text)
    for p in [os.environ.get("FONT", "")] + (FONT_DEV if dev else []) + FONT_LAT:
        if p and Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def wrap(text, font, d, max_w):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if d.textlength(t, font=font) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur); cur = w
    return lines + ([cur] if cur else [])


def text_overlay(text, w, h, size, y_frac=0.70):
    font = pick_font(text, size)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0)); d = ImageDraw.Draw(img)
    lines = wrap(text, font, d, int(w * 0.86)); lh = int(size * 1.35)
    y = int(h * y_frac) - lh * len(lines) // 2
    for ln in lines:
        x = (w - d.textlength(ln, font=font)) / 2
        d.text((x, y), ln, font=font, fill="white", stroke_width=max(3, size // 10), stroke_fill="black")
        y += lh
    return np.array(img)


def chunk_words(text, per=4):
    ws = text.split()
    return [" ".join(ws[i:i + per]) for i in range(0, len(ws), per)] or [text]


def build_scene(i, narration, img_path, mp3, W, H):
    audio = AudioFileClip(str(mp3)); dur = audio.duration + 0.35
    base = ImageClip(str(img_path)).set_duration(dur)
    zoom = base.resize(lambda t: 1 + 0.07 * t / dur) if i % 2 == 0 else base.resize(lambda t: 1.07 - 0.07 * t / dur)
    layers = [zoom.set_position("center")]
    chunks = chunk_words(narration); total = sum(len(c) for c in chunks); t = 0.0
    size = int(W * 0.075)
    for c in chunks:
        cd = (audio.duration) * len(c) / total
        layers.append(ImageClip(text_overlay(c, W, H, size)).set_start(t).set_duration(cd).set_position((0, 0)))
        t += cd
    return CompositeVideoClip(layers, size=(W, H)).set_duration(dur).set_audio(audio)


def make_thumbnail(meta, out_dir, seed):
    p = out_dir / "thumbnail.jpg"
    make_image(meta.get("thumbnail_prompt", meta["title"]), p, 1280, 720, seed)
    img = Image.open(p).convert("RGBA")
    ov = Image.fromarray(text_overlay(meta.get("thumbnail_text", meta["title"]), 1280, 720, 110, 0.5))
    Image.alpha_composite(img, ov).convert("RGB").save(p, quality=92)
    return p


# ---------- 5. UPLOAD (optional) ----------
def upload(video, thumb, meta, privacy):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    need = ["YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"]
    if any(k not in os.environ for k in need):
        print("⚠ Upload skip: YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN set nahi hain."); return
    creds = Credentials(None, refresh_token=os.environ["YT_REFRESH_TOKEN"], client_id=os.environ["YT_CLIENT_ID"],
                        client_secret=os.environ["YT_CLIENT_SECRET"], token_uri="https://oauth2.googleapis.com/token")
    yt = build("youtube", "v3", credentials=creds)
    tags = [str(t).replace("#", "") for t in meta.get("tags", [])]
    desc = meta.get("description", "") + "\n\n" + " ".join("#" + t.replace(" ", "") for t in tags[:5]) \
           + "\n\nAI-assisted video."
    body = {"snippet": {"title": meta["title"][:100], "description": desc, "tags": tags, "categoryId": "22"},
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}}
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(str(video), chunksize=-1, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    vid = resp["id"]; print(f"✅ Uploaded: https://youtu.be/{vid} ({privacy})")
    try:
        yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(str(thumb))).execute()
    except Exception as e:
        print("⚠ Thumbnail set nahi hua (channel verified hona chahiye):", e)


# ---------- MAIN ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("topic")
    ap.add_argument("--style", default="Facts / Education")
    ap.add_argument("--lang", choices=VOICES, default="hinglish")
    ap.add_argument("--format", choices=SIZES, default="short")
    ap.add_argument("--scenes", type=int, default=0)
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"])
    a = ap.parse_args()

    W, H = SIZES[a.format]; n = a.scenes or (7 if a.format == "short" else 11)
    out = Path("output") / datetime.now().strftime("%Y%m%d_%H%M%S"); out.mkdir(parents=True, exist_ok=True)

    log("Script likh raha hu (Gemini)...")
    meta = write_script(a.topic, a.style, a.lang, a.format, n)
    scenes = meta["scenes"]
    (out / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "script.txt").write_text("\n\n".join(s["narration"] for s in scenes), encoding="utf-8")
    print("  Title:", meta["title"])

    clips = []
    for i, s in enumerate(scenes):
        log(f"Scene {i + 1}/{len(scenes)}: image + voice")
        img = out / f"scene{i + 1}.jpg"; mp3 = out / f"scene{i + 1}.mp3"
        make_image(s["image_prompt"], img, W, H, seed=random.randint(1, 10**6))
        asyncio.run(tts(s["narration"], VOICES[a.lang], mp3))
        clips.append(build_scene(i, s["narration"], img, mp3, W, H))

    log("Video jod raha hu (thoda time lagega)...")
    final = concatenate_videoclips(clips, method="compose")
    music = Path("music.mp3")
    if music.exists():
        m = AudioFileClip(str(music)).fx(afx.audio_loop, duration=final.duration).volumex(0.08)
        final = final.set_audio(CompositeAudioClip([final.audio, m]))
    video = out / "video.mp4"
    final.write_videofile(str(video), fps=24, codec="libx264", audio_codec="aac", preset="veryfast",
                          threads=2, logger=None)

    log("Thumbnail bana raha hu...")
    thumb = make_thumbnail(meta, out, random.randint(1, 10**6))
    print(f"\n✅ Ready: {video}")
    if a.upload:
        log("YouTube pe upload...")
        upload(video, thumb, meta, a.privacy)


if __name__ == "__main__":
    main()
  
