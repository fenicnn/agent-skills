# Moyu Studio vocabulary contract

## Schema version 2 (default)

```json
{
  "schemaVersion": 2,
  "vocabulary": [{
    "cueIndex": 1,
    "term": "worth it",
    "phonetic": "/ˈwɜːθ ɪt/",
    "partOfSpeech": "phrase",
    "meaning": "值得"
  }]
}
```

`cueIndex` must be an integer. Studio matches entries with `cueIndex + normalized(term)`. Use schema 2 unless the installed Studio only accepts schema 1.

## Schema version 1 (legacy)

```json
{
  "schemaVersion": 1,
  "vocabulary": [{
    "id": "cue-1-000001000-term-0",
    "term": "worth it",
    "phonetic": "/ˈwɜːθ ɪt/",
    "partOfSpeech": "phrase",
    "meaning": "值得"
  }]
}
```

The deterministic ID is `cue-{cueIndex}-{HHMMSSmmm}-term-{zeroBasedTermIndex}`.

## Highlight parsing parity

Mirror `pc/macos/src/shared/srt.ts`:

- recognize `<font color=...>` and `<span style="...color: ...">`;
- accept quoted/unquoted attributes and named, hex, or functional CSS colors;
- strip nested tags and decode HTML entities;
- retain a highlighted value only when it contains Latin letters and Latin count is at least CJK count;
- deduplicate per cue using NFKC, lowercase English, straight apostrophes, collapsed whitespace, and trim;
- classify a line containing CJK as Chinese context; otherwise a line containing Latin letters is English context.

## Import diagnostics

- “版本不受支持”: install a v2-capable Studio build or explicitly generate `--schema 1`.
- “ID 或词汇不匹配”: verify the v1 start timestamp and term order.
- “字幕序号或词汇不匹配”: verify numeric cue index and normalized term.
- Partial imports usually mean Pipeline and Studio extracted different highlighted terms; run the fixture tests first.
