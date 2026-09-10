import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import audit_media_batch


class BatchMediaTests(unittest.TestCase):
    def test_episode_numbers_support_ranges_and_lists(self):
        self.assertEqual(audit_media_batch.episode_numbers("4-6,8,10"), [4, 5, 6, 8, 10])

    def test_canonical_status_checks_compatible_codecs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "episode.mkv"
            target = root / "episode.mp4"
            source.touch()
            with target.open("wb") as handle:
                handle.truncate(1_000_001)
            source_info = {
                "streams": [{"codec_type": "video", "codec_name": "hevc", "width": 1920, "height": 1080}],
                "format": {"duration": "1200"},
            }
            target_info = {
                "streams": [
                    {"codec_type": "video", "codec_name": "hevc", "codec_tag_string": "hvc1", "width": 1920, "height": 1080},
                    {"codec_type": "audio", "codec_name": "aac"},
                ],
                "format": {"duration": "1200.2"},
            }
            with patch.object(audit_media_batch, "probe", side_effect=[source_info, target_info]):
                self.assertTrue(audit_media_batch.canonical_status(source, target).startswith("OK("))

            target_info["streams"][0]["codec_tag_string"] = "hev1"
            with patch.object(audit_media_batch, "probe", side_effect=[source_info, target_info]):
                self.assertEqual(audit_media_batch.canonical_status(source, target), "INVALID(video-codec)")

    def test_json_status_requires_schema_two_and_all_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "moyu-ai-vocabulary.json"
            output.write_text(json.dumps({
                "schemaVersion": 2,
                "vocabulary": [{
                    "cueIndex": 7,
                    "term": "fair enough",
                    "phonetic": "",
                    "partOfSpeech": "phrase",
                    "meaning": "有道理",
                }],
            }), encoding="utf-8")
            self.assertEqual(audit_media_batch.json_status(output), "OK(1)")


if __name__ == "__main__":
    unittest.main()
