---
name: moyu-vocab-pipeline
description: 一站式批量或单集生成 Moyu Studio 学习资源：从双语 SRT 生成彩色重点词字幕、schemaVersion 2 AI 词汇任务与导入 JSON，并审计、幂等生成规范 MP4。用户要求处理一集或一段集数、复用上一集流程、整理课程文件、生成配套视频字幕词汇或运行 moyu pipeline 时使用；不用于下载在线视频或最终视频画面合成。
metadata:
  short-description: 批量审计并生成 Moyu Studio 视频、字幕与词汇文件
---

# Moyu Vocab Pipeline

## Overview

把"原始视频 + 原始双语 SRT → Moyu Studio 产物"的流程从一次性脚本升级成可复用 pipeline。

- **核心产物**：规范 MP4、高亮 SRT、词汇 JSON（v2 cueIndex 格式）和 AI 释义提示词
- **7 个子命令**：`init / apply-selection / highlight / vocab / transcode / finish / status`
- **批量工具**：`audit_media_batch.py` 先审计、`ensure_canonical_mp4.py` 幂等补齐规范 MP4，完成后再严格复查
- `vocab` 直接解析高亮 SRT，并同时生成可交给外部 AI 的释义任务；默认输出 schemaVersion 2，`--schema 1` 兼容旧客户端。
- **依赖**：内置 `srt-vocab-highlight` skill（高亮阶段）+ 系统 `ffmpeg`（transcode 阶段）

Skill 的入口脚本 `scripts/moyu_pipeline.py` 同时也是 CLI，可直接 `python moyu_pipeline.py finish --help` 跑。

## Workflow Decision Tree

1. 用户指定一段集数或说“处理第 N–M 集” → 视为完整批处理：先批量审计，再只补缺失/无效产物，最后严格复查。除非用户明确缩小范围，否则规范 MP4、原始 SRT、高亮 SRT、词汇 JSON 缺一不可。
2. 用户给原始双语 SRT（必填）和视频（可选）→ 进入单集工作流。
3. 用户说"跑一遍 E04 / 同上一集再来一次 / pipeline 一键" → 直接用 `finish`（前提是工程目录已有 words/corrections）。
4. 用户是全新一集没建工程 → 先 `init --gen-prompt`，再 AI 标词；外部 AI 返回单个 JSON 时用 `apply-selection` 落成 words/corrections，最后 `finish` + AI 回填释义。
5. 用户只要其中一类产物 → 用对应单步子命令（`highlight` / `vocab` / `transcode`）。
6. 用户想看进度 → 单集用 `status`，批量用 `audit_media_batch.py`。

## 批量处理协议（必须执行）

假设根目录下按 `第4集/`、`第5集/` 等命名，每集以唯一的 MKV 或原始 MP4 为基准。开始前先做只读审计：

```bash
python scripts/audit_media_batch.py /path/to/season --episodes 4-10
```

若 MKV 对应的规范 MP4 缺失，幂等补齐：

```bash
python scripts/ensure_canonical_mp4.py /path/to/season --episodes 4-10
```

规则：

- 规范 MP4 只能叫 `<源视频文件名去扩展名>.mp4`；`-moyu-*`、`_000*`、预览文件及其他发布变体都不算规范交付文件。
- 已存在且验证有效的规范 MP4 必须跳过，不能重复编码、覆盖或生成带后缀副本。
- 已存在但验证失败的规范 MP4 必须停止并报告，不能静默覆盖。历史 `.converting.mp4` 仅在验证有效时可原子提升为规范文件。
- 每集必须恰好有一个源视频；缺失或有多个候选时停止该批次，要求人工确认，不能猜。
- 用户没有明确要求时，不生成 Bilibili、压缩、预览或编号副本等额外 MP4。
- 每一集的字幕与词汇仍按下方单集工作流处理；批次末尾必须执行严格完成检查：

```bash
python scripts/audit_media_batch.py /path/to/season --episodes 4-10 --strict
```

只有严格检查退出成功，且每集 `MP4 / SRT / HIGHLIGHT / VOCAB` 均为 `OK`，才可向用户报告整批完成。`EXTRA_MP4` 只报告冗余文件，不自动删除；删除需要用户明确授权。

## 典型工作流（4 步）

### Step 1 — 建工程骨架：`init`

```bash
python scripts/moyu_pipeline.py init \
    --srt /path/to/Friends.S01E04...srt \
    --episode e04 \
    --video /path/to/Friends.S01E04...mp4  # 可选；MP4 不会重复转码
```

落地：
- `<work_dir>/srt.original.txt`（规范化换行的 SRT 备份，**AI 标词直接读它**）
- `<work_dir>/e04_words.json`、`e04_corrections.json`、`e04_glosses.json`（空模板）

默认不生成标词 prompt；需要交给外部 AI 时加 `--gen-prompt`。其输出格式可由 `apply-selection` 直接导入。

### Step 2 — AI 标词（外部介入）

AI 按下方"AI 标词规则"阅读 `<work_dir>/srt.original.txt`，输出：
- `<work_dir>/e04_words.json`：`{"字幕序号": ["term1", "term2"]}`（term 必须是该 cue 英文行的原样子串）
- `<work_dir>/e04_corrections.json`：`{"字幕序号": "修正后的整句中文"}`

注：skill **不**内置 AI 标词实现——这是 workflow 唯一的"非自动化"环节之一。

外部 AI 返回 `{"words": {...}, "corrections": {...}}` 后执行：

