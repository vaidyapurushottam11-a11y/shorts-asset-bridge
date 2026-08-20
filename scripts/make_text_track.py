#!/usr/bin/env python3
import argparse
import json
import pathlib
import re
import whisper


def ass_time(seconds):
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def clean_text(text):
    text = re.sub(r"\s+", " ", text or "").strip()
    return text


def ass_escape(text):
    return clean_text(text).replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


def chunk_words(words, max_words=4):
    chunks = []
    buf = []
    start = None
    end = None
    for w in words:
        token = clean_text(w.get("word", ""))
        if not token:
            continue
        if start is None:
            start = float(w.get("start", 0.0))
        end = float(w.get("end", start + 0.5))
        buf.append(token)
        terminal = token.endswith((".", "?", "!", ",", ";", ":"))
        if len(buf) >= max_words or (terminal and len(buf) >= 2):
            chunks.append((start, end, " ".join(buf)))
            buf, start, end = [], None, None
    if buf:
        chunks.append((start or 0.0, end or (start or 0.0) + 1.0, " ".join(buf)))
    return chunks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--model", default="base.en")
    args = ap.parse_args()

    manifest = json.load(open(args.manifest, encoding="utf-8"))
    model = whisper.load_model(args.model)
    result = model.transcribe(args.audio, language="en", word_timestamps=True, fp16=False)

    all_words = []
    for seg in result.get("segments", []):
        if seg.get("words"):
            all_words.extend(seg["words"])

    if all_words:
        captions = chunk_words(all_words, max_words=4)
    else:
        captions = []
        for seg in result.get("segments", []):
            text = clean_text(seg.get("text", ""))
            parts = text.split()
            if not parts:
                continue
            start, end = float(seg["start"]), float(seg["end"])
            n = max(1, (len(parts) + 3) // 4)
            span = (end - start) / n
            for i in range(n):
                chunk = parts[i*4:(i+1)*4]
                if chunk:
                    captions.append((start + i*span, min(end, start + (i+1)*span), " ".join(chunk)))

    pathlib.Path(args.transcript).write_text(clean_text(result.get("text", "")) + "\n", encoding="utf-8")

    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Caption,Arial,66,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,1,2,90,90,250,1
Style: Overlay,Arial,72,&H00FFFFFF,&H000000FF,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,3,3,0,8,110,110,180,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""

    events = []
    for start, end, text in captions:
        if end <= start:
            end = start + 0.35
        events.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Caption,,0,0,0,,{ass_escape(text)}")

    for ov in manifest.get("overlays", []):
        start = float(ov["start"])
        end = float(ov["end"])
        text = ass_escape(ov["text"])
        events.append(f"Dialogue: 1,{ass_time(start)},{ass_time(end)},Overlay,,0,0,0,,{text}")

    pathlib.Path(args.out).write_text(header + "\n".join(events) + "\n", encoding="utf-8")

    meta = {
        "caption_chunks": len(captions),
        "overlays": len(manifest.get("overlays", [])),
        "transcript_chars": len(clean_text(result.get("text", ""))),
        "model": args.model,
    }
    print(json.dumps(meta))


if __name__ == "__main__":
    main()
