---
name: moyu-vocab-pipeline
description: 一站式生成 Moyu Studio 学习资源的 pipeline，串联 4 类产物：高亮 SRT（生词彩色 `<font>` 标签）、`moyu-ai-vocabulary.json`（Moyu Studio 词汇导入、id 与 prompt 对齐）、`moyu-ai-vocabulary-prompt-<ts>.md`（AI 标词的中间产物）、MP4（Apple QuickTime 兼容的 HEVC/AAC/faststart）。当用户给出原始双语 SRT + 可选 MKV，要求"高亮字幕 + 生成词汇 + 转码 MP4 一键搞定 / 跑一遍 E03 / 同上一集流程再来一次 / moyu pipeline"时使用。本 skill 负责工程脚手架和机械自动化，"标词"那一步需要外部 AI 介入（skill 内已自动生成 prompt md）。
agent_created: true
---

# Moyu Vocab Pipeline

## Overview

把"原始视频 + 原始双语 SRT → 4 类 Moyu Studio 产物"的流程从一次性脚本升级成可复用 pipeline。

- **4 类产物**：高亮 SRT、词汇 JSON、prompt md、MP4
- **6 个子命令**：`init / highlight / vocab / transcode / finish / status`
- **依赖**：内置 `srt-vocab-highlight` skill（高亮阶段）+ 系统 `ffmpeg`（transcode 阶段）

Skill 的入口脚本 `scripts/moyu_pipeline.py` 同时也是 CLI，可直接 `python moyu_pipeline.py finish --help` 跑。

## Workflow Decision Tree

1. 用户给原始双语 SRT（必填）和 MKV（可选）→ 进入 Step 2
2. 用户说"跑一遍 E03 / 同上一集再来一次 / pipeline 一键" → 直接用 `finish`（前提是工程目录已有 words/corrections）
3. 用户是全新一集没建工程 → 先 `init`，再 AI 标词，最后 `finish`
4. 用户只要其中一类产物 → 用对应单步子命令（`highlight` / `vocab` / `transcode`）
5. 用户想看进度 → `status`

## 典型工作流（3 步）

### Step 1 — 建工程骨架：`init`

```bash
python scripts/moyu_pipeline.py init \
    --srt /path/to/Friends.S01E02.1994.BluRay.1080p.x265.10bit.MNHD-FRDS.srt \
    --episode e02 \
    --mkv /path/to/Friends.S01E02...mkv  # 可选
```

落地：
- `<work_dir>/srt.original.txt`（规范化换行的 SRT 备份）
- `<work_dir>/e02_words.json`、`e02_corrections.json`、`e02_glosses.json`（空模板）
- `<output_dir>/moyu-ai-vocabulary-prompt-YYYY-MM-DD-HH-MM-SS.md`（AI 标词用的 prompt）

工作目录默认 = `<srt_dir>/.moyu-work/`，可用 `--work-dir` 改。

### Step 2 — AI 标词（外部介入）

把 prompt md 喂给 AI，AI 输出：
- `<work_dir>/e02_words.json`：`{"字幕序号": ["term1", "term2"]}`
- `<work_dir>/e02_corrections.json`：`{"字幕序号": "修正后的中文译文"}`

注：skill **不**内置 AI 标词实现——这是 Moyu Studio workflow 唯一的"非自动化"环节。

### Step 3 — 一键产出 `finish`

```bash
python scripts/moyu_pipeline.py finish \
    --srt /path/to/Friends.S01E02...srt \
    --episode e02 \
    --prompt-md moyu-ai-vocabulary-prompt-2026-09-09-XX-XX-XX.md \
    --vocab-output moyu-ai-vocabulary.json \
    --mkv /path/to/Friends.S01E02...mkv \  # 可选
    --mp4-output Friends.S01E02...mp4      # 默认 <mkv>.mp4
```

跑完产出：
- `output/<srt_stem>_高亮.srt`
- `moyu-ai-vocabulary.json`（id 与 prompt 完全对齐）
- `<mp4>`（H.265 hvc1 + AAC 192k + faststart）

## 子命令速查

| 命令 | 用途 | 关键参数 |
|---|---|---|
| `init` | 建工程 + 生 prompt md | `--srt` (必)、`--mkv` (可)、`--episode`、`--work-dir` |
| `highlight` | 单跑：生成高亮 SRT | `--srt`、`--episode`、`--colors` |
| `vocab` | 单跑：从 prompt md → vocabulary.json | `--prompt-md`、`--output` |
| `transcode` | 单跑：ffmpeg MKV → MP4 | `--input`、`--output`、`--vcodec-copy` |
| `finish` | 一键：`highlight + vocab + transcode` | `--srt`、`--prompt-md`、`--vocab-output`、`--mkv` |
| `status` | 检查工程目录各产物是否就位 | `--srt`、`--episode`、`--work-dir` |

