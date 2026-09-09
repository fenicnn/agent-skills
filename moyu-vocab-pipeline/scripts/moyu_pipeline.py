#!/usr/bin/env python3
"""moyu-vocab-pipeline / moyu_pipeline.py

一站式生成 Moyu Studio 学习资源：
  * 高亮 SRT（生词彩色 <font>，双行双语）
  * moyu-ai-vocabulary.json（Moyu Studio 词汇导入，schemaVersion 2：
    直接从高亮 SRT 的彩色标签提取 cueIndex + term，不生成任何 ID）
  * 可选兼容 MP4（仅非 MP4 输入默认转码；已有 MP4 默认原样复用）

本 skill 提供 init/apply-selection/highlight/vocab/transcode/finish/status 子命令，
其中"标词"与"回填释义"依赖外部 AI 介入（无法自动），标词规则见 SKILL.md；
AI 标完 words.json / corrections.json 后，用 finish 命令一键跑完剩余产物
（vocab 骨架与 AI 释义任务由 finish 从刚生成的高亮 SRT 解析）。

依赖 skill：
  * srt-vocab-highlight（用于生成高亮 SRT）

依赖系统：
  * python 3.10+
  * ffmpeg（仅 transcode 阶段需要）
"""

from __future__ import annotations
import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

# -------------------- 常量 --------------------

SCRIPT_VERSION = "2.1.0"
SKILL_DIR = Path(__file__).resolve().parents[1]
HIGHLIGHT_CANDIDATES = (
    Path(os.environ["MOYU_HIGHLIGHT_SCRIPT"]) if os.environ.get("MOYU_HIGHLIGHT_SCRIPT") else None,
    SKILL_DIR.parent / "srt-vocab-highlight/scripts/srt_highlight_full.py",
    Path.home() / ".workbuddy/skills/srt-vocab-highlight/scripts/srt_highlight_full.py",
)

DEFAULT_COLORS = "#ffd400,#ff9a00,#00ff7f,#ff66ff,#00e5ff"  # 5 色霓虹系（term-0..4），适配暗 / 亮 / 户外背景
DEFAULT_TRANSCODE_ACODEC = "aac"
DEFAULT_TRANSCODE_ABITRATE = "192k"

# SRT 时间戳解析
SRT_TIME_RE = re.compile(
    r"^(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})"
)
TAG_RE = re.compile(r"<[^>]+>")
CJK_RE = re.compile(r"[\u3400-\u9fff]")
LATIN_RE = re.compile(r"[A-Za-z]")
FONT_RE = re.compile(r"<font\b([^>]*)>([\s\S]*?)</font>", re.IGNORECASE)
SPAN_RE = re.compile(r"<span\b([^>]*)>([\s\S]*?)</span>", re.IGNORECASE)


# -------------------- 工具函数 --------------------


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


def infer_episode(srt_path: Path, supplied: str | None) -> str:
    if supplied:
        return supplied.lower()
    match = re.search(r"[Ss]\d+[Ee](\d+)", srt_path.name)
    if not match:
        raise ValueError("无法从文件名推断集数，请显式传入 --episode（如 e04）")
    return f"e{int(match.group(1)):02d}"


def resolve_highlight_script() -> Path | None:
    return next((path for path in HIGHLIGHT_CANDIDATES if path and path.exists()), None)


def read_attribute(attributes: str, name: str) -> str | None:
    expression = re.compile(
        rf"\b{re.escape(name)}\s*=\s*(?:\"([^\"]+)\"|'([^']+)'|([^\s>]+))",
        re.IGNORECASE,
    )
    match = expression.search(attributes)
    return next((value for value in match.groups() if value is not None), None) if match else None


def read_style_color(style: str | None) -> str | None:
    if not style:
        return None
    match = re.search(r"(?:^|;)\s*color\s*:\s*([^;]+)", style, re.IGNORECASE)
    return match.group(1).strip() if match else None


def strip_subtitle_tags(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(TAG_RE.sub("", value))).strip()


