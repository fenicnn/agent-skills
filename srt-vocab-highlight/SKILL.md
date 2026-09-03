---
name: srt-vocab-highlight
description: 对 SRT 双语字幕的英文原句进行 CET-4/6 难度生词、短语、固定搭配的高亮标记，并可同步高亮中文翻译行中对应的释义，保留原字幕结构（序号、时间轴、中英文本）完全不变并过滤基础词汇。当用户要求"给字幕标生词/高亮字幕单词/加工字幕/学习用字幕/中文字幕也标颜色"且输入为 .srt 双语字幕时使用。输出兼容播放器的 <font> 标签版本，默认使用黄/橙/亮绿/亮粉等浅亮色（深色会被视频背景吞没，用户明确反馈过）。
agent_created: true
---

# Srt Vocab Highlight（SRT 字幕生词高亮）

## Overview

将双语 SRT 字幕中的英文原句里 CET-4/6 难度实用生词、短语、固定搭配用颜色标签标记，并（可选）同步标记中文翻译行中对应的释义，同时：
- 保留全部原有时间轴、序号、原句与中文翻译，结构零改动
- 过滤简单基础词汇（the/a/is/school/coffee 等）
- 保留字幕中已有的 `<font color="#FFFF00">` 等原生标签
- **同一行多个生词用不同颜色区分**（黄→橙→亮绿→亮粉循环，按首次出现顺序）
- **纠正错误的中文翻译**（如 eleven-ish 被译成"十一岁左右"、moorish 误译"摩尔式"）

## Workflow Decision Tree

1. 用户提供 .srt 文件路径 → 读取文件
2. 按字幕序号（英文字幕行）维护目标词表 → 见 Step 1
3. （可选）维护中文对应释义表 → 见 Step 1
4. 运行 `scripts/srt_highlight_full.py`（推荐，完整版）或 `scripts/highlight_srt.py`（旧版，仅单色英文标红）生成高亮版 → 见 Step 2
5. 校验输出（行数、时间轴、标签配对、无嵌套、无词边界误标）→ 见 Step 3
6. 用 present_files 展示新文件

## Step 1: 维护目标词表

- 词表按**字幕序号**组织：`{"序号": ["词/短语", ...]}`
- 只标四级-六级难度**实用**词（CET-4/CET-6 大纲词、地道英式口语、常用固定搭配），过滤基础词
- 典型可标对象：fancy、reckon、grab、vague、immersion、turquoise、moorish、loo、jubbly；fair enough、kind of/sort of、when it comes to、pop to/pop out/pop over、check out、immerse yourself in、gut feeling、in context 等
- 典型过滤对象：基础词（school、coffee、summer、lovely）、专有名词（bougainvillea、bunting、banh mi、sproutlanguage.com）
- 同一句中若短语与单词重叠（如 fair enough 与 enough），只标短语，避免嵌套

**中文对应释义表（可选）**：当用户希望中文字幕里的生词翻译也标红时，为每个英文生词建立中文翻译映射 `{"序号": ["中文释义", ...]}`，释义必须是**原字幕中文译文中的实际子串**（逐句核对译文后提取，如 fancy→想、reckon→认为/估计/觉得、moorish→停不下来、pop to→去）。匹配不上的释义自动跳过，不强行标注。中文行替换用**子串匹配（无 \b）+ 长词优先**（"摩尔式"先于"摩尔"、"十一点左右"先于"十一点"）。

**同句多词多色**：默认颜色池 `['#ffd400' 黄, '#ff9a00' 橙, '#66ff66' 亮绿, '#ff8ee0' 亮粉]` 循环（可用 `--colors` 自定义）。**务必用浅亮色：深色（红/蓝/绿/紫暗色调）会被视频背景吞没，用户明确反馈过**。按词条在原行中的**首次出现位置**排序分配（同词条同色，重复出现处同一颜色），替换回调根据匹配词查颜色映射包裹。英文行用 `m.group(0).lower()` 查色（大小写不敏感），中文行直接子串。

**译文纠正表（可选）**：当用户要求纠正错译时，建立 `{序号: 纠正后整行译文}` 映射，处理中文行时先替换译文再做释义标红（纠正句若含英文词如 Pop/moorish 需同步更新释义表）。常见错译排查点：grabbed（拿非喝）、see you in two（两分钟后见）、Woof（汪汪）、eleven-ish（十一点左右）、moorish（好吃到停不下来非"摩尔式"）、rammed（挤满人非"被撞"）、fair enough（有道理/没关系）、Pop.（保留原文）、early night（早睡）、cheers（语境可表谢意）、lovely jubbly（太棒了）。

## Step 2: 运行高亮脚本

```bash
# 完整版（推荐）：英文多色高亮 + 中文释义同步标色 + 译文纠正
python3 scripts/srt_highlight_full.py \
  --input <原字幕.srt> \
  --output <输出.srt> \
  --words <词表.json> \
  [--glosses <释义表.json>] \
  [--corrections <纠正表.json>] \
  [--tag font|span] \
  [--colors "#ffd400,#ff9a00,#66ff66,#ff8ee0"]

# 旧版（仅英文单色红标，无释义/纠正）：
# python3 scripts/highlight_srt.py --input in.srt --output out.srt --words words.json
```

