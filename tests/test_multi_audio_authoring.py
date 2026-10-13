"""CLI-to-BDMV regression coverage for up to eight AC-3 output streams.

No physical drive, Blu-ray player, tsMuxer or FFmpeg is required.
"""
import contextlib
import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from cli import main as cli_main
from core import DiscForgeError, bitrate_plan, create_bluray, parse_probe, write_meta
from tools.audio_tracks import normalize_bluray_language


def make_info(path, *, audio=True):
    streams = [{"codec_type": "video", "codec_name": "h264",
                "width": 1920, "height": 1080}]
    if audio:
        streams += [
            {"codec_type": "audio", "codec_name": "aac", "channels": 2,
             "tags": {"language": "eng"}},
            {"codec_type": "subtitle", "codec_name": "subrip"},
            {"codec_type": "audio", "codec_name": "aac", "channels": 6,
             "tags": {"language": "rus"}},
            {"codec_type": "audio", "codec_name": "flac", "channels": 2,
             "tags": {"language": "bad\nA_AC3, injected"}},
        ]
    return parse_probe(str(path), {"format": {"duration": "120", "size": "100"},
                                   "streams": streams})


class MultipleAc3Tests(unittest.TestCase):
    def test_budget_accounts_for_every_output_stream(self):
        self.assertEqual(bitrate_plan(7500, "BD25", True),
                         bitrate_plan(7500, "BD25", True, audio_count=1))
        self.assertEqual(bitrate_plan(7500, "BD25", True, audio_count=1) -
                         bitrate_plan(7500, "BD25", True, audio_count=3), 1_280_000)
        for invalid in (True, -1, 9, 1.5, "2"):
            with self.subTest(invalid=invalid), self.assertRaises(DiscForgeError):
                bitrate_plan(120, "BD25", True, audio_count=invalid)

    def test_metadata_order_and_safe_languages(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "test.meta"
            write_meta(p, Path(d)/"video.h264", Path(d)/"first.ac3", [0.0, 20],
                       additional_audio=[(Path(d)/"second.ac3", "rus"),
                                         (Path(d)/"third.ac3", "foo\nINJECTED")],
                       audio_language="en")
            data = p.read_text(encoding="utf-8")
            self.assertEqual(data.count("A_AC3,"), 3)
            self.assertLess(data.index("first.ac3"), data.index("second.ac3"))
            self.assertLess(data.index("second.ac3"), data.index("third.ac3"))
            self.assertIn("lang=eng", data)
            self.assertIn("lang=rus", data)
            self.assertIn("lang=und", data)
            self.assertNotIn("INJECTED", data)

    def test_orphaned_extra_audio_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "meta"
            with self.assertRaises(DiscForgeError):
                write_meta(p, Path(d)/"video.h264", None,
                           additional_audio=[(Path(d)/"orphan.ac3", "eng")])
            self.assertFalse(p.exists())

    def test_language_normalization(self):
        self.assertEqual(normalize_bluray_language("RU"), "rus")
        self.assertEqual(normalize_bluray_language("eng"), "eng")
        for input_value in (None, "", "a\nA_AC3", "english", "日本語", 99):
            with self.subTest(value=input_value):
                self.assertEqual(normalize_bluray_language(input_value), "und")

    def test_invalid_multi_selection_has_no_side_effects(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "film.mkv"
            src.touch()
            info = make_info(src)
            out = Path(d) / "must-not-exist"
            cases = ([], [0, 0], [0, -1], [0, 3], [0, True],
                     ["1"], list(range(9)), "0,1")
            for selection in cases:
                with self.subTest(selection=selection), patch("core._require_binary") as require:
                    with self.assertRaises(DiscForgeError):
                        create_bluray(info, "BD25", str(out), "ffmpeg", "tsmuxer",
                                      False, False, threading.Event(), lambda _: None,
                                      lambda _: None, audio_indices=selection)
                    require.assert_not_called()
                    self.assertFalse(out.exists())

    def test_mixed_single_and_multiple_selection_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "film.mkv"
            src.touch()
            info = make_info(src)
            with patch("core._require_binary") as require:
                with self.assertRaises(DiscForgeError):
                    create_bluray(info, "BD25", str(Path(d)/"out"), "ffmpeg", "tsmuxer",
                                  False, False, threading.Event(), lambda _: None,
                                  lambda _: None, audio_index=1, audio_indices=[0, 2])
                require.assert_not_called()

    def test_multi_audio_requires_probe_track_details(self):
        from dataclasses import replace
        with tempfile.TemporaryDirectory() as d:
            src = Path(d)/"film.mkv"
            src.touch()
            info = replace(make_info(src), audio_details=())
            with patch("core._require_binary") as require:
                with self.assertRaisesRegex(DiscForgeError, "повторите анализ"):
                    create_bluray(info, "BD25", str(Path(d)/"out"), "ffmpeg", "tsmuxer",
                                  False, False, threading.Event(), lambda _: None,
                                  lambda _: None, audio_indices=[0, 1])
                require.assert_not_called()

    def test_multi_pipeline_encodes_and_multiplexes_in_selection_order(self):
        class StopAtMux(Exception):
            pass

        with tempfile.TemporaryDirectory() as d:
            src = Path(d)/"film.mkv"
            src.write_bytes(b"test")
            info = make_info(src)
            commands = []
            meta_text = []

            def encode(command, *args):
                commands.append(command)
                Path(command[-1]).write_bytes(b"mocked stream")

            def mux(binary, meta, target, cancel, log):
                meta_text.append(meta.read_text(encoding="utf-8"))
                raise StopAtMux()

            with patch("core._require_binary"), patch("core.run_command", side_effect=encode), \
                 patch("core.run_tsmuxer", side_effect=mux):
                with self.assertRaises(StopAtMux):
                    create_bluray(info, "BD25", str(Path(d)/"out"), "ffmpeg", "tsmuxer",
                                  False, False, threading.Event(), lambda _: None,
                                  lambda _: None, chapters=[0.0], audio_indices=[1, 0, 2])
            self.assertEqual(len(commands), 4)
            self.assertEqual([cmd[cmd.index("-map") + 1] for cmd in commands[1:]],
                             ["0:a:1", "0:a:0", "0:a:2"])
            self.assertEqual([cmd[cmd.index("-ac") + 1] for cmd in commands[1:]],
                             ["6", "2", "2"])
            self.assertEqual(meta_text[0].count("A_AC3,"), 3)
            self.assertLess(meta_text[0].index("audio_00.ac3"),
                            meta_text[0].index("audio_01.ac3"))
            self.assertIn("lang=rus", meta_text[0])
            self.assertIn("lang=eng", meta_text[0])
            self.assertIn("lang=und", meta_text[0])
            self.assertEqual(list((Path(d)/"out").glob("DiscForge_work_*")), [])

    def test_cli_repeated_selection(self):
        info = make_info("film.mkv")
        with (patch("cli.probe_media", return_value=info),
              patch("cli.create_bluray", return_value=Path("out")) as run,
              contextlib.redirect_stderr(io.StringIO()),
              contextlib.redirect_stdout(io.StringIO())):
            code = cli_main(["build", "film.mkv", "--folder",
                             "--audio-track", "2", "--audio-track", "0"])
        self.assertEqual(code, 0)
        self.assertEqual(run.call_args.kwargs["audio_indices"], [2, 0])
        self.assertEqual(run.call_args.kwargs["audio_index"], 0)

    def test_cli_audio_flags_conflict_before_encoding(self):
        info = make_info("film.mkv")
        with (patch("cli.probe_media", return_value=info),
              patch("cli.create_bluray") as run,
              contextlib.redirect_stderr(io.StringIO()),
              contextlib.redirect_stdout(io.StringIO())):
            code = cli_main(["build", "film.mkv", "--audio-index", "1",
                             "--audio-track", "0"])
        self.assertEqual(code, 1)
        run.assert_not_called()

    def test_cli_repeated_selection_silent_input(self):
        info = make_info("silent.mkv", audio=False)
        with (patch("cli.probe_media", return_value=info),
              patch("cli.create_bluray") as run,
              contextlib.redirect_stderr(io.StringIO()),
              contextlib.redirect_stdout(io.StringIO())):
            code = cli_main(["build", "silent.mkv", "--audio-track", "0"])
        self.assertEqual(code, 1)
        run.assert_not_called()

    def test_legacy_single_audio_still_supported(self):
        info = make_info("film.mkv")
        with (patch("cli.probe_media", return_value=info),
              patch("cli.create_bluray", return_value=Path("out")) as run,
              contextlib.redirect_stderr(io.StringIO()),
              contextlib.redirect_stdout(io.StringIO())):
            code = cli_main(["build", "film.mkv", "--audio-index", "2"])
        self.assertEqual(code, 0)
        self.assertEqual(run.call_args.kwargs["audio_index"], 2)
        self.assertIsNone(run.call_args.kwargs["audio_indices"])


if __name__ == "__main__":
    unittest.main()