## 关键约定（防踩坑）

### 配色：5 色霓虹系
- term-0 → `#ffd400`（浅黄，最显眼，常用于最常见生词）
- term-1 → `#ff9a00`（浅橙）
- term-2 → `#00ff7f`（亮绿）
- term-3 → `#ff66ff`（亮粉/品红）
- term-4 → `#00e5ff`（亮青/cyan）
- 同 cue 出现 6 个及以上 term：循环回 `#ffd400`
- 5 色组合对暗背景 / 明亮户外背景（霓虹色饱和度更高，更耐背景吞没）都友好
- 不用红/蓝/绿/紫深色——会被视频背景吞没（用户明确反馈过）
- 上层调用可通过 `--colors` 覆盖默认值；脚本默认串已更新为 `#ffd400,#ff9a00,#00ff7f,#ff66ff,#00e5ff`

### Glosses 不传
- `glosses.json` 必须是空 `{}`，**禁止传值为空字符串**的释义
- `srt_vocab_highlight.py` 内部 `re.compile("")` 会匹配每个位置，导致 cue 40/214/263/350/351 中文行每个字都被包成 `<font color="#ffd400"></font>字</font>`
- 如果需要中文行释义高亮，必须用中文行实际包含的中文子串，绝不能用空串

### ID 格式：`cue-XX-XXX-term-N`
- `{字幕序号:0>3}-{start_ms:0>9}-term-{术语序号:0}`
- 例：`cue-033-000045889-term-0`
- 同一 cue 多词项用 `-term-0` / `-term-1` 区分
- vocabulary.json 的 id 必须复用 prompt 的 cue id（**禁止**自创 `e02_001..e02_095` 连续编号），否则丢失 cue↔term 的多对一关系

### `--vcodec-copy`：默认开启
- 视频直接 copy 原码流，不需要重新编码（HEVC BluRay 已经是 hvc1）
- 音频 AC3 → AAC LC 192k

### `--movflags +faststart`
- 必须加，否则 QuickTime/Web 浏览器没法 seek

## 验证清单（每个 step 后必查）

**Step 2 init 后**：
- [ ] `<work_dir>/srt.original.txt` 行数与 `<output_dir>/<srt>.srt` 一致
- [ ] prompt md 里时间戳与 SRT 完全对得上

**Step 3 highlight 后**：
- [ ] HL SRT 行数 = 原 SRT 行数（结构零改动）
- [ ] `<font>` 计数 == `</font>` 计数（标签配对）
- [ ] 没有连续 2 个及以上 `<font></font>` 单字符包裹
- [ ] 英文去标签后与原 SRT 逐字一致（`re.sub(r'</?font[^>]*>', '', hl) == orig`）
- [ ] 所有 cue 的 start/end 时间戳未变

**Step 3 vocab 后**：
- [ ] vocabulary.json id 集合 == prompt md id 集合
- [ ] vocabulary.json 总数 = prompt md 总数
- [ ] 颜色统计与 prompt 一致（典型如 90 #ffd400 + 4 #ff9a00 + 1 #00ff7f，按 cue 多 term 分布走 5 色）

**Step 3 transcode 后**：
- [ ] `ffprobe output.mp4` 显示 `hvc1` 视频、`aac` 音频
- [ ] 10s 中间位置能 seek（= faststart 生效）
- [ ] 时长与 MKV 一致

## 子脚本调用方式（如需不通过 moyu_pipeline.py 直接调）

- 高亮：`python ~/.workbuddy/skills/srt-vocab-highlight/scripts/srt_highlight_full.py \
    --input foo.srt --output foo_高亮.srt \
    --words foo_words.json --colors "#ffd400,#ff9a00,#00ff7f,#ff66ff,#00e5ff" \
    [--corrections foo_corrections.json]`
  **不传** `--glosses`
- 转码：`ffmpeg -y -i input.mkv -map 0:v:0 -map 0:a:0? \
    -c:v copy -tag:v hvc1 -c:a aac -b:a 192k \
    -movflags +faststart output.mp4`

## Environment

- Python 3.13+：默认用 `/Users/cnn/.workbuddy/binaries/python/versions/3.13.12/bin/python3`
- ffmpeg：标准 `ffmpeg` 命令，安装可用 `brew install ffmpeg`
- 视频目录惯例：`/Users/cnn/Movies/<剧集>/<季>/<第N集>/`
- 工程目录惯例：`<视频目录>/.moyu-work/<eNN>/`

## Resources

### scripts/
- `scripts/moyu_pipeline.py`：本 skill 的全部实现——6 个子命令 + SRT 解析 + prompt 生成 + 调用 srt-vocab-highlight + 调用 ffmpeg。可作为 CLI 直接 `python moyu_pipeline.py <cmd> [opts]` 跑，也可被 WorkBuddy 加载走子命令 API。