```bash
python scripts/moyu_pipeline.py apply-selection \
  --input /path/to/selection.json --episode e04 \
  --work-dir /path/to/.moyu-work/e04
```

### Step 3 — 一键产出 `finish`（vocab 自动从高亮 SRT 解析）

```bash
python scripts/moyu_pipeline.py finish \
    --srt /path/to/Friends.S01E04...srt \
    --episode e04 \
    --video /path/to/Friends.S01E04...mp4  # 可选；已有 MP4 原样复用
```

默认在**原 SRT 所在目录**产出（`--output-dir` 可覆盖）：
- `<srt_stem>_高亮.srt`
- `moyu-ai-vocabulary.json`（默认 **schemaVersion 2 骨架**；`--schema 1` 可兼容旧客户端）
- `moyu-ai-vocabulary-prompt.md`（含完整双语语境，可直接交给外部 AI 回填释义）
- 非 MP4 视频才另产兼容 MP4；输入已是 MP4 时不复制、不转码

`.moyu-work/<episode>` 只保存可复用的中间状态，不作为最终交付目录。

不需要 `--prompt-md`——vocab 阶段自动用刚生成的高亮 SRT。

### Step 4 — AI 回填释义（外部介入）

AI 按下方"词汇 JSON 映射规则"给 `moyu-ai-vocabulary.json` 的 92/N 条骨架填
`phonetic / partOfSpeech / meaning`（cueIndex/term 保持原样不动），输出仍是合法 UTF-8 JSON。

## 子命令速查

| 命令 | 用途 | 关键参数 |
|---|---|---|
| `init` | 建工程骨架（默认不生成 prompt md） | `--srt` (必)、`--video` (可)、`--episode`、`--work-dir`、`--gen-prompt` (可) |
| `apply-selection` | 把 AI 标词结果拆成 words/corrections | `--input`、`--episode`、`--work-dir` |
| `highlight` | 单跑：生成高亮 SRT | `--srt`、`--episode`、`--colors` |
| `vocab` | 单跑：**高亮 SRT → vocabulary.json + AI prompt** | `--hl-srt`、`--output`、`--schema 1|2` |
| `transcode` | 单跑：ffmpeg MKV → MP4 | `--input`、`--output`、`--video-mode auto|copy|hevc` |
| `finish` | 一键：`highlight + vocab`，非 MP4 视频按需 transcode | `--srt`、`--output-dir`、`--vocab-output`、`--video`、`--transcode-mp4` |
| `status` | 检查工程目录各产物是否就位 | `--srt`、`--episode`、`--work-dir` |

## 关键约定（防踩坑）

### 词汇 JSON 映射规则（schemaVersion 2 / cueIndex）——权威规则

默认生成 schemaVersion 2。脚本负责生成 `cueIndex + term`，AI 不得新增、删除或修改二者：

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

### 视频模式：默认 `--video-mode auto`
- `finish --video` 遇到 `.mp4` 时默认直接复用，不创建第二份视频，不检查或改变其编码。
- 只有非 MP4 输入才进入 auto：HEVC 自动 copy 并标记 hvc1；H.264 自动 copy 并标记 avc1；其他编码转 HEVC。音频默认 AAC LC 192k。
- 仅当用户明确要求重编码已有 MP4 时，才传 `--transcode-mp4`。
- 批量场景必须使用 `ensure_canonical_mp4.py`，它复用本 skill 的 `transcode` 实现并在生成前后验证，保证重复运行只跳过有效文件。

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

**批量任务结束前**：
- [ ] 对用户指定的完整集数范围运行了 `audit_media_batch.py --strict`
- [ ] 每集规范 MP4 名称与源文件 stem 完全一致，且视频/音频、分辨率、时长和兼容编码验证通过
- [ ] 每集原始 SRT、高亮 SRT、schemaVersion 2 词汇 JSON 都有效
- [ ] 未把发布变体误报为规范 MP4，也未无要求生成额外 MP4

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

- Python 3.10+：使用当前解释器；高亮脚本优先从相邻 skill 解析，也可用 `MOYU_HIGHLIGHT_SCRIPT` 指定
- ffmpeg：标准 `ffmpeg` 命令，安装可用 `brew install ffmpeg`
- 视频目录惯例：`/Users/cnn/Movies/<剧集>/<季>/<第N集>/`
- 工程目录惯例：`<视频目录>/.moyu-work/`

## Resources

### scripts/
- `scripts/moyu_pipeline.py`：本 skill 的全部实现——7 个子命令 + 与 Studio 对齐的 SRT 高亮解析 + 调用 srt-vocab-highlight + 调用 ffmpeg。
- `scripts/audit_media_batch.py`：只读扫描一段集数，输出逐集完成矩阵；`--strict` 在任何规范交付物缺失或无效时退出失败。
- `scripts/ensure_canonical_mp4.py`：幂等补齐每集唯一的规范 MP4；复用 `moyu_pipeline.py transcode`，不覆盖无效成品、不制造后缀副本。

## 参考协议

- AI JSON 导入或高亮解析异常时，读取 [references/studio-vocabulary-contract.md](references/studio-vocabulary-contract.md)。
- 编码、音轨或播放器兼容异常时，读取 [references/video-transcoding.md](references/video-transcoding.md)。

## 修改后验证

```bash
python -m unittest discover -s scripts -p 'test_*.py'
python scripts/moyu_pipeline.py --help
python scripts/audit_media_batch.py --help
python scripts/ensure_canonical_mp4.py --help
```