def normalize_term(value: str) -> str:
    return re.sub(
        r"\s+", " ", unicodedata.normalize("NFKC", value).lower().replace("‘", "'").replace("’", "'")
    ).strip()


def is_english_term(value: str) -> bool:
    latin = len(LATIN_RE.findall(value))
    cjk = len(CJK_RE.findall(value))
    return latin > 0 and latin >= cjk


def extract_tagged_terms(content: str) -> list[dict]:
    """Mirror pc/macos/src/shared/srt.ts extractHighlightedTerms()."""
    matches: list[tuple[int, str, str]] = []
    for pattern, tag_name in ((FONT_RE, "font"), (SPAN_RE, "span")):
        for match in pattern.finditer(content):
            attributes, inner = match.groups()
            color = (
                read_attribute(attributes, "color")
                if tag_name == "font"
                else read_style_color(read_attribute(attributes, "style"))
            )
            term = strip_subtitle_tags(inner)
            if color and term and is_english_term(term):
                matches.append((match.start(), term, color))

    seen: set[str] = set()
    result: list[dict] = []
    for _, term, color in sorted(matches, key=lambda item: item[0]):
        key = normalize_term(term)
        if key in seen:
            continue
        seen.add(key)
        result.append({"term": term, "color": color})
    return result


def parse_highlighted_srt(text: str) -> list[dict]:
    normalized = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    cues: list[dict] = []
    for block_index, block in enumerate(re.split(r"\n{2,}", normalized)):
        lines = [line.strip() for line in block.split("\n")]
        timing_index = next((i for i, line in enumerate(lines) if SRT_TIME_RE.match(line)), -1)
        if timing_index < 0:
            continue
        timing = SRT_TIME_RE.match(lines[timing_index])
        if not timing:
            continue
        try:
            cue_index = int(lines[0])
        except ValueError:
            cue_index = block_index + 1
        content_lines = lines[timing_index + 1:]
        english: list[str] = []
        chinese: list[str] = []
        for raw_line in content_lines:
            line = strip_subtitle_tags(raw_line)
            if not line:
                continue
            if CJK_RE.search(line):
                chinese.append(line)
            elif LATIN_RE.search(line):
                english.append(line)
        cues.append({
            "cueIndex": cue_index,
            "startKey": "".join(timing.groups()[:4]),
            "start": f"{timing.group(1).zfill(2)}:{timing.group(2)}:{timing.group(3)}.{timing.group(4)}",
            "end": f"{timing.group(5).zfill(2)}:{timing.group(6)}:{timing.group(7)}.{timing.group(8)}",
            "english": " ".join(english),
            "chinese": " ".join(chinese),
            "terms": extract_tagged_terms("\n".join(content_lines)),
        })
    return cues


def resolve_work_dir(args) -> Path:
    """工作目录：优先 --work-dir，否则放 --srt 同目录的 .moyu-work/。"""
    if args.work_dir:
        return Path(args.work_dir)
    if args.srt:
        episode = args.episode or "unknown"
        return Path(args.srt).parent / ".moyu-work" / episode
    if args.episode:
        return Path(f"./.moyu-work/{args.episode}")
    return Path("./.moyu-work")


def run(cmd: list[str], dry: bool = False) -> int:
    print(" $", " ".join(str(c) for c in cmd))
    if dry:
        return 0
    r = subprocess.run(cmd, check=False)
    return r.returncode


def refuse_existing(path: Path, force: bool, label: str) -> bool:
    if path.exists() and not force:
        print(f"[{label}] 目标已存在，拒绝覆盖: {path}（需要覆盖时传 --force）", file=sys.stderr)
        return True
    return False


