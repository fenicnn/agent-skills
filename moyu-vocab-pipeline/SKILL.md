---
name: moyu-vocab-pipeline
description: 一站式生成 Moyu Studio 学习资源的 pipeline，串联 3 类产物：高亮 SRT（生词彩色 `<font>` 标签）、`moyu-ai-vocabulary.json`（Moyu Studio 词汇导入，schemaVersion 2：直接从高亮 SRT 的彩色标签提取 cueIndex + term，AI 不生成任何 ID）、MP4（Apple QuickTime 兼容的 HEVC/AAC/faststart）。当用户给出原始双语 SRT + 可选 MKV，要求"高亮字幕 + 生成词汇 + 转码 MP4 一键搞定 / 跑一遍 E04 / 同上一集流程再来一次 / moyu pipeline"时使用。本 skill 负责工程脚手架和机械自动化，"标词"与"回填释义"两步需要外部 AI 介入（规则内置于本文档，无需中间 prompt md）。
agent_created: true
---

# Moyu Vocab Pipeline

## Overview

把"原始视频 + 原始双语 SRT → Moyu Studio 产物"的流程从一次性脚本升级成可复用 pipeline。

- **3 类产物**：高亮 SRT、词汇 JSON（v2 cueIndex 格式）、MP4
- **6 个子命令**：`init / highlight / vocab / transcode / finish / status`
- **不再生成中间 prompt md**：`vocab` 直接解析高亮 SRT 的彩色标签提取 `cueIndex + term`（旧版 `cue-XX-XXX-term-N` ID 与 `moyu-ai-vocabulary-prompt-*.md` 中间产物均已废弃；`--prompt-md` 仅作存量文件 legacy 兼容）
- **依赖**：内置 `srt-vocab-highlight` skill（高亮阶段）+ 系统 `ffmpeg`（transcode 阶段）

Skill 的入口脚本 `scripts/moyu_pipeline.py` 同时也是 CLI，可直接 `python moyu_pipeline.py finish --help` 跑。

## Workflow Decision Tree

1. 用户给原始双语 SRT（必填）和 MKV（可选）→ 进入 Step 2
2. 用户说"跑一遍 E04 / 同上一集再来一次 / pipeline 一键" → 直接用 `finish`（前提是工程目录已有 words/corrections）
3. 用户是全新一集没建工程 → 先 `init`，再 AI 标词，最后 `finish` + AI 回填释义
4. 用户只要其中一类产物 → 用对应单步子命令（`highlight` / `vocab` / `transcode`）
5. 用户想看进度 → `status`

## 典型工作流（4 步）

### Step 1 — 建工程骨架：`init`

```bash
python scripts/moyu_pipeline.py init \
    --srt /path/to/Friends.S01E04...srt \
    --episode e04 \
    --mkv /path/to/Friends.S01E04...mkv  # 可选
```

落地：
- `<work_dir>/srt.original.txt`（规范化换行的 SRT 备份，**AI 标词直接读它**）
- `<work_dir>/e04_words.json`、`e04_corrections.json`、`e04_glosses.json`（空模板）

默认**不生成** prompt md；如需把标词规则+字幕打包喂给别的 AI，可加 `--gen-prompt`。

### Step 2 — AI 标词（外部介入）

AI 按下方"AI 标词规则"阅读 `<work_dir>/srt.original.txt`，输出：
- `<work_dir>/e04_words.json`：`{"字幕序号": ["term1", "term2"]}`（term 必须是该 cue 英文行的原样子串）
- `<work_dir>/e04_corrections.json`：`{"字幕序号": "修正后的整句中文"}`

注：skill **不**内置 AI 标词实现——这是 workflow 唯一的"非自动化"环节之一。

### Step 3 — 一键产出 `finish`（vocab 自动从高亮 SRT 解析）

```bash
python scripts/moyu_pipeline.py finish \
    --srt /path/to/Friends.S01E04...srt \
    --episode e04 \
    --output-dir /path/to/第4集 \        # HL SRT + vocabulary.json 输出目录
    --mkv /path/to/Friends.S01E04...mkv   # 可选
```

