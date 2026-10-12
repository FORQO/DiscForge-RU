"""Regressions for experimental, capacity-aware multi-track AC-3 CLI authoring."""
import contextlib
import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from cli import _audio_indices, main as cli_main
from core import DiscForgeError, bitrate_plan, create_bluray, parse_probe, write_meta


def media(path, *, audio=True):
    streams = [{"codec_type": "video", "codec_name": "h264", "width": 1920,
                "height": 1080, "avg_frame_rate": "24000/1001"}]
    if audio:
        streams += [
            {"codec_type": "audio", "codec_name": "aac", "channels": 2,
             "tags": {"language": "eng"}},
            {"codec_type": "subtitle", "codec_name": "subrip"},
            {"codec_type": "audio", "codec_name": "eac3", "channels": 6,
             "tags": {"language": "rus"}},
            {"codec_type": "audio", "codec_name": "aac", "channels": 1,
             "tags": {"language": "eng\nV_FAKE"}},
        ]
    return parse_probe(str(path), {"format": {"duration": "90", "size": "1"},
                                   "streams": streams})


class MultiAudioCLI(unittest.TestCase):
    def test_audio_indices_parser(self):
        self.assertEqual(_audio_indices("0, 2,1"), (0, 2, 1))
        from argparse import ArgumentTypeError
        for invalid in ("", ",", "1,", "1,,2", "abc", "-1", "1;2"):
            with self.subTest(invalid=invalid), self.assertRaises(ArgumentTypeError):
                _audio_indices(invalid)

    def test_cli_forwards_multiple_indices(self):
        info = media("demo.mkv")
        with (patch("cli.probe_media", return_value=info),
              patch("cli.create_bluray", return_value=Path("done")) as create,
              contextlib.redirect_stdout(io.StringIO()),
              contextlib.redirect_stderr(io.StringIO())):
            status = cli_main(["build", "demo.mkv", "--audio-indices", "2,0", "--folder"])
        self.assertEqual(status, 0)
        self.assertEqual(create.call_args.kwargs["audio_indices"], (2, 0))

    def test_audio_options_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                cli_main(["build", "demo.mkv", "--audio-index", "0",
                          "--audio-indices", "1,0"])

    def test_multi_audio_budget_reserves_every_stream(self):
        one = bitrate_plan(5400, "BD25", True)
        three = bitrate_plan(5400, "BD25", 3)
        self.assertEqual(one - three, 2 * 640_000)
        for invalid in (-1, 9, 1.2, "2"):
            with self.subTest(invalid=invalid), self.assertRaises(DiscForgeError):
                bitrate_plan(5400, "BD25", invalid)

    def test_meta_lists_independent_ac3_streams_in_requested_order(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            meta = p / "film.meta"
            write_meta(meta, p / "video.h264", [p / "one.ac3", p / "two.ac3"],
                       [0.0, 10.0], ["rus", "eng\nA_AC3"])
            lines = meta.read_text(encoding="utf-8").splitlines()
            audio_lines = [line for line in lines if line.startswith("A_AC3")]
            self.assertEqual(len(audio_lines), 2)
            self.assertIn("one.ac3", audio_lines[0])
            self.assertTrue(audio_lines[0].endswith("lang=rus"))
            self.assertIn("two.ac3", audio_lines[1])
            self.assertTrue(audio_lines[1].endswith("lang=und"))
            with self.assertRaises(DiscForgeError):
                write_meta(meta, p / "video.h264", [p / "one.ac3"], audio_languages=[])

    def test_reject_invalid_multiselect_before_io(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "film.mkv"
            source.touch()
            info = media(source)
            out = Path(temp) / "result"
            for indices in ((), (1, 1), (-1, 1), (3,), (True,), ("1",), (0, 1, 2) * 3):
                with self.subTest(indices=indices), patch("core._require_binary") as req:
                    with self.assertRaises(DiscForgeError):
                        create_bluray(info, "BD25", str(out), "ffmpeg", "tsmuxer",
                                      False, False, threading.Event(), lambda _: None,
                                      lambda _: None, audio_indices=indices)
                    req.assert_not_called()
                    self.assertFalse(out.exists())

    def test_silent_video_and_explicit_multi_is_error(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "silent.mkv"
            source.touch()
            info = media(source, audio=False)
            with self.assertRaises(DiscForgeError):
                create_bluray(info, "BD25", str(Path(temp) / "out"),
                              "ffmpeg", "tsmuxer", False, False, threading.Event(),
                              lambda _: None, lambda _: None, audio_indices=(0,))

    def test_pipeline_encodes_selected_streams_and_hands_them_to_muxer(self):
        class StopBeforePhysicalAuthoring(Exception):
            pass

        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "film.mkv"
            source.touch()
            info = media(source)
            commands = []
            progress_stages = []
            captured_meta = []

            def encode(command, stage, start, width, *_):
                commands.append(command)
                progress_stages.append((start, width))
                Path(command[-1]).write_bytes(b"synthetic-encoded-content")

            def mux(binary, meta, destination, cancel, log):
                captured_meta.append(meta.read_text(encoding="utf-8"))
                raise StopBeforePhysicalAuthoring()

            out = Path(temp) / "out"
            with patch("core._require_binary"), patch("core.run_command", side_effect=encode), \
                 patch("core.run_tsmuxer", side_effect=mux):
                with self.assertRaises(StopBeforePhysicalAuthoring):
                    create_bluray(info, "BD25", str(out), "ffmpeg", "tsmuxer",
                                  False, False, threading.Event(), lambda _: None,
                                  lambda _: None, audio_indices=(1, 0))
            self.assertEqual(len(commands), 3)
            self.assertEqual([commands[i][commands[i].index("-map") + 1]
                              for i in (1, 2)], ["0:a:1", "0:a:0"])
            self.assertEqual([commands[i][commands[i].index("-ac") + 1]
                              for i in (1, 2)], ["6", "2"])
            self.assertEqual(progress_stages, [(0, 84), (84, 4), (88, 4)])
            audio_lines = [line for line in captured_meta[0].splitlines()
                           if line.startswith("A_AC3")]
            self.assertEqual(len(audio_lines), 2)
            self.assertIn("audio_00.ac3", audio_lines[0])
            self.assertIn("lang=rus", audio_lines[0])
            self.assertIn("audio_01.ac3", audio_lines[1])
            self.assertIn("lang=eng", audio_lines[1])
            self.assertEqual(list(out.glob("DiscForge_work_*")), [])


if __name__ == "__main__":
    unittest.main()