def should_transcode_video(path: str | Path, transcode_mp4: bool = False) -> bool:
    """非 MP4 默认转码；MP4 只在调用方明确要求时重编码。"""
    return Path(path).suffix.casefold() != ".mp4" or transcode_mp4


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

    try:
        args.episode = infer_episode(srt_path, args.episode)
    except ValueError as error:
        print(f"[init] {error}", file=sys.stderr)
        return 1
    work_dir = resolve_work_dir(args)
    work_dir.mkdir(parents=True, exist_ok=True)
    print(f"[init] 工作目录: {work_dir}")

    # 1. 备份规范化 SRT
    srt_text = srt_path.read_text(encoding="utf-8")
    srt_text = srt_text.replace("\r\n", "\n").replace("\r", "\n")
    original_path = work_dir / "srt.original.txt"
    init_paths = [
        original_path,
        work_dir / f"{args.episode}_words.json",
        work_dir / f"{args.episode}_corrections.json",
        work_dir / f"{args.episode}_glosses.json",
    ]
    existing = [path for path in init_paths if path.exists()]
    if existing and not args.force:
        print("[init] 工程已存在，拒绝覆盖（需要重建时传 --force）：", file=sys.stderr)
        for path in existing:
            print(f"  - {path}", file=sys.stderr)
        return 1
    original_path.write_text(srt_text, encoding="utf-8")

    blocks = parse_srt(srt_text)
    print(f"[init] 解析 {len(blocks)} 个 SRT 块")

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
        prompt_md = build_selection_prompt(
            work_dir=work_dir,
            episode=args.episode,
            srt_text=srt_text,
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
          + (f" --video {args.mkv}" if args.mkv else ""))
    print("=" * 60)
    return 0


def cmd_apply_selection(args) -> int:
    """把外部 AI 的标词结果拆分成 highlighter 所需的两个 JSON。"""
    source = Path(args.input)
    if not source.exists():
        print(f"[apply-selection] 输入不存在: {source}", file=sys.stderr)
        return 1
    try:
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"[apply-selection] JSON 无效: {error}", file=sys.stderr)
        return 1
    if not isinstance(payload, dict) or not isinstance(payload.get("words"), dict) or not isinstance(payload.get("corrections"), dict):
        print("[apply-selection] 必须包含对象字段 words 和 corrections", file=sys.stderr)
        return 1
    for cue, terms in payload["words"].items():
        if not str(cue).isdigit() or not isinstance(terms, list) or not all(isinstance(term, str) and term for term in terms):
            print(f"[apply-selection] words[{cue!r}] 格式无效", file=sys.stderr)
            return 1
    for cue, correction in payload["corrections"].items():
        if not str(cue).isdigit() or not isinstance(correction, str) or not correction:
            print(f"[apply-selection] corrections[{cue!r}] 格式无效", file=sys.stderr)
            return 1
    work_dir = Path(args.work_dir)
    original = work_dir / "srt.original.txt"
    if not original.exists():
        print(f"[apply-selection] 缺少 {original}，请先运行 init", file=sys.stderr)
        return 1
    cue_by_index = {
        str(cue["cueIndex"]): cue
        for cue in parse_highlighted_srt(original.read_text(encoding="utf-8-sig"))
    }
    for cue, terms in payload["words"].items():
        source_cue = cue_by_index.get(str(cue))
        if not source_cue:
            print(f"[apply-selection] 字幕序号不存在: {cue}", file=sys.stderr)
            return 1
        seen: set[str] = set()
        for term in terms:
            if term not in source_cue["english"]:
                print(f"[apply-selection] words[{cue!r}] 的词汇不是英文字幕原样子串: {term!r}", file=sys.stderr)
                return 1
            key = normalize_term(term)
            if key in seen:
                print(f"[apply-selection] words[{cue!r}] 包含重复词汇: {term!r}", file=sys.stderr)
                return 1
            seen.add(key)
    for cue in payload["corrections"]:
        if str(cue) not in cue_by_index:
            print(f"[apply-selection] corrections 的字幕序号不存在: {cue}", file=sys.stderr)
            return 1
    outputs = [
        (work_dir / f"{args.episode}_words.json", payload["words"]),
        (work_dir / f"{args.episode}_corrections.json", payload["corrections"]),
    ]
    blocked: list[Path] = []
    if not args.force:
        for path, _ in outputs:
            if not path.exists():
                continue
            try:
                existing = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                existing = "invalid"
            if existing != {}:
                blocked.append(path)
    if blocked:
        print("[apply-selection] 已有非空结果，拒绝覆盖（确认后可传 --force）：", file=sys.stderr)
        for path in blocked:
            print(f"  - {path}", file=sys.stderr)
        return 1
    if args.dry_run:
        print(f"[apply-selection] dry-run：{len(payload['words'])} 个 cue 含重点词汇")
        return 0
    for path, value in outputs:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[apply-selection] ✓ {path}")
    return 0