跑完产出：
- `<output_dir>/<srt_stem>_高亮.srt`
- `<output_dir>/moyu-ai-vocabulary.json`（**schemaVersion 2 骨架**：cueIndex/term 已从高亮 SRT 彩色标签解析，phonetic/partOfSpeech/meaning 为空待 AI 回填；默认放 HL SRT 同目录，`--vocab-output` 可覆盖）
- `<mp4>`（H.265 hvc1 + AAC 192k + faststart）

不需要 `--prompt-md`——vocab 阶段自动用刚生成的高亮 SRT。

### Step 4 — AI 回填释义（外部介入）

AI 按下方"词汇 JSON 映射规则"给 `moyu-ai-vocabulary.json` 的 92/N 条骨架填
`phonetic / partOfSpeech / meaning`（cueIndex/term 保持原样不动），输出仍是合法 UTF-8 JSON。

## 子命令速查

| 命令 | 用途 | 关键参数 |
|---|---|---|
| `init` | 建工程骨架（默认不生成 prompt md） | `--srt` (必)、`--mkv` (可)、`--episode`、`--work-dir`、`--gen-prompt` (可) |
| `highlight` | 单跑：生成高亮 SRT | `--srt`、`--episode`、`--colors` |
| `vocab` | 单跑：**高亮 SRT → vocabulary.json** | `--hl-srt` (推荐)、`--output`；legacy：`--prompt-md` |
| `transcode` | 单跑：ffmpeg MKV → MP4 | `--input`、`--output`、`--vcodec-copy` |
| `finish` | 一键：`highlight + vocab + transcode` | `--srt`、`--output-dir`、`--vocab-output`、`--mkv` |
| `status` | 检查工程目录各产物是否就位 | `--srt`、`--episode`、`--work-dir` |

## 关键约定（防踩坑）

### 词汇 JSON 映射规则（schemaVersion 2 / cueIndex）——权威规则

新版 Moyu Studio **不要求 AI 生成词汇 `id`**，AI 只需正确返回 `cueIndex + term`：

1. **只提取带颜色标签的英文词汇/短语**：`<font color="...">...</font>` 或 `<span style="color: ...">...</span>`；没有颜色标签的单词不得加入结果。
2. **cueIndex = 该词汇所在 SRT 字幕块第一行的字幕序号**，必须是数字不能是字符串（`"cueIndex": 62` 正确，`"cueIndex": "62"` 错误）。
3. **term = 彩色标签中的英文原文，原样保留**：不改原形、不改单复数、不改时态、不改写短语、不增删内部单词、不改标点或连字符。
4. **不需要时间戳、不需要 `term-0`、不拼接任何 ID**（旧格式 `cue-62-000352857-term-0` 禁止）。
5. **同一条字幕内重复出现的相同彩色词汇只保留第一次**（如 E03 cue 44 的 "cell" 出现两次只算一条）。
6. 五个字段齐全：`cueIndex / term / phonetic / partOfSpeech / meaning`；输出 `{"schemaVersion": 2, "vocabulary": [...]}`。
7. **phonetic**：常见 IPA 音标；短语不适合标注时用空字符串 `""`。
8. **partOfSpeech**：`n. / v. / adj. / adv.` 或 `phrase`。
9. **meaning**：根据完整中英文字幕语境填写简洁自然的中文释义。
10. 只能输出合法 UTF-8 JSON，不要 Markdown 代码块、说明或其他文字。
11. **输入若是无彩色标签的普通 SRT：不产生任何重点词汇，AI 也不应自行选择词汇**（`vocab` 子命令会直接报错退出）。

**一句话**：`cueIndex` 直接抄 SRT 字幕块第一行的数字，`term` 直接抄彩色标签中的英文，不生成或推算任何 ID。

### legacy：存量 prompt md 兼容（仅维护，不再新生成）

- `vocab --prompt-md` 仍可解析存量文件：v1（`cue-XX-XXX-term-N` → schemaVersion 1，含 id/color/block）/ v2（`cueIndex` → schemaVersion 2）
- 解析前先按 `## 待处理词汇` 切段，避免误读规则模板占位示例（E02 老坑）
- 新流程一律走 `--hl-srt`，两入口同时给出时 `--hl-srt` 优先

