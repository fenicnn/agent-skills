#!/usr/bin/env python3
"""Idempotently create one validated canonical MP4 per requested MKV episode."""

import argparse
import os
import subprocess
import sys
from pathlib import Path

from audit_media_batch import canonical_status, episode_numbers


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--episodes", required=True, help="e.g. 4-10 or 4,6,8-10")
    args = parser.parse_args()

    created = skipped = promoted = 0
    for number in episode_numbers(args.episodes):
        directory = args.root / f"第{number}集"
        sources = sorted(directory.glob("*.mkv")) if directory.is_dir() else []
        if len(sources) != 1:
            print(f"E{number:02d}: expected exactly one MKV, found {len(sources)}", file=sys.stderr)
            return 2
        source = sources[0]
        target = source.with_suffix(".mp4")
        temporary = source.with_name(f"{source.stem}.converting.mp4")

        status = canonical_status(source, target)
        if status.startswith("OK("):
            print(f"E{number:02d}: skip existing canonical MP4 ({status})")
            skipped += 1
            continue
        if target.exists():
            print(f"E{number:02d}: refusing to overwrite {target.name} ({status})", file=sys.stderr)
            return 2

        if temporary.exists():
            temp_status = canonical_status(source, temporary)
            if temp_status.startswith("OK("):
                os.replace(temporary, target)
                print(f"E{number:02d}: promoted valid temporary file")
                promoted += 1
                continue
            print(f"E{number:02d}: stale invalid temporary file requires review ({temp_status})", file=sys.stderr)
            return 2

        pipeline = Path(__file__).with_name("moyu_pipeline.py")
        command = [
            sys.executable, str(pipeline), "transcode",
            "--input", str(source), "--output", str(target),
        ]
        print(f"E{number:02d}: creating {target.name}")
        result = subprocess.run(command, check=False)
        if result.returncode != 0:
            print(f"E{number:02d}: transcode failed", file=sys.stderr)
            return result.returncode or 1
        target_status = canonical_status(source, target)
        if not target_status.startswith("OK("):
            print(f"E{number:02d}: generated MP4 failed validation ({target_status})", file=sys.stderr)
            return 2
        created += 1
        print(f"E{number:02d}: complete ({target_status})")

    print(f"summary: created={created} promoted={promoted} skipped={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
