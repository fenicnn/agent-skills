# Video transcoding

The pipeline probes the first video stream before choosing a mode.

`finish --video` reuses an existing `.mp4` unchanged by default. It does not
probe, copy, or re-encode that file. Pass `--transcode-mp4` only when the user
explicitly asks to normalize an existing MP4.

- `--video-mode auto`: copy H.264 as `avc1`, copy HEVC as `hvc1`, encode other codecs to HEVC.
- `--video-mode copy`: copy the stream; use only when the codec is MP4-compatible.
- `--video-mode hevc`: encode with `--video-encoder` (`hevc_videotoolbox` on macOS by default, otherwise `libx265`).
- `--audio-map 0:a:0?`: select another audio stream when the first stream is not the intended language.

Audio defaults to AAC 192k and MP4 uses `+faststart`. Existing output is preserved unless `--force` is explicit. FFmpeg writes a `.moyu-part-*` file first, so interruption does not replace a valid output.

Verify with:

```bash
ffprobe -v error -show_entries stream=index,codec_type,codec_name,codec_tag_string -show_entries format=duration -of json output.mp4
```
