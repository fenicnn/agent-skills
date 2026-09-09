import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path


SCRIPT = Path(__file__).with_name("moyu_pipeline.py")
SPEC = importlib.util.spec_from_file_location("moyu_pipeline", SCRIPT)
PIPELINE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(PIPELINE)


SAMPLE = """1
00:00:01,000 --> 00:00:04,000
This is <font color="yellow">figure out</font>, <span style="font-weight:bold; color: rgb(99, 102, 241)">worth it</span> and <font color="#fff">RUN</font>.
这是<font color="#ff0">弄清楚</font>，英文重复写作 <font color="#0ff">run</font>。
"""


class PipelineTests(unittest.TestCase):
    def test_highlight_extraction_matches_studio_rules(self):
        cues = PIPELINE.parse_highlighted_srt(SAMPLE)
        self.assertEqual(
            cues[0]["terms"],
            [
                {"term": "figure out", "color": "yellow"},
                {"term": "worth it", "color": "rgb(99, 102, 241)"},
                {"term": "RUN", "color": "#fff"},
            ],
        )

    def test_builds_released_client_v1_ids_and_optional_v2(self):
        cues = PIPELINE.parse_highlighted_srt(SAMPLE)
        v1, _ = PIPELINE.build_vocabulary_payload(cues, 1)
        v2, _ = PIPELINE.build_vocabulary_payload(cues, 2)
        self.assertEqual(v1["vocabulary"][0]["id"], "cue-1-000001000-term-0")
        self.assertNotIn("cueIndex", v1["vocabulary"][0])
        self.assertEqual(v2["vocabulary"][0]["cueIndex"], 1)
        self.assertNotIn("id", v2["vocabulary"][0])

    def test_vocab_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.srt"
            output = root / "result.json"
            source.write_text(SAMPLE, encoding="utf-8")
            result = PIPELINE.cmd_vocab(Namespace(
                output=str(output), hl_srt=str(source), prompt_md=None,
                schema=1, prompt_output=None, no_prompt=False,
                force=False, dry_run=True,
            ))
            self.assertEqual(result, 0)
            self.assertFalse(output.exists())

    def test_cli_defaults_to_schema_version_2(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.srt"
            output = root / "result.json"
            source.write_text(SAMPLE, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "vocab", "--hl-srt", str(source), "--output", str(output)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(output.read_text())["schemaVersion"], 2)

    def test_apply_selection_writes_expected_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / "work"
            work.mkdir()
            (work / "srt.original.txt").write_text(SAMPLE, encoding="utf-8")
            source = root / "selection.json"
            source.write_text(json.dumps({"words": {"1": ["worth it"]}, "corrections": {}}), encoding="utf-8")
            result = PIPELINE.cmd_apply_selection(Namespace(
                input=str(source), work_dir=str(work), episode="e01",
                force=False, dry_run=False,
            ))
            self.assertEqual(result, 0)
            self.assertEqual(json.loads((root / "work/e01_words.json").read_text()), {"1": ["worth it"]})

    def test_empty_finish_fails(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "finish"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--srt 必填", result.stderr)

    def test_mp4_is_reused_unless_transcode_is_explicit(self):
        self.assertFalse(PIPELINE.should_transcode_video("episode.mp4"))
        self.assertFalse(PIPELINE.should_transcode_video("EPISODE.MP4"))
        self.assertTrue(PIPELINE.should_transcode_video("episode.mkv"))
        self.assertTrue(PIPELINE.should_transcode_video("episode.mp4", transcode_mp4=True))

    def test_end_to_end_defaults_to_v2(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Show.S02E04.srt"
            source.write_text("""1
00:00:01,000 --> 00:00:04,000
It is worth it.
这是值得的。
""", encoding="utf-8")
            init = subprocess.run(
                [sys.executable, str(SCRIPT), "init", "--srt", str(source), "--episode", "e04"],
                capture_output=True, text=True,
            )
            self.assertEqual(init.returncode, 0, init.stderr)
            work = root / ".moyu-work/e04"
            selection = root / "selection.json"
            selection.write_text(json.dumps({"words": {"1": ["worth it"]}, "corrections": {}}), encoding="utf-8")
            apply = subprocess.run(
                [sys.executable, str(SCRIPT), "apply-selection", "--input", str(selection),
                 "--episode", "e04", "--work-dir", str(work)],
                capture_output=True, text=True,
            )
            self.assertEqual(apply.returncode, 0, apply.stderr)
            finish = subprocess.run(
                [sys.executable, str(SCRIPT), "finish", "--srt", str(source), "--episode", "e04"],
                capture_output=True, text=True,
            )
            self.assertEqual(finish.returncode, 0, finish.stderr)
            payload = json.loads((root / "moyu-ai-vocabulary.json").read_text())
            self.assertEqual(payload["schemaVersion"], 2)
            self.assertEqual(payload["vocabulary"][0]["cueIndex"], 1)
            self.assertTrue((root / "moyu-ai-vocabulary-prompt.md").exists())
            self.assertTrue((root / "Show.S02E04_高亮.srt").exists())


if __name__ == "__main__":
    unittest.main()