# -------------------- prompt md 生成 --------------------

SELECTION_RULES_HEADER = """\
你是一档英语学习节目的脚本编辑，专注于 CET-4/6 难度的"实用口语/地道短语/俚语"。
请阅读下面提供的双语字幕（SRT 原始块），为每一行英文挑出值得学的高亮生词/短语/俚语/固定搭配。

## 规则

- **难度过滤**：只挑 CET-4 / CET-6 词汇 + 实用口语/俚语/固定搭配（如 fancy、reckon、gut feeling、twirly、chaotic 等）；
  跳过基础词（the/a/is/school/coffee/on a roll）、专有名词（Pink Floyd、Mars）、人名。
- **短语优先于单词**：同一句同时含短语与单词（如 fair enough / enough）时只标短语，避免嵌套。
- **完整匹配**：以字幕文本里的原始大小写/连字符形式为准。例如字幕里有 "stand-up comedian" 不要拆成 "stand-up" 和 "comedian"；
  "after all" 不能拆成 "after" 和 "all"。
- **保持顺序**：生词在 cue 内按首次出现顺序。
- **修正中文翻译**：把错译直接改成正确中文（如 eleven-ish → "十一点左右"、moorish → "好吃到停不下来"）。

## 输出格式（严格裸 JSON 对象，不要 Markdown 代码块或解释）

{
  "words": {"字幕序号": ["英文原样词汇", "英文原样短语"]},
  "corrections": {"仅需修正的字幕序号": "修正后的整句中文"}
}

没有词汇或修正的 cue 不要写入；字幕序号作为 JSON 字符串键。term 必须是对应英文字幕的原样子串。

## 原始双语 SRT
"""


def build_selection_prompt(
    work_dir: Path, episode: str, srt_text: str, output_dir: Path | None
) -> Path:
    """生成能直接回填 words/corrections 的标词任务。"""
    ts = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    if output_dir is None:
        output_dir = work_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"moyu-vocab-selection-prompt-{episode}-{ts}.md"
    out_path.write_text(f"{SELECTION_RULES_HEADER}\n\n{srt_text}", encoding="utf-8")
    print(f"[init] 生成 prompt md: {out_path}")
    return out_path


# -------------------- 子命令：highlight --------------------


