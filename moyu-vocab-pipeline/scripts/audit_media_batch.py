#!/usr/bin/env python3
"""Read-only completeness audit for episode/course media folders."""

import argparse
import json
import subprocess
import sys
from pathlib import Path


def episode_numbers(spec: str) -> list[int]:
    values: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = (int(x) for x in part.split("-", 1))
            values.update(range(start, end + 1))
        else:
            values.add(int(part))
    return sorted(values)


def probe(path: Path) -> dict | None:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration:stream=codec_type,codec_name,profile,width,height,pix_fmt,channels,codec_tag_string",
             "-of", "json", str(path)],
            check=True, capture_output=True, text=True,
        )
        return json.loads(result.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
        return None


def av_summary(info: dict | None) -> tuple[dict | None, dict | None, float | None]:
    if not info:
        return None, None, None
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    try:
        duration = float(info.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        duration = None
    return video, audio, duration


def canonical_status(source: Path, target: Path) -> str:
    if not target.exists():
        return "MISSING"
    if target.stat().st_size < 1_000_000:
        return "INVALID(size)"
    source_info, target_info = probe(source), probe(target)
    sv, _, sd = av_summary(source_info)
    tv, ta, td = av_summary(target_info)
    if not tv or not ta or td is None:
        return "INVALID(av)"
    if sv and (sv.get("width"), sv.get("height")) != (tv.get("width"), tv.get("height")):
        return "INVALID(resolution)"
    if sd is not None and abs(sd - td) > 1.0:
        return "INVALID(duration)"
    if source.resolve() != target.resolve():
        source_codec = (sv or {}).get("codec_name")
        expected_codec = source_codec if source_codec in {"hevc", "h264"} else "hevc"
        expected_tag = "hvc1" if expected_codec == "hevc" else "avc1"
        if tv.get("codec_name") != expected_codec or tv.get("codec_tag_string") != expected_tag:
            return "INVALID(video-codec)"
        if ta.get("codec_name") != "aac":
            return "INVALID(audio-codec)"
    return f"OK({tv.get('codec_name')}/{ta.get('codec_name')},{tv.get('width')}x{tv.get('height')})"


def json_status(path: Path) -> str:
    if not path.exists():
        return "MISSING"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        vocab = data.get("vocabulary")
        if data.get("schemaVersion") != 2 or not isinstance(vocab, list):
            return "INVALID(schema)"
        required = {"cueIndex", "term", "phonetic", "partOfSpeech", "meaning"}
        if any(not isinstance(item, dict) or not required.issubset(item) for item in vocab):
            return "INVALID(entry)"
        return f"OK({len(vocab)})"
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return "INVALID(json)"


def highlighted_status(path: Path) -> str:
    if not path.exists():
        return "MISSING"
    text = path.read_text(encoding="utf-8", errors="replace")
    opens = text.count("<font color=")
    closes = text.count("</font>")
    return f"OK({opens})" if opens > 0 and opens == closes else "INVALID(tags)"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--episodes", required=True, help="e.g. 4-10 or 4,6,8-10")
    parser.add_argument("--strict", action="store_true", help="exit 2 if any canonical deliverable is missing/invalid")
    args = parser.parse_args()

    incomplete = False
    for number in episode_numbers(args.episodes):
        directory = args.root / f"第{number}集"
        mkvs = sorted(directory.glob("*.mkv")) if directory.is_dir() else []
        if len(mkvs) > 1:
            print(f"E{number:02d}\tSOURCE=AMBIGUOUS")
            incomplete = True
            continue
        if mkvs:
            source = mkvs[0]
        else:
            originals = [p for p in directory.glob("*.mp4")
                         if "-moyu-" not in p.name and "_000" not in p.stem
                         and not p.name.endswith(".converting.mp4")]
            if len(originals) != 1:
                print(f"E{number:02d}\tSOURCE={'MISSING' if not originals else 'AMBIGUOUS'}")
                incomplete = True
                continue
            source = originals[0]
        base = source.stem
        canonical = source if source.suffix.lower() == ".mp4" else directory / f"{base}.mp4"
        original_srt = directory / f"{base}.srt"
        highlighted = directory / f"{base}_高亮.srt"
        vocabulary = directory / "moyu-ai-vocabulary.json"
        extras = [p.name for p in directory.glob("*.mp4") if p != canonical and not p.name.endswith(".converting.mp4")]

        mp4 = canonical_status(source, canonical)
        srt = "OK" if original_srt.exists() else "MISSING"
        hsrt = highlighted_status(highlighted)
        vocab = json_status(vocabulary)
        statuses = (mp4, srt, hsrt, vocab)
        if any(s.startswith(("MISSING", "INVALID")) for s in statuses):
            incomplete = True
        print(f"E{number:02d}\tMP4={mp4}\tSRT={srt}\tHIGHLIGHT={hsrt}\tVOCAB={vocab}\tEXTRA_MP4={len(extras)}")

    return 2 if args.strict and incomplete else 0


if __name__ == "__main__":
    sys.exit(main())
