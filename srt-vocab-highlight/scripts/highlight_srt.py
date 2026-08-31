#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SRT 双语字幕生词高亮工具。

对 SRT 字幕的英文原句按词表用颜色标签标记生词/短语，
保留序号、时间轴、中文翻译与原 HTML 标签完全不变。

用法:
    python3 highlight_srt.py --input in.srt --output out.srt --words words.json [--tag font|span]

words.json 格式（键为字幕序号，值为该句要标记的词/短语列表）:
    {"7": ["coconut"], "24": ["fancy"], "86": ["fair enough"], ...}

--tag 默认 font（<font color="#ff4444">），兼容 VLC/PotPlayer/IINA 等播放器；
     播放器不识别 <span style="..."> 样式，除非用户明确要求 span 否则一律用 font。
"""
import argparse
import json
import re

CJK = re.compile(r'[\u4e00-\u9fff]')
TAG = re.compile(r'<[^>]+>')

TAGS = {
    'font': ('<font color="#ff4444">', '</font>'),
    'span': ('<span style="color:#ff4444">', '</span>'),
}


def build_pattern(words):
    # 长短语优先，避免 "eleven-ish" 被 "ish" 抢先匹配
    words = sorted(words, key=len, reverse=True)
    inner = '|'.join(re.escape(w) for w in words)
    # \b 词边界防止 "British" 中的 "ish"、 "chopsticks" 中的 "stick" 被误标
    return re.compile(r'\b(?:' + inner + r')\b', re.IGNORECASE)


def highlight_line(line, words, tag):
    if not words:
        return line
    open_t, close_t = tag
    pattern = build_pattern(words)
    parts = TAG.split(line)
    tags = TAG.findall(line)
    out = []
    for i, part in enumerate(parts):
        if part:
            out.append(pattern.sub(lambda m: open_t + m.group(0) + close_t, part))
        else:
            out.append('')
        if i < len(tags):
            out.append(tags[i])
    return ''.join(out)


def process(input_path, output_path, word_map, tag):
    with open(input_path, encoding='utf-8') as f:
        lines = f.read().splitlines()

    out_lines = []
    current_idx = None
    changed = 0
    for line in lines:
        s = line.strip()
        if re.fullmatch(r'\d+', s):
            current_idx = int(s)
            out_lines.append(line)
            continue
        if '-->' in line:
            out_lines.append(line)
            continue
        if CJK.search(line):
            out_lines.append(line)
            continue
        if re.search(r'[A-Za-z]', line):
            words = word_map.get(str(current_idx), [])
            new = highlight_line(line, words, tag)
            if new != line:
                changed += 1
            out_lines.append(new)
            continue
        out_lines.append(line)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(out_lines) + '\n')
    print('总行数: %d, 高亮处理字幕条数: %d' % (len(out_lines), changed))


def main():
    ap = argparse.ArgumentParser(description='SRT 字幕生词高亮')
    ap.add_argument('--input', required=True, help='原 SRT 文件')
    ap.add_argument('--output', required=True, help='输出 SRT 文件')
    ap.add_argument('--words', required=True, help='词表 JSON 文件 {"序号": ["词", ...]}')
    ap.add_argument('--tag', default='font', choices=['font', 'span'],
                    help='标记标签类型，默认 font（播放器兼容）')
    args = ap.parse_args()

    with open(args.words, encoding='utf-8') as f:
        word_map = json.load(f)
    word_map = {str(k): v for k, v in word_map.items()}
    process(args.input, args.output, word_map, TAGS[args.tag])


if __name__ == '__main__':
    main()
