#!/usr/bin/env python3
import argparse
import json
import pathlib
import subprocess


def run(cmd, capture=False):
    if capture:
        return subprocess.check_output(cmd, text=True).strip()
    subprocess.run(cmd, check=True)


def probe(path):
    raw = run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=index,codec_type,codec_name,width,height,r_frame_rate",
        "-of", "json", str(path)
    ], capture=True)
    return json.loads(raw)


def duration_seconds(info):
    return float(info["format"]["duration"])


def video_stream(info):
    for s in info.get("streams", []):
        if s.get("codec_type") == "video":
            return s
    return None


def audio_stream(info):
    for s in info.get("streams", []):
        if s.get("codec_type") == "audio":
            return s
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--assets", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--qa", required=True)
    ap.add_argument("--text-track")
    args = ap.parse_args()

    manifest = json.load(open(args.manifest, encoding="utf-8"))
    assets = pathlib.Path(args.assets)
    out_dir = pathlib.Path(args.out)
    qa_dir = pathlib.Path(args.qa)
    out_dir.mkdir(parents=True, exist_ok=True)
    qa_dir.mkdir(parents=True, exist_ok=True)

    narration = assets / manifest["narration"]
    clips = [assets / c["file"] for c in manifest["clips"]]
    expected = clips + [narration]
    missing = [str(p) for p in expected if not p.exists()]
    if missing:
        raise SystemExit("Missing assets: " + ", ".join(missing))

    text_track = pathlib.Path(args.text_track) if args.text_track else None
    if text_track and not text_track.exists():
        raise SystemExit(f"Missing text track: {text_track}")

    narration_info = probe(narration)
    narration_duration = duration_seconds(narration_info)
    if not audio_stream(narration_info):
        raise SystemExit("Narration file has no audio stream")

    source_durations = []
    for clip in clips:
        info = probe(clip)
        if not video_stream(info):
            raise SystemExit(f"No video stream: {clip}")
        d = duration_seconds(info)
        if d <= 0:
            raise SystemExit(f"Invalid duration: {clip}")
        source_durations.append(d)

    requested = [float(c["target_seconds"]) for c in manifest["clips"]]
    requested_total = sum(requested)
    if requested_total <= 0:
        raise SystemExit("Manifest target duration total must be positive")

    factor = narration_duration / requested_total
    targets = [x * factor for x in requested]
    stretch = [t / s for t, s in zip(targets, source_durations)]
    max_stretch = float(manifest.get("max_stretch", 1.6))
    if any(x > max_stretch for x in stretch):
        raise SystemExit(f"Required clip stretch exceeds guardrail {max_stretch}: {stretch}")

    inputs = []
    for clip in clips:
        inputs += ["-i", str(clip)]
    inputs += ["-i", str(narration)]

    filters = []
    labels = []
    for i, (src_d, target_d) in enumerate(zip(source_durations, targets)):
        ratio = target_d / src_d
        label = f"v{i}"
        labels.append(f"[{label}]")
        filters.append(
            f"[{i}:v]trim=duration={src_d:.6f},setpts=PTS-STARTPTS,"
            f"scale=1080:1920:force_original_aspect_ratio=increase,"
            f"crop=1080:1920,fps=30,setpts={ratio:.9f}*PTS[{label}]"
        )

    base_label = "vbase" if text_track else "vout"
    filters.append("".join(labels) + f"concat=n={len(clips)}:v=1:a=0[{base_label}]")
    if text_track:
        escaped = str(text_track).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
        filters.append(f"[vbase]subtitles='{escaped}'[vout]")
    filter_complex = ";".join(filters)

    output = out_dir / manifest["output"]
    narration_index = len(clips)
    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", f"{narration_index}:a:0",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "192k",
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-t", f"{narration_duration:.6f}",
        "-movflags", "+faststart", str(output)
    ]
    run(cmd)

    final_info = probe(output)
    final_v = video_stream(final_info)
    final_a = audio_stream(final_info)
    final_duration = duration_seconds(final_info)

    checks = {
        "file_exists": output.exists(),
        "video_codec_h264": bool(final_v and final_v.get("codec_name") == "h264"),
        "audio_codec_aac": bool(final_a and final_a.get("codec_name") == "aac"),
        "resolution_1080x1920": bool(final_v and final_v.get("width") == 1080 and final_v.get("height") == 1920),
        "duration_matches_narration": abs(final_duration - narration_duration) <= 0.20,
        "all_source_assets_valid": True,
        "text_track_present": bool(text_track and text_track.exists()) if manifest.get("require_text_track", False) else True,
    }
    technical_pass = all(checks.values())

    qa = {
        "short_id": manifest["short_id"],
        "pilot": bool(manifest.get("pilot", False)),
        "narration_seconds": round(narration_duration, 3),
        "output_seconds": round(final_duration, 3),
        "clip_count": len(clips),
        "source_clip_seconds": [round(x, 3) for x in source_durations],
        "target_clip_seconds": [round(x, 3) for x in targets],
        "stretch_factors": [round(x, 3) for x in stretch],
        "checks": checks,
        "technical_status": "PASS" if technical_pass else "FAIL",
        "editorial_status": "PENDING_HUMAN_REVIEW",
        "captions_status": "BURNED_IN" if text_track else "NONE",
        "overlay_count": len(manifest.get("overlays", [])),
        "notes": manifest.get("notes", []),
    }
    json.dump(qa, open(qa_dir / f"{manifest['short_id']}.json", "w", encoding="utf-8"), indent=2)
    with open(qa_dir / f"{manifest['short_id']}.txt", "w", encoding="utf-8") as f:
        f.write(f"short={manifest['short_id']}\n")
        f.write(f"clips={len(clips)}\n")
        f.write(f"narration_duration={narration_duration:.3f}\n")
        f.write(f"output_duration={final_duration:.3f}\n")
        f.write(f"resolution={final_v.get('width')}x{final_v.get('height')}\n")
        f.write(f"video_codec={final_v.get('codec_name')}\n")
        f.write(f"audio_codec={final_a.get('codec_name')}\n")
        f.write("stretch_factors=" + ",".join(f"{x:.3f}" for x in stretch) + "\n")
        f.write(f"captions_status={'BURNED_IN' if text_track else 'NONE'}\n")
        f.write(f"overlay_count={len(manifest.get('overlays', []))}\n")
        f.write(f"technical_status={'PASS' if technical_pass else 'FAIL'}\n")
        f.write("editorial_status=PENDING_HUMAN_REVIEW\n")

    if not technical_pass:
        raise SystemExit("Technical QA failed")


if __name__ == "__main__":
    main()
