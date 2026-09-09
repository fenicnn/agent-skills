#!/usr/bin/env python3
"""moyu-vocab-pipeline / moyu_pipeline.py

一站式生成 Moyu Studio 学习资源：
  * 高亮 SRT（生词彩色 <font>，双行双语）
  * moyu-ai-vocabulary.json（Moyu Studio 词汇导入，schemaVersion 2：
    直接从高亮 SRT 的彩色标签提取 cueIndex + term，不生成任何 ID）
  * MP4（MKV 转 HEVC/AAC/faststart，Apple QuickTime 兼容）

本 skill 只会 spawn 出能被静态分析的子命令（init/highlight/vocab/transcode/finish/status），
其中"标词"与"回填释义"依赖外部 AI 介入（无法自动），标词规则见 SKILL.md；
AI 标完 words.json / corrections.json 后，用 finish 命令一键跑完剩余产物
（vocab 骨架由 finish 自动从刚生成的高亮 SRT 解析，无需中间 prompt md）。

依赖 skill：
  * srt-vocab-highlight（用于生成高亮 SRT）

依赖系统：
  * python（推荐使用 ~/.workbuddy/binaries/python/versions/3.13.12/bin/python3）
  * ffmpeg（仅 transcode 阶段需要）
"""

from __future__ import annotations
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# -------------------- 常量 --------------------

SCRIPT_VERSION = "1.0.0"
SRT_HIGHLIGHT_SCRIPT = Path(
    "/Users/cnn/.workbuddy/skills/srt-vocab-highlight/scripts/srt_highlight_full.py"
)
PYTHON_BIN = Path("/Users/cnn/.workbuddy/binaries/python/versions/3.13.12/bin/python3")

DEFAULT_COLORS = "#ffd400,#ff9a00,#00ff7f,#ff66ff,#00e5ff"  # 5 色霓虹系（term-0..4），适配暗 / 亮 / 户外背景
DEFAULT_TRANSCODE_VCODEC_COPY = True
DEFAULT_TRANSCODE_ACODEC = "aac"
DEFAULT_TRANSCODE_ABITRATE = "192k"

# SRT 时间戳解析
SRT_TIME_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s+-->\s+(\d{2}):(\d{2}):(\d{2}),(\d{3})"
)

# 高亮 SRT 彩色标签（<font color="#ffd400">term</font> 或
# <span style="color: #ffd400;">term</span>），组 1 = font 内容，组 2 = span 内容
HL_COLOR_TAG_RE = re.compile(
    r'<font\s+color="#?[0-9a-fA-F]{3,8}">([^<]*)</font>'
    r'|<span\s+style="color:\s*[^"]*">([^<]*)</span>'
)


# -------------------- 工具函数 --------------------


def iso_time(hhmmss_mmm: str) -> str:
    """'00:00:15,306' → '00:00:15.306'"""
    return hhmmss_mmm.replace(",", ".")


def hms_to_ms(hhmmss_mmm: str) -> int:
    m = SRT_TIME_RE.match(hhmmss_mmm)
    if not m:
        raise ValueError(f"无法解析时间戳: {hhmmss_mmm}")
    h, mn, s, ms, _, _, _, _ = m.groups()
    return int(h) * 3_600_000 + int(mn) * 60_000 + int(s) * 1_000 + int(ms)


def ms_to_hms(ms: int) -> str:
    h = ms // 3_600_000
    ms %= 3_600_000
    mn = ms // 60_000
    ms %= 60_000
    s = ms // 1_000
    ms %= 1_000
    return f"{h:02d}:{mn:02d}:{s:02d},{ms:03d}"


def parse_srt(text: str) -> dict:
    """返回 {idx: (time, english, chinese)}，idx 为 1-based 字幕序号。"""
    # 规范化换行
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", text.strip())
    out = {}
    for blk in blocks:
        lines = [l for l in blk.split("\n") if l.strip()]
        if len(lines) < 3:
            continue
        try:
            idx = int(lines[0].strip())
        except ValueError:
            continue
        time_line = lines[1].strip()
        if "-->" not in time_line:
            continue
        # 默认英上中下；只有中文时只取一行
        en = lines[2]
        cn = lines[3] if len(lines) >= 4 else ""
        out[idx] = (time_line, en, cn)
    return out