### AI 标词规则（Step 2 用，AI 直接读 srt.original.txt）

- **难度过滤**：只挑 CET-4 / CET-6 词汇 + 实用口语/俚语/固定搭配（如 fancy、reckon、gut feeling）；跳过基础词（the/a/is/school）、专有名词、人名。
- **短语优先于单词**：同一句同时含短语与单词（如 fair enough / enough）时只标短语，避免嵌套。
- **完整匹配**：以字幕文本原始大小写/连字符为准（"stand-up comedian" 不拆分；"after all" 不拆成 after/all）；term 必须是英文行的**原样子串**（含重音字符如 déjà vu）。
- **保持顺序**：生词在 cue 内按首次出现顺序写入 words.json 数组。
- **颜色不用 AI 管**：`srt_highlight_full.py` 按 cue 内 term 序号自动分配 5 色。
- **修正中文翻译**：错译写入 corrections.json（整句替换）。

### 配色：5 色霓虹系（highlight 自动分配，term-0..4）
- term-0 → `#ffd400`（黄）｜ term-1 → `#ff9a00`（橙）｜ term-2 → `#00ff7f`（亮绿）
- term-3 → `#ff66ff`（亮粉）｜ term-4 → `#00e5ff`（亮青）；6 个以上循环回黄
- 霓虹饱和度对暗背景 / 明亮户外背景都友好；不用红/蓝/绿/紫深色（会被背景吞没）
- `--colors` 可覆盖默认串 `#ffd400,#ff9a00,#00ff7f,#ff66ff,#00e5ff`

### Glosses 不传
- `glosses.json` 必须是空 `{}`，**禁止传值为空字符串**的释义
- `srt_vocab_highlight.py` 内部 `re.compile("")` 会匹配每个位置，导致中文行每个字被包成 `<font></font>字</font>`
- 如需中文行释义高亮，必须用中文行实际包含的子串，绝不能用空串

### `--vcodec-copy`：默认开启
- 视频直接 copy 原码流（HEVC BluRay 已是 hvc1）；音频 AC3 → AAC LC 192k

### `--movflags +faststart`
- 必须加，否则 QuickTime/Web 浏览器没法 seek

## 验证清单（每个 step 后必查）

**Step 1 init 后**：
- [ ] `<work_dir>/srt.original.txt` 与原 SRT 内容一致（仅换行规范化）

**Step 3 highlight 后**：
- [ ] HL SRT 块数 = 原 SRT 块数（结构零改动）
- [ ] `<font>` 计数 == `</font>` 计数（标签配对）
- [ ] 英文去标签后与原 SRT 逐字一致（`re.sub(r'</?font[^>]*>', '', hl) == orig`）
- [ ] 所有 cue 的 start/end 时间戳未变

**Step 3 vocab 后**（新链路重点）：
- [ ] vocabulary.json 条数 = HL SRT 去重后的彩色 term 数（同 cue 同 term 只算一次）
- [ ] 每条 `cueIndex` 是数字且等于原 SRT 字幕序号
- [ ] 每条 `term` 原样来自彩色标签（不改写、不推算 ID、无 `cue-XX-XXX-term-N` 残留）
- [ ] `schemaVersion` 为数字 2，`vocabulary` 为数组，五字段齐全
- [ ] 逐项核对：HL SRT 每个 cue 的彩色标签序列 ↔ vocabulary.json 对应 cueIndex 的 term 序列

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
- 工程目录惯例：`<视频目录>/.moyu-work/`

## Resources

### scripts/
- `scripts/moyu_pipeline.py`：本 skill 的全部实现——6 个子命令 + SRT 解析 + 高亮 SRT 彩色标签解析（`extract_terms_from_hl_srt`：cueIndex/term 提取与去重）+ 调用 srt-vocab-highlight + 调用 ffmpeg。可作为 CLI 直接 `python moyu_pipeline.py <cmd> [opts]` 跑，也可被 WorkBuddy 加载走子命令 API。