def cmd_highlight(args) -> int:
    """调用 srt-vocab-highlight 生成高亮 SRT。"""
    highlight_script = resolve_highlight_script()
    if not highlight_script:
        print("[highlight] 找不到 srt-vocab-highlight；可通过 MOYU_HIGHLIGHT_SCRIPT 指定", file=sys.stderr)
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

    # .moyu-work 只存中间状态；最终 SRT 默认回到用户指定的原 SRT 目录。
    out_dir = (
        Path(args.output_dir)
        if args.output_dir
        else Path(args.srt).parent if args.srt else work_dir / "output"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    out_srt = out_dir / f"{Path(args.srt).stem}_高亮.srt"
    if refuse_existing(out_srt, args.force, "highlight"):
        return 1

    # 默认不传 --glosses，避免空字符串导致单字符 <font> 包裹
    cmd_ = [
        sys.executable,
        str(highlight_script),
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
    return [
        {"cueIndex": cue["cueIndex"], "term": term["term"]}
        for cue in parse_highlighted_srt(text)
        for term in cue["terms"]
    ]


def build_vocabulary_payload(cues: list[dict], schema_version: int) -> tuple[dict, list[dict]]:
    vocabulary: list[dict] = []
    prompt_items: list[dict] = []
    for cue in cues:
        for term_index, term in enumerate(cue["terms"]):
            identity = (
                {"id": f"cue-{cue['cueIndex']}-{cue['startKey']}-term-{term_index}"}
                if schema_version == 1 else {"cueIndex": cue["cueIndex"]}
            )
            vocabulary.append({
                **identity,
                "term": term["term"],
                "phonetic": "",
                "partOfSpeech": "",
                "meaning": "",
            })
            prompt_items.append({
                **identity,
                "term": term["term"],
                "color": term["color"],
                "subtitle": {
                    "start": cue["start"], "end": cue["end"],
                    "english": cue["english"], "chinese": cue["chinese"],
                },
            })
    return {"schemaVersion": schema_version, "vocabulary": vocabulary}, prompt_items


def build_gloss_prompt(schema_version: int, items: list[dict]) -> str:
    identity = "id" if schema_version == 1 else "cueIndex"
    identity_example = '"原样保留输入 id"' if schema_version == 1 else "62"
    return f"""# Moyu Studio AI 词汇生成任务

根据待处理词汇所在的双语字幕语境，填写简洁准确的英语学习信息。

规则：
1. 不新增、删除或重新排序词汇。
2. 原样保留每项的 `{identity}` 和 `term`。
3. `phonetic` 使用常见 IPA；短语不适合标注时填空字符串。
4. `partOfSpeech` 使用 `n.`、`v.`、`adj.`、`adv.` 或 `phrase`。
5. `meaning` 填当前语境下简洁自然的中文释义，不能为空。
6. 只输出合法 UTF-8 JSON，不要 Markdown 代码块或说明。
7. 输出结构：{{"schemaVersion": {schema_version}, "vocabulary": [{{"{identity}": {identity_example}, "term": "原样保留", "phonetic": "", "partOfSpeech": "phrase", "meaning": "语境释义"}}]}}

## 待处理词汇

{json.dumps(items, ensure_ascii=False, indent=2)}
"""


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
        cues = parse_highlighted_srt(hl_srt.read_text(encoding="utf-8-sig"))
        if not any(cue["terms"] for cue in cues):
            print(
                "[vocab] 高亮 SRT 中未发现任何彩色标签（<font color> / <span style=\"color\">）。"
                "普通 SRT 无法产生重点词汇，AI 也不应自行选择词汇。",
                file=sys.stderr,
            )
            return 1
        vocab, prompt_items = build_vocabulary_payload(cues, args.schema)
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
    prompt_path = None
    if args.hl_srt and not args.no_prompt:
        prompt_path = Path(args.prompt_output) if args.prompt_output else out_path.with_name("moyu-ai-vocabulary-prompt.md")
    targets = [out_path, *([prompt_path] if prompt_path else [])]
    if any(path.exists() for path in targets) and not args.force:
        print("[vocab] 目标已存在，拒绝产生部分输出（需要覆盖时传 --force）：", file=sys.stderr)
        for path in targets:
            if path.exists():
                print(f"  - {path}", file=sys.stderr)
        return 1
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if args.dry_run:
        print(f"[vocab] dry-run：将生成 {len(vocab['vocabulary'])} 条 schemaVersion {vocab['schemaVersion']} 词汇")
        return 0
    out_path.write_text(
        json.dumps(vocab, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"[vocab] ✓ vocab.json 已写入 {out_path}（{len(vocab['vocabulary'])} 条，"
          f"schemaVersion {vocab['schemaVersion']}）")
    if prompt_path:
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(build_gloss_prompt(vocab["schemaVersion"], prompt_items), encoding="utf-8")
        print(f"[vocab] ✓ AI 释义任务已写入 {prompt_path}")
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
    if inp.resolve() == out.resolve():
        print("[transcode] 输入和输出不能是同一个文件", file=sys.stderr)
        return 1
    if refuse_existing(out, args.force, "transcode"):
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)

    codec = ""
    if shutil.which("ffprobe"):
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name", "-of", "default=nw=1:nk=1", str(inp)],
            check=False, capture_output=True, text=True,
        )
        codec = probe.stdout.strip().lower()

    mode = args.video_mode
    if mode == "auto":
        mode = "copy" if codec in {"hevc", "h264"} else "hevc"
    temporary = out.with_name(f".{out.stem}.moyu-part-{os.getpid()}{out.suffix}")
    if temporary.exists():
        temporary.unlink()

    if mode == "copy":
        video_args = ["-c:v", "copy"]
        if codec == "hevc":
            video_args += ["-tag:v", "hvc1"]
        elif codec == "h264":
            video_args += ["-tag:v", "avc1"]
    else:
        video_args = ["-c:v", args.video_encoder, "-tag:v", "hvc1"]
        video_args += ["-b:v", args.video_bitrate] if "videotoolbox" in args.video_encoder else ["-crf", args.crf]

    cmd_ = [
        "ffmpeg", "-y", "-i", str(inp),
        "-map", "0:v:0", "-map", args.audio_map,
        *video_args,
        "-c:a", args.acodec, "-b:a", args.abitrate,
        "-movflags", "+faststart",
        str(temporary),
    ]
    rc = run(cmd_, dry=args.dry_run)
    if rc == 0:
        if not args.dry_run:
            temporary.replace(out)
        print(f"[transcode] ✓ MP4 已写入 {out}")
    elif temporary.exists():
        temporary.unlink()
    return rc


# -------------------- 子命令：finish --------------------


def cmd_finish(args) -> int:
    """一键跑剩余步骤：highlight + vocab（从 HL SRT 直接解析）+ transcode。"""
    if not args.srt:
        print("[finish] --srt 必填；没有工作可执行时不会报告成功", file=sys.stderr)
        return 1
    srt_path = Path(args.srt)
    try:
        args.episode = infer_episode(srt_path, args.episode)
    except ValueError as error:
        print(f"[finish] {error}", file=sys.stderr)
        return 1

    hl_srt: Path | None = None
    rc = cmd_highlight(_as_sub(args, ["--srt", args.srt]))
    if rc != 0:
        print("[finish] highlight 失败，已停止后续阶段", file=sys.stderr)
        return rc
    work_dir = resolve_work_dir(args)
    out_dir = Path(args.output_dir) if args.output_dir else srt_path.parent
    hl_srt = out_dir / f"{Path(args.srt).stem}_高亮.srt"

    if hl_srt is not None and hl_srt.exists():
        # 新链路：vocab 直接从高亮 SRT 解析 cueIndex + term，无需中间 prompt md
        vocab_out = Path(args.vocab_output) if args.vocab_output else (
            hl_srt.parent / "moyu-ai-vocabulary.json"
        )
        rc = cmd_vocab(_as_sub(args, ["--hl-srt", str(hl_srt), "--output", str(vocab_out)]))
        if rc != 0:
            print("[finish] vocab 失败，已停止后续阶段", file=sys.stderr)
            return rc
    else:
        print("[finish] 当前执行未生成高亮 SRT，已停止", file=sys.stderr)
        return 1

    if args.mkv:
        if should_transcode_video(args.mkv, args.transcode_mp4):
            if not args.mp4_output:
                mp4 = Path(args.mkv).with_suffix(".mp4")
                args.mp4_output = str(mp4)
            tr_args = _as_sub(
                args, ["--input", args.mkv, "--output", args.mp4_output]
            )
            rc = cmd_transcode(tr_args)
            if rc != 0:
                print("[finish] transcode 失败", file=sys.stderr)
                return rc
        else:
            print(f"[finish] 输入已是 MP4，原样复用并跳过 transcode: {args.mkv}")
    else:
        print("[finish] 未传 --mkv，跳过 transcode")

    print("[finish] 所有请求的阶段均已完成")
    return rc


# -------------------- 子命令：status --------------------


def cmd_status(args) -> int:
    """报告各产物是否就位。"""
    if args.srt and not args.episode:
        try:
            args.episode = infer_episode(Path(args.srt), None)
        except ValueError as error:
            print(f"[status] {error}", file=sys.stderr)
            return 1
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
        try:
            value = json.loads(p.read_text(encoding="utf-8-sig"))
            marker = "✓ 空模板" if value == {} else f"✓ {len(value)} 个 cue"
        except (OSError, json.JSONDecodeError):
            marker = "✗ JSON 无效"
        print(f"  - {p.name}: {marker}")

    out_dir = (
        Path(args.output_dir)
        if args.output_dir
        else Path(args.srt).parent if args.srt else work_dir / "output"
    )
    if out_dir.exists():
        any_out = False
        for f in sorted(out_dir.iterdir()):
            print(f"  - {f}  ({f.stat().st_size} bytes)")
            any_out = True
        if not any_out:
            print(f"  - {out_dir}: (空)")
    else:
        print(f"  - {out_dir}: (目录不存在)")
    print("[status] 提示: 若 words.json 已填好但交付目录没有产物，跑 finish 子命令一键产出")
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
        "force": False,
        "schema": 2,
        "prompt_output": None,
        "no_prompt": False,
        "video_mode": "auto",
        "video_encoder": "hevc_videotoolbox" if sys.platform == "darwin" else "libx265",
        "video_bitrate": "8M",
        "crf": "23",
        "audio_map": "0:a:0?",
        "acodec": DEFAULT_TRANSCODE_ACODEC,
        "abitrate": DEFAULT_TRANSCODE_ABITRATE,
        "transcode_mp4": False,
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
典型流程：
  1) init --srt foo.srt --episode e03            # 建工程骨架
  2) （AI 标词，直接写 words/corrections；或用 apply-selection 导入结果）
  3) finish --srt foo.srt --episode e03           # 产出 HL SRT + v2 JSON + AI 释义任务
     --video foo.mkv                              # 可选；非 MP4 才默认转码
     # vocab 自动从刚生成的高亮 SRT 解析 cueIndex + term（schemaVersion 2）