def resolve_work_dir(args) -> Path:
    """工作目录：优先 --work-dir，否则放 --srt 同目录的 .moyu-work/。"""
    if args.work_dir:
        return Path(args.work_dir)
    if args.srt:
        return Path(args.srt).parent / ".moyu-work"
    if args.episode:
        return Path(f"./.moyu-work/{args.episode}")
    return Path("./.moyu-work")


def resolve_output_dir(work_dir: Path, episode: str) -> Path:
    """默认把 HL SRT / vocabulary / MP4 放工作目录的 output/。"""
    return work_dir / "output"


def run(cmd: list[str], dry: bool = False, check: bool = True) -> int:
    print(" $", " ".join(str(c) for c in cmd))
    if dry:
        return 0
    r = subprocess.run(cmd, check=check)
    return r.returncode


# -------------------- 子命令：init --------------------


def cmd_init(args) -> int:
    """根据 SRT 建工程骨架：
      .moyu-work/<episode>/
        ├── srt.original.txt         （规范化后的 SRT 备份，AI 标词直接读它）
        ├── eNN_words.json           （空模板）
        ├── eNN_corrections.json     （空模板）
        └── eNN_glosses.json         （空 {}）

    中间产物 moyu-ai-vocabulary-prompt-*.md 默认**不再生成**（新版流程
    vocab 直接从高亮 SRT 解析 cueIndex + term）；如需把标词规则 + 字幕
    打包成 prompt 喂给别的 AI，可加 --gen-prompt。
    """
    if not args.srt:
        print("[init] --srt 必填", file=sys.stderr)
        return 1
    srt_path = Path(args.srt)
    if not srt_path.exists():
        print(f"[init] SRT 不存在: {srt_path}", file=sys.stderr)
        return 1

    work_dir = resolve_work_dir(args)
    work_dir.mkdir(parents=True, exist_ok=True)
    print(f"[init] 工作目录: {work_dir}")

    # 1. 备份规范化 SRT
    srt_text = srt_path.read_text(encoding="utf-8")
    srt_text = srt_text.replace("\r\n", "\n").replace("\r", "\n")
    (work_dir / "srt.original.txt").write_text(srt_text, encoding="utf-8")

    blocks = parse_srt(srt_text)
    print(f"[init] 解析 {len(blocks)} 个 SRT 块")

    if not args.episode:
        # 尝试从 srt 文件名提取 eNN
        m = re.search(r"[Ss]01[Ee](\d+)", srt_path.name)
        args.episode = f"e{int(m.group(1)):02d}" if m else "e00"

    # 2. 生成空 JSON 模板
    empty_words: dict[str, list[str]] = {}
    (work_dir / f"{args.episode}_words.json").write_text(
        json.dumps(empty_words, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (work_dir / f"{args.episode}_corrections.json").write_text(
        json.dumps({}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (work_dir / f"{args.episode}_glosses.json").write_text(
        json.dumps({}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # 3. 默认省略中间 prompt md（可选 --gen-prompt 再生成）
    if getattr(args, "gen_prompt", False):
        prompt_md = build_prompt_md(
            work_dir=work_dir,
            episode=args.episode,
            blocks=blocks,
            output_dir=Path(args.output_dir) if args.output_dir else None,
        )
        print(f"[init] （--gen-prompt）标词 prompt: {prompt_md}")

    print()
    print("=" * 60)
    print("初始化完毕，后续步骤：")
    print(f"  1) AI 按 SKILL.md 标词规则阅读 {work_dir}/srt.original.txt，")
    print(f"     输出 words.json / corrections.json 写回 {work_dir}/")
    print(f"  3) 用 finish 子命令一键跑剩余步骤（vocab 自动从高亮 SRT 解析）：")
    print()
    print(f"     {sys.argv[0]} finish --srt {args.srt} --episode {args.episode}"
          + (f" --mkv {args.mkv}" if args.mkv else ""))
    print("=" * 60)
    return 0


# -------------------- prompt md 生成 --------------------

VOCAB_RULES_HEADER = """\
你是一档英语学习节目的脚本编辑，专注于 CET-4/6 难度的"实用口语/地道短语/俚语"。
请阅读下面提供的双语字幕（SRT 原始块），为每一行英文挑出值得学的高亮生词/短语/俚语/固定搭配。

## 规则

- **难度过滤**：只挑 CET-4 / CET-6 词汇 + 实用口语/俚语/固定搭配（如 fancy、reckon、gut feeling、twirly、chaotic 等）；
  跳过基础词（the/a/is/school/coffee/on a roll）、专有名词（Pink Floyd、Mars）、人名。
- **短语优先于单词**：同一句同时含短语与单词（如 fair enough / enough）时只标短语，避免嵌套。
- **完整匹配**：以字幕文本里的原始大小写/连字符形式为准。例如字幕里有 "stand-up comedian" 不要拆成 "stand-up" 和 "comedian"；
  "after all" 不能拆成 "after" 和 "all"。
- **保持顺序**：生词在 cue 内按首次出现顺序。
- **多生词配色**：cue 内按 term 序号分配 5 色霓虹系：
  - term-0 → `#ffd400`（黄）
  - term-1 → `#ff9a00`（橙）
  - term-2 → `#00ff7f`（亮绿）
  - term-3 → `#ff66ff`（亮粉）
  - term-4 → `#00e5ff`（亮青）
  - 5 个以上 term 循环回 `#ffd400`
- **修正中文翻译**：把错译直接改成正确中文（如 eleven-ish → "十一点左右"、moorish → "好吃到停不下来"）。

## 输出格式（严格裸 JSON 数组，不要包裹 ```json 代码块，不要任何解释）

[
  {
    "id": "cue-{字幕序号}-{start_ms:0>9}-term-{术语序号}",
    "term": "字幕里原样的英文词或短语",
    "color": "#ffd400 / #ff9a00 / #00ff7f / #ff66ff / #00e5ff 五选一",
    "subtitle": {{
      "start": "00:01:23.456",
      "end": "00:01:25.678",
      "english": "字幕原文（完整原句）",
      "chinese": "已应用 corrections 后的中文译文"
    }}
  }
]

注意：
1. **id 中 start_ms 是 0-padded 9 位十进制，字幕序号不补零**（如 cue-33-000045889-term-0）
2. 同一 cue 的多词项 id 的结尾用 -term-0 / -term-1 ...
3. **chinese 字段**：如果该 cue 不需要修正中文翻译，仍要照抄原文；如果需要修正，请直接给修正后的整句中文。

## 双语字幕（idx → time → english → chinese）
"""


def build_prompt_md(
    work_dir: Path, episode: str, blocks: dict, output_dir: Path | None
) -> Path:
    """生成 moyu-ai-vocabulary-prompt-YYYY-MM-DD-HH-MM-SS.md。"""
    ts = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    if output_dir is None:
        output_dir = work_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"moyu-ai-vocabulary-prompt-{ts}.md"

    body_lines = [VOCAB_RULES_HEADER]
    for idx in sorted(blocks.keys()):
        time_line, en, cn = blocks[idx]
        body_lines.append(f"\n### cue {idx}\n")
        body_lines.append(time_line)
        body_lines.append(en)
        body_lines.append(cn)
    body_lines.append("\n\n[")
    body_lines.append("]")
    full_text = "\n".join(body_lines)

    out_path.write_text(full_text, encoding="utf-8")
    print(f"[init] 生成 prompt md: {out_path}")
    return out_path


# -------------------- 子命令：highlight --------------------


def cmd_highlight(args) -> int:
    """调用 srt-vocab-highlight 生成高亮 SRT。"""
    if not PYTHON_BIN.exists():
        print(f"[highlight] python 不存在: {PYTHON_BIN}", file=sys.stderr)
        return 1
    if not SRT_HIGHLIGHT_SCRIPT.exists():
        print(f"[highlight] srt_vocab_highlight 脚本不存在: {SRT_HIGHLIGHT_SCRIPT}", file=sys.stderr)
        return 1
    if not args.srt or not args.episode:
        print("[highlight] --srt --episode 必填", file=sys.stderr)
        return 1

    work_dir = resolve_work_dir(args)
    words = work_dir / f"{args.episode}_words.json"
    corrections = work_dir / f"{args.episode}_corrections.json"
    if not words.exists():
        print(f"[highlight] {words} 不存在，先跑 init", file=sys.stderr)
        return 1

    out_dir = Path(args.output_dir) if args.output_dir else work_dir / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_srt = out_dir / f"{Path(args.srt).stem}_高亮.srt"

    # 默认不传 --glosses，避免空字符串导致单字符 <font> 包裹
    cmd_ = [
        str(PYTHON_BIN),
        str(SRT_HIGHLIGHT_SCRIPT),
        "--input", args.srt,
        "--output", str(out_srt),
        "--words", str(words),
        "--colors", args.colors,
    ]
    if corrections.exists() and corrections.read_text(encoding="utf-8").strip() not in ("", "{}"):
        cmd_ += ["--corrections", str(corrections)]

    rc = run(cmd_, dry=args.dry_run)
    if rc == 0:
        print(f"[highlight] ✓ 高亮 SRT 已写入 {out_srt}")
    return rc


# -------------------- 子命令：vocab --------------------


def extract_terms_from_hl_srt(text: str) -> list[dict]:
    """从高亮 SRT 解析 cueIndex + term（新版 Moyu Studio 映射规则）。

    - cueIndex：词汇所在 SRT 字幕块第一行的字幕序号（数字，非字符串）
    - term：彩色标签（<font color> / <span style="color">）中的英文原文，
      原样保留（不改原形/单复数/时态/标点/连字符）
    - 同一条字幕内重复出现的相同彩色词汇只保留第一次
    - 无彩色标签的单词一律不收录（普通 SRT 无法产生重点词汇）

    返回 [{"cueIndex": int, "term": str}, ...]，按 SRT 块序与行内出现顺序。
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", text.strip())
    entries: list[dict] = []
    for blk in blocks:
        lines = [l for l in blk.split("\n") if l.strip()]
        if len(lines) < 2:
            continue
        try:
            idx = int(lines[0].strip())
        except ValueError:
            continue
        if "-->" not in lines[1]:
            continue
        seen: set[str] = set()
        for m in HL_COLOR_TAG_RE.finditer(blk):
            term = (m.group(1) or m.group(2) or "").strip()
            if not term or term in seen:
                continue
            seen.add(term)
            entries.append({"cueIndex": idx, "term": term})
    return entries


def cmd_vocab(args) -> int:
    """生成 moyu-ai-vocabulary.json 骨架（schemaVersion 2，cueIndex 格式）。

    两种入口（优先 --hl-srt）：
    - --hl-srt 高亮 SRT（推荐，finish 默认链路）：直接解析彩色标签提取
      cueIndex + term，不依赖任何中间 prompt md。
    - --prompt-md legacy 兼容：解析旧 prompt md（v1 cue-XX-XXX-term-N /
      v2 cueIndex 双格式），仅为存量文件保留，新流程不再生成 prompt md。

    注意：本命令不调用 AI，也不会自己写释义；phonetic/partOfSpeech/meaning
    由 AI 在骨架生成后按语境回填（规则见 SKILL.md）。
    """
    if not args.output:
        print("[vocab] --output 必填", file=sys.stderr)
        return 1

    if args.hl_srt:
        hl_srt = Path(args.hl_srt)
        if not hl_srt.exists():
            print(f"[vocab] 高亮 SRT 不存在: {hl_srt}", file=sys.stderr)
            return 1
        terms = extract_terms_from_hl_srt(hl_srt.read_text(encoding="utf-8-sig"))
        if not terms:
            print(
                "[vocab] 高亮 SRT 中未发现任何彩色标签（<font color> / <span style=\"color\">）。"
                "普通 SRT 无法产生重点词汇，AI 也不应自行选择词汇。",
                file=sys.stderr,
            )
            return 1
        vocab = {
            "schemaVersion": 2,
            "vocabulary": [
                {
                    "cueIndex": e["cueIndex"],
                    "term": e["term"],
                    "phonetic": "",
                    "partOfSpeech": "",
                    "meaning": "",
                }
                for e in terms
            ],
        }
    elif args.prompt_md:
        # ---- legacy：从 prompt md 解析（存量文件兼容） ----
        prompt_md = Path(args.prompt_md)
        if not prompt_md.exists():
            print(f"[vocab] prompt md 不存在: {prompt_md}", file=sys.stderr)
            return 1
        text = prompt_md.read_text(encoding="utf-8")
        # 只在 "## 待处理词汇" 之后切段，避免误读规则模板里的占位示例
        # （E02 教训：否则 "原样保留输入 term" 这类占位符会被当词条）
        if "## 待处理词汇" in text:
            text = text.split("## 待处理词汇", 1)[1]

        pat_v2 = re.compile(
            r'"cueIndex":\s*(\d+),\s*\n\s*"term": "([^"]+)"', re.DOTALL
        )
        pat_v1 = re.compile(
            r'"id": "(cue-\d+-\d+-term-\d+)".*?'
            r'"term": "([^"]+)".*?'
            r'"color": "(#[0-9a-f]+)"',
            re.DOTALL,
        )
        entries_v2 = pat_v2.findall(text)
        entries_v1 = pat_v1.findall(text)

        if entries_v2 and len(entries_v2) >= len(entries_v1):
            vocab = {
                "schemaVersion": 2,
                "vocabulary": [
                    {
                        "cueIndex": int(cue_idx),
                        "term": term,
                        "phonetic": "",
                        "partOfSpeech": "",
                        "meaning": "",
                    }
                    for cue_idx, term in entries_v2
                ],
            }
        elif entries_v1:
            vocab = {
                "schemaVersion": 1,
                "vocabulary": [
                    {
                        "id": eid,
                        "term": term,
                        "color": color,
                        "phonetic": "",
                        "partOfSpeech": "",
                        "meaning": "",
                        "block": int(eid.split("-")[1]),
                    }
                    for eid, term, color in entries_v1
                ],
            }
        else:
            print("[vocab] prompt md 内未匹配到 entry（v1/v2 均无），检查格式", file=sys.stderr)
            return 1
    else:
        print("[vocab] --hl-srt（推荐）或 --prompt-md（legacy）必填其一", file=sys.stderr)
        return 1

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(vocab, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"[vocab] ✓ vocab.json 已写入 {out_path}（{len(vocab['vocabulary'])} 条，"
          f"schemaVersion {vocab['schemaVersion']}）")
    return 0


# -------------------- 子命令：transcode --------------------


def cmd_transcode(args) -> int:
    """MKV → MP4：HEVC hvc1 + AAC 192k + faststart，对齐 Apple QuickTime。"""
    if not args.input or not args.output:
        print("[transcode] --input --output 必填", file=sys.stderr)
        return 1
    inp = Path(args.input)
    if not inp.exists():
        print(f"[transcode] 输入不存在: {inp}", file=sys.stderr)
        return 1
    if shutil.which("ffmpeg") is None:
        print("[transcode] 系统没装 ffmpeg（brew install ffmpeg）", file=sys.stderr)
        return 1

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)

    cmd_ = [
        "ffmpeg", "-y", "-i", str(inp),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-c:v", "copy" if args.vcodec_copy else "libx265", "-tag:v", "hvc1",
        "-c:a", args.acodec, "-b:a", args.abitrate,
        "-movflags", "+faststart",
        str(out),
    ]
    rc = run(cmd_, dry=args.dry_run)
    if rc == 0:
        print(f"[transcode] ✓ MP4 已写入 {out}")
    return rc


# -------------------- 子命令：finish --------------------


def cmd_finish(args) -> int:
    """一键跑剩余步骤：highlight + vocab（从 HL SRT 直接解析）+ transcode。"""
    rc = 0

    hl_srt: Path | None = None
    if args.srt:
        rc |= cmd_highlight(_as_sub(args, ["--srt", args.srt]))
        work_dir = resolve_work_dir(args)
        out_dir = Path(args.output_dir) if args.output_dir else work_dir / "output"
        hl_srt = out_dir / f"{Path(args.srt).stem}_高亮.srt"
    else:
        print("[finish] 未传 --srt，跳过 highlight（vocab 优先用刚生成的高亮 SRT）")

    if hl_srt is not None and hl_srt.exists():
        # 新链路：vocab 直接从高亮 SRT 解析 cueIndex + term，无需中间 prompt md
        vocab_out = Path(args.vocab_output) if args.vocab_output else (
            hl_srt.parent / "moyu-ai-vocabulary.json"
        )
        rc |= cmd_vocab(_as_sub(args, ["--hl-srt", str(hl_srt), "--output", str(vocab_out)]))
    elif args.prompt_md and args.vocab_output:
        # legacy：没有 --srt 时仍支持从存量 prompt md 生成 vocab
        vocab_args = _as_sub(args, ["--prompt-md", args.prompt_md, "--output", args.vocab_output])
        rc |= cmd_vocab(vocab_args)
    else:
        print("[finish] 无高亮 SRT 也未传 --prompt-md / --vocab-output，跳过 vocab")

    if args.mkv:
        if not args.mp4_output:
            mp4 = Path(args.mkv).with_suffix(".mp4")
            args.mp4_output = str(mp4)
        tr_args = _as_sub(
            args, ["--input", args.mkv, "--output", args.mp4_output]
        )
        rc |= cmd_transcode(tr_args)
    else:
        print("[finish] 未传 --mkv，跳过 transcode")

    print("[finish] 全部阶段完成" if rc == 0 else f"[finish] 部分阶段失败，rc={rc}")
    return rc


# -------------------- 子命令：status --------------------


def cmd_status(args) -> int:
    """报告各产物是否就位。"""
    work_dir = resolve_work_dir(args)
    print(f"[status] 工作目录: {work_dir}  ({'存在' if work_dir.exists() else '不存在'})")

    if not work_dir.exists():
        return 0

    for name in ("words.json", "corrections.json", "glosses.json"):
        if not args.episode:
            break
        p = work_dir / f"{args.episode}_{name}"
        if not p.exists():
            print(f"  - {p.name}: ✗ 未生成")
            continue
        size = p.stat().st_size
        marker = "✓ 空模板" if size < 5 else f"✓ ({size} bytes)"
        print(f"  - {p.name}: {marker}")

    out_dir = work_dir / "output"
    if out_dir.exists():
        any_out = False
        for f in sorted(out_dir.iterdir()):
            print(f"  - output/{f.name}  ({f.stat().st_size} bytes)")
            any_out = True
        if not any_out:
            print("  - output/: (空)")
    else:
        print("  - output/: (目录不存在)")
    print(f"[status] 提示: 若 words.json 已填好但 output/ 为空，跑 finish 子命令一键产出")
    return 0


# -------------------- helper --------------------


def _as_sub(args, extra: list[str]) -> argparse.Namespace:
    """把当前 args 复制一份并追加 extra，模拟子命令嵌套调用。

    子命令实际会访问的属性若在 finish 的参数表里没定义，这里补默认值，
    避免 AttributeError（finish → highlight/vocab/transcode 嵌套场景）。
    """
    sub = argparse.Namespace(**vars(args))
    for i in range(0, len(extra), 2):
        setattr(sub, extra[i].lstrip("-").replace("-", "_"), extra[i + 1])
    defaults = {
        "dry_run": False,
        "output_dir": None,
        "vcodec_copy": DEFAULT_TRANSCODE_VCODEC_COPY,
        "acodec": DEFAULT_TRANSCODE_ACODEC,
        "abitrate": DEFAULT_TRANSCODE_ABITRATE,
    }
    for name, default in defaults.items():
        if not hasattr(sub, name):
            setattr(sub, name, default)
    return sub


# -------------------- main --------------------


def main():
    p = argparse.ArgumentParser(
        prog="moyu_pipeline",
        description="Moyu Studio 学习资源一站式 pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
典型流程（新版，无需中间 prompt md）：
  1) init --srt foo.srt --episode e03            # 建工程骨架
  2) （AI 按 SKILL.md 标词规则读 srt.original.txt，输出 words/corrections）
  3) finish --srt foo.srt --episode e03           # 一键产出 HL SRT + vocabulary.json
     --mkv foo.mkv                                # 可选，同步转码 MP4
     # vocab 自动从刚生成的高亮 SRT 解析 cueIndex + term（schemaVersion 2）
""",
    )
    sp = p.add_subparsers(dest="cmd", required=True)

    # init
    p_init = sp.add_parser("init", help="建工作目录骨架（默认不生成 prompt md）")
    p_init.add_argument("--srt", help="原始双语 SRT 路径")
    p_init.add_argument("--mkv", help="原始视频路径（可选）")
    p_init.add_argument("--episode", help="剧集 ID，如 e02")
    p_init.add_argument("--work-dir", help="工作目录，默认 <srt_dir>/.moyu-work")
    p_init.add_argument(
        "--output-dir",
        help="--gen-prompt 时 prompt md 的输出目录，默认 <work-dir>",
    )
    p_init.add_argument(
        "--gen-prompt",
        dest="gen_prompt",
        action="store_true",
        help="可选：额外生成标词 prompt md（默认不生成，AI 直接读 srt.original.txt）",
    )
    p_init.set_defaults(func=cmd_init)

    # highlight
    p_hl = sp.add_parser("highlight", help="生成高亮 SRT")
    p_hl.add_argument("--srt", required=True)
    p_hl.add_argument("--episode", required=True)
    p_hl.add_argument("--work-dir")
    p_hl.add_argument("--output-dir")
    p_hl.add_argument("--colors", default=DEFAULT_COLORS)
    p_hl.add_argument("--dry-run", action="store_true")
    p_hl.set_defaults(func=cmd_highlight)

    # vocab
    p_vb = sp.add_parser(
        "vocab",
        help="从高亮 SRT 解析 cueIndex+term 生成 vocabulary.json（legacy: --prompt-md）",
    )
    p_vb.add_argument(
        "--hl-srt",
        help="高亮 SRT 路径（推荐：直接解析彩色标签，提取 cueIndex + term）",
    )
    p_vb.add_argument(
        "--prompt-md",
        help="legacy：存量 prompt md 路径（v1/v2 双格式兼容，新流程不再生成 prompt md）",
    )
    p_vb.add_argument("--output", required=True, help="moyu-ai-vocabulary.json 路径")
    p_vb.add_argument("--dry-run", action="store_true")
    p_vb.set_defaults(func=cmd_vocab)

    # transcode
    p_tc = sp.add_parser("transcode", help="MKV → MP4（HEVC/AAC/faststart）")
    p_tc.add_argument("--input", required=True)
    p_tc.add_argument("--output", required=True)
    p_tc.add_argument("--vcodec-copy", dest="vcodec_copy", action="store_true", default=True,
                     help="视频直接 copy（推荐，原码流兼容），默认开")
    p_tc.add_argument("--acodec", default=DEFAULT_TRANSCODE_ACODEC)
    p_tc.add_argument("--abitrate", default=DEFAULT_TRANSCODE_ABITRATE)
    p_tc.add_argument("--dry-run", action="store_true")
    p_tc.set_defaults(func=cmd_transcode)

    # finish
    p_fin = sp.add_parser(
        "finish",
        help="一键跑 highlight + vocab（从 HL SRT 解析）+ transcode（前提：words/corrections 已就绪）",
    )
    p_fin.add_argument("--srt")
    p_fin.add_argument("--mkv")
    p_fin.add_argument("--episode")
    p_fin.add_argument("--work-dir")
    p_fin.add_argument("--output-dir",
                       help="高亮 SRT 输出目录，默认 <work-dir>/output")
    p_fin.add_argument(
        "--prompt-md",
        help="legacy：无 --srt 时从存量 prompt md 生成 vocab（有 --srt 时忽略）",
    )
    p_fin.add_argument("--vocab-output",
                       help="vocabulary.json 输出路径，默认 <HL SRT 同目录>/moyu-ai-vocabulary.json")
    p_fin.add_argument("--mp4-output")
    p_fin.add_argument("--colors", default=DEFAULT_COLORS)
    p_fin.set_defaults(func=cmd_finish)

    # status
    p_st = sp.add_parser("status", help="检查工作目录各产物是否就位")
    p_st.add_argument("--srt")
    p_st.add_argument("--episode")
    p_st.add_argument("--work-dir")
    p_st.set_defaults(func=cmd_status)

    args = p.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
