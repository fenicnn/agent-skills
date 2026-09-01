#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SRT 双语字幕全面高亮：英文生词多色标记 + 译文纠正 + 中文释义同步标色。

保留序号、时间轴、原 HTML 标签与全部结构。
同一句多个生词按首次出现顺序循环分配颜色（默认黄/橙/亮绿/亮粉，
均为高亮度浅色，避免深色被视频背景吞没），同词条重复出现处使用同一颜色。

用法:
    python3 srt_highlight_full.py --input in.srt --output out.srt \
        --words words.json [--glosses glosses.json] \
        [--corrections corrections.json] [--tag font|span] \
        [--colors "#ffd400,#ff9a00,#66ff66,#ff8ee0"]

words.json:       {"序号": ["word", "phrase", ...]}
glosses.json:     {"序号": {"word": "中文释义"}}   释义须为该行译文的实际子串
corrections.json: {"序号": "纠正后的整行中文译文"}
"""
import argparse
import json
import re

CJK = re.compile(r'[\u4e00-\u9fff]')
TAG = re.compile(r'<[^>]+>')
DEFAULT_COLORS = ['#ffd400', '#ff9a00', '#66ff66', '#ff8ee0']


def tag_pair(color, tag):
    if tag == 'span':
        return '<span style="color:%s">' % color, '</span>'
    return '<font color="%s">' % color, '</font>'


def word_pattern(words):
    # 长短语优先，避免短词抢先匹配；\b 词边界防误标（ish 之于 British）
    ordered = sorted(words, key=len, reverse=True)
    return re.compile(r'\b(?:' + '|'.join(re.escape(w) for w in ordered) + r')\b',
                      re.IGNORECASE)


def gloss_pattern(gloss_color):
    # 中文释义：纯子串匹配（无 \b），长释义优先，单趟替换防嵌套
    items = sorted(gloss_color.items(), key=lambda kv: len(kv[0]), reverse=True)
    return re.compile('|'.join(re.escape(g) for g, _ in items))


def assign_colors(text, pattern, colors):
    color_map = {}
    for m in pattern.finditer(text):
        key = m.group(0).lower()
        if key not in color_map:
            color_map[key] = colors[len(color_map) % len(colors)]
    return color_map


def tag_safe_sub(line, pattern, repl):
    # 先拆开 <...> 标签，只替换纯文本段，避免改到标签属性
    parts = TAG.split(line)
    tags = TAG.findall(line)
    out = []
    for i, part in enumerate(parts):
        out.append(pattern.sub(repl, part) if part else '')
        if i < len(tags):
            out.append(tags[i])
    return ''.join(out)


def is_chinese(l):
    return bool(CJK.search(l))


def is_english(l):
    return bool(re.search(r'[A-Za-z]', l)) and not is_chinese(l)


def parse_blocks(lines):
    blocks = []
    cur = None
    for line in lines:
        s = line.strip()
        if re.fullmatch(r'\d+', s):
            cur = {'idx': int(s), 'lines': [line]}
            blocks.append(cur)
        elif cur is not None:
            cur['lines'].append(line)
        else:
            blocks.append({'idx': None, 'lines': [line]})
    return blocks


def process_block(block, words_map, glosses_map, corrections, tag, colors):
    idx = block['idx']
    key = str(idx) if idx is not None else None
    wl = words_map.get(key, []) if key else []
    pattern = word_pattern(wl) if wl else None
    color_map = {}
    if pattern:
        en_text = ' '.join(TAG.sub('', l) for l in block['lines'] if is_english(l))
        color_map = assign_colors(en_text, pattern, colors)

    gl = glosses_map.get(key, {}) if key else {}
    gloss_color = {}
    for w, g in gl.items():
        c = color_map.get(w.lower())
        if c:
            gloss_color[g] = c
    gp = gloss_pattern(gloss_color) if gloss_color else None

    correction = corrections.get(key) if key else None

    def en_repl(m):
        c = color_map.get(m.group(0).lower())
        if c is None:
            return m.group(0)
        o, cl = tag_pair(c, tag)
        return o + m.group(0) + cl

    def zh_repl(m):
        c = gloss_color.get(m.group(0))
        if c is None:
            return m.group(0)
        o, cl = tag_pair(c, tag)
        return o + m.group(0) + cl

    out_lines = []
    for l in block['lines']:
        s = l.strip()
        if idx is None or '-->' in l or re.fullmatch(r'\d+', s) or not s:
            out_lines.append(l)
            continue
        if is_chinese(l):
            if correction is not None:
                l = correction
            if pattern and color_map:
                l = tag_safe_sub(l, pattern, en_repl)
            if gp:
                l = tag_safe_sub(l, gp, zh_repl)
            out_lines.append(l)
        elif is_english(l):
            if pattern and color_map:
                l = tag_safe_sub(l, pattern, en_repl)
            out_lines.append(l)
        else:
            out_lines.append(l)
    return out_lines


def main():
    ap = argparse.ArgumentParser(description='SRT 双语字幕全面高亮')
    ap.add_argument('--input', required=True)
    ap.add_argument('--output', required=True)
    ap.add_argument('--words', required=True)
    ap.add_argument('--glosses', default=None)
    ap.add_argument('--corrections', default=None)
    ap.add_argument('--tag', default='font', choices=['font', 'span'])
    ap.add_argument('--colors', default=','.join(DEFAULT_COLORS),
                    help='逗号分隔的颜色循环，默认亮色系: ' + ','.join(DEFAULT_COLORS))
    args = ap.parse_args()
    colors = [c.strip() for c in args.colors.split(',') if c.strip()] or DEFAULT_COLORS

    def load(p):
        if not p:
            return {}
        with open(p, encoding='utf-8') as f:
            return {str(k): v for k, v in json.load(f).items()}

    words_map = load(args.words)
    glosses_map = load(args.glosses)
    corrections = load(args.corrections)

    with open(args.input, encoding='utf-8-sig') as f:
        lines = f.read().splitlines()

    out = []
    changed_en = changed_zh = 0
    for block in parse_blocks(lines):
        before = block['lines']
        after = process_block(block, words_map, glosses_map, corrections, args.tag, colors)
        for b, a in zip(before, after):
            if b != a:
                if is_chinese(a) or is_chinese(b):
                    changed_zh += 1
                else:
                    changed_en += 1
        out.extend(after)

    with open(args.output, 'w', encoding='utf-8') as f:
        f.write('\n'.join(out) + '\n')
    print('总行数: %d, 英文高亮行: %d, 中文处理行: %d' % (len(out), changed_en, changed_zh))


if __name__ == '__main__':
    main()