""",
    )
    sp = p.add_subparsers(dest="cmd", required=True)

    # init
    p_init = sp.add_parser("init", help="建工作目录骨架（默认不生成 prompt md）")
    p_init.add_argument("--srt", help="原始双语 SRT 路径")
    p_init.add_argument("--mkv", "--video", dest="mkv", help="原始视频路径（可选）")
    p_init.add_argument("--episode", help="剧集 ID，如 e02")
    p_init.add_argument("--work-dir", help="工作目录，默认 <srt_dir>/.moyu-work/<episode>")
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
    p_init.add_argument("--force", action="store_true", help="覆盖已有工程文件")
    p_init.set_defaults(func=cmd_init)

    p_selection = sp.add_parser("apply-selection", help="导入 AI 标词结果，生成 words/corrections")
    p_selection.add_argument("--input", required=True, help="AI 输出 JSON")
    p_selection.add_argument("--episode", required=True, help="剧集 ID，如 e04")
    p_selection.add_argument("--work-dir", required=True)
    p_selection.add_argument("--force", action="store_true")
    p_selection.add_argument("--dry-run", action="store_true")
    p_selection.set_defaults(func=cmd_apply_selection)

    # highlight
    p_hl = sp.add_parser("highlight", help="生成高亮 SRT")
    p_hl.add_argument("--srt", required=True)
    p_hl.add_argument("--episode", required=True)
    p_hl.add_argument("--work-dir")
    p_hl.add_argument("--output-dir")
    p_hl.add_argument("--colors", default=DEFAULT_COLORS)
    p_hl.add_argument("--force", action="store_true")
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
    p_vb.add_argument("--schema", type=int, choices=[1, 2], default=2,
                      help="Studio 导入格式；默认 2，旧客户端可显式选择 1")
    p_vb.add_argument("--prompt-output", help="AI 释义任务 md 路径")
    p_vb.add_argument("--no-prompt", action="store_true", help="只生成 JSON 骨架")
    p_vb.add_argument("--force", action="store_true")
    p_vb.add_argument("--dry-run", action="store_true")
    p_vb.set_defaults(func=cmd_vocab)

    # transcode
    p_tc = sp.add_parser("transcode", help="MKV → MP4（HEVC/AAC/faststart）")
    p_tc.add_argument("--input", required=True)
    p_tc.add_argument("--output", required=True)
    p_tc.add_argument("--video-mode", choices=["auto", "copy", "hevc"], default="auto",
                      help="auto 会复制 H.264/HEVC，其他编码转 HEVC")
    p_tc.add_argument("--video-encoder", default="hevc_videotoolbox" if sys.platform == "darwin" else "libx265")
    p_tc.add_argument("--video-bitrate", default="8M", help="VideoToolbox 目标码率")
    p_tc.add_argument("--crf", default="23", help="libx265 CRF")
    p_tc.add_argument("--audio-map", default="0:a:0?", help="ffmpeg 音轨 map，如 0:a:1?")
    p_tc.add_argument("--acodec", default=DEFAULT_TRANSCODE_ACODEC)
    p_tc.add_argument("--abitrate", default=DEFAULT_TRANSCODE_ABITRATE)
    p_tc.add_argument("--force", action="store_true")
    p_tc.add_argument("--dry-run", action="store_true")
    p_tc.set_defaults(func=cmd_transcode)

    # finish
    p_fin = sp.add_parser(
        "finish",
        help="一键跑 highlight + vocab（从 HL SRT 解析）+ transcode（前提：words/corrections 已就绪）",
    )
    p_fin.add_argument("--srt")
    p_fin.add_argument("--mkv", "--video", dest="mkv", help="原始视频；已有 MP4 默认原样复用")
    p_fin.add_argument("--episode")
    p_fin.add_argument("--work-dir")
    p_fin.add_argument("--output-dir",
                       help="高亮 SRT/词汇任务输出目录，默认原 SRT 所在目录")
    p_fin.add_argument("--vocab-output",
                       help="vocabulary.json 输出路径，默认 <HL SRT 同目录>/moyu-ai-vocabulary.json")
    p_fin.add_argument("--mp4-output")
    p_fin.add_argument("--transcode-mp4", action="store_true",
                       help="即使输入已是 MP4 也显式重编码（默认跳过）")
    p_fin.add_argument("--colors", default=DEFAULT_COLORS)
    p_fin.add_argument("--schema", type=int, choices=[1, 2], default=2)
    p_fin.add_argument("--prompt-output")
    p_fin.add_argument("--no-prompt", action="store_true")
    p_fin.add_argument("--video-mode", choices=["auto", "copy", "hevc"], default="auto")
    p_fin.add_argument("--video-encoder", default="hevc_videotoolbox" if sys.platform == "darwin" else "libx265")
    p_fin.add_argument("--video-bitrate", default="8M")
    p_fin.add_argument("--crf", default="23")
    p_fin.add_argument("--audio-map", default="0:a:0?")
    p_fin.add_argument("--acodec", default=DEFAULT_TRANSCODE_ACODEC)
    p_fin.add_argument("--abitrate", default=DEFAULT_TRANSCODE_ABITRATE)
    p_fin.add_argument("--force", action="store_true")
    p_fin.set_defaults(func=cmd_finish)

    # status
    p_st = sp.add_parser("status", help="检查工作目录各产物是否就位")
    p_st.add_argument("--srt")
    p_st.add_argument("--episode")
    p_st.add_argument("--work-dir")
    p_st.add_argument("--output-dir")
    p_st.set_defaults(func=cmd_status)

    args = p.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