- 词表 `--words`：`{"序号": ["词/短语", ...]}`
- 释义表 `--glosses`：`{"序号": {"英文词": "中文释义"}}`，释义须为该行译文实际子串；颜色自动跟随对应英文生词
- 纠正表 `--corrections`：`{"序号": "纠正后的整行中文译文"}`，先替换译文再做释义标色
- `--tag` 默认 `font`（`<font color="#ffd400">`）：**绝大多数播放器（VLC/PotPlayer/IINA 等）不识别 `<span style="color:...">`，必须用 font 标签**，与原字幕自带 font 标签兼容，内层 color 优先显示
- `--tag span` 仅在用户明确要求 `<span style="color:...">` 时使用
- `--colors` 逗号分隔的颜色循环，默认亮色系黄/橙/亮绿/亮粉；颜色总数不限，单生词句固定用第一个颜色，多生词句按顺序循环
  - 若视频背景很亮（如户外/商场玻璃门/白天天空），用户可能反馈默认浅亮色“不够亮”，可换更饱和的霓虹色：`--colors "#ffff00,#ff6a00,#00ff7f,#ff66ff"`
- 脚本自动处理：中英文行识别（含 CJK 判断）、原 HTML 标签保护（先 split 标签只替换纯文本）、`\b` 词边界、长短语优先交替匹配、多色循环（按首次出现顺序，同词同色）、中文释义长词优先单趟替换（防嵌套，如"好奇心"先于"好奇"、"引发对话"先于"引发"）
- 注意：文件名以 `-` 开头时（如 YouTube ID 命名的 srt），grep/wc 需用 `./` 前缀或 `--` 分隔，否则被当成参数解析失败

## Step 3: 校验输出（必做）

- 行数一致：`wc -l 原文件 输出` 应相同
- 时间轴一致：`diff <(grep '\-\->' 原文件) <(grep '\-\->' 输出)` 应为空
- 标签配对：`grep -o '<font color=' | wc -l`（各颜色）+ 原黄/橙标签数 应等于 `grep -o "</font>" | wc -l`
- 无嵌套：`grep -n "<font[^>]*><font"` 无输出（font 内套 font 属正常，如原黄色+红色标记）
- 无词边界误标：重点检查 ish 是否标入 British、stick 是否标入 chopsticks、grab 是否标入 grabbed（应匹配 grabbed 本身）
- 原 font 标签数量不减少
- 空标签检查：`grep -c '<font color="#[0-9a-f]*"></font>'` 应为 0
- **纠正表逐条生效校验**：运行前先数 `corrections.json` 的条目数；运行后抽查每一条纠正句（尤其含英文注释词的，如 341 shoplifting、329 on sale），确认旧译文已被替换——实践中曾多次漏写纠正条目，仅靠标签配对检查发现不了（高亮/释义照常生效，唯独纠正未落地）。可用脚本输出的"中文处理行"数与预期比对辅助判断
- **macOS/BSD 校验避坑**：macOS 的 `grep` 不支持 `-P`，用 `grep -P` 校验中文/空标签时会静默失败或返回空，一律改用 Python 正则或 `ripgrep`（rg）做校验

## 关键坑点（务必遵守）

1. **词边界必须加 `\b`**：否则 "ish" 会标进 "British"，"stick" 会标进 "chopsticks"
2. **长短语优先**：交替正则按词长降序排列，否则 "eleven-ish" 会被 "ish" 抢先匹配
3. **标签保护**：先 `TAG.split` 把 `<...>` 标签拆出，只对纯文本段替换再拼回，避免改到标签属性
4. **播放器兼容性**：默认输出 font 标签；用户反馈播放器不支持 span 时，直接字符串替换 `<span style="color:...">` → `<font color="...">`、`</span>` → `</font>`
5. **保留原标签**：字幕自带的 `<font color="#FFFF00">` 等标记原样保留，高亮标记嵌套在内（`<font color="#FFFF00"><font color="#ffd400">coconut</font></font>`）
6. **空词表保护**：高亮函数在 `words` 为空时必须直接返回原行，否则 `\b(?:)\b` 或空交替模式会匹配空串并插入空标签，污染全文
7. **文件被外部二次修改时从原始文件重建**：若发现输出文件时间轴偏移、原颜色标记丢失、基础词被误标（如 plate），说明文件被其他工具改过，应基于原始 srt 一次性重建完整高亮版（英文+中文），切勿在损坏文件上继续叠加
8. 中文翻译行与序号、时间轴行一律不动（中文行需含 CJK 判断，中文行内可能夹带英文原文词如 "grab"，同样按释义表处理）

## 资源

### scripts/srt_highlight_full.py
完整版高亮脚本（推荐）：英文生词多色标记 + 中文释义同步标色 + 译文纠正，词表/释义表/纠正表均以独立 JSON 传入。

### scripts/highlight_srt.py
旧版通用高亮脚本（仅英文单色红标），词表以独立 JSON 文件传入。

**无 references/ 与 assets/，已删除（该工作流无需额外文档与模板资源）。**
