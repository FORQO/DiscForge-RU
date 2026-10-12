"""Regression: the CLI-selected source stream reaches FFmpeg as 0:a:N."""
import contextlib
import io
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from cli import main as cli_main
from core import DiscForgeError, create_bluray, make_audio_command, parse_probe


def media(path, *, with_audio=True):
    streams = [
        {"codec_type": "video", "codec_name": "h264", "width": 1920,
         "height": 1080, "avg_frame_rate": "24000/1001"},
    ]
    if with_audio:
        streams.extend([
            {"codec_type": "audio", "codec_name": "aac", "channels": 2},
            {"codec_type": "subtitle", "codec_name": "subrip"},
            {"codec_type": "audio", "codec_name": "eac3", "channels": 6,
             "tags": {"language": "rus"}},
        ])
    return parse_probe(str(path), {"format": {"duration": "90", "size": "1"},
                                   "streams": streams})


class AudioSelectionPipelineTests(unittest.TestCase):
    def test_probe_retains_all_audio_only_indices(self):
        info = media("test.mkv")
        self.assertEqual(info.audio_tracks, 2)
        self.assertEqual([t.ffmpeg_map for t in info.audio_details],
                         ["0:a:0", "0:a:1"])
        self.assertEqual(info.audio_details[1].channels, 6)
        self.assertEqual(info.audio_details[1].language, "rus")

    def test_encoder_maps_second_audio_track_and_preserves_channels(self):
        cmd = make_audio_command("ffmpeg", "in.mkv", "out.ac3", 6, 1)
        self.assertEqual(cmd[cmd.index("-map") + 1], "0:a:1")
        self.assertEqual(cmd[cmd.index("-ac") + 1], "6")
        for bad in (-1, True, 1.5, "1"):
            with self.subTest(index=bad), self.assertRaises(DiscForgeError):
                make_audio_command("ffmpeg", "in.mkv", "out.ac3", 2, bad)

    def test_invalid_choice_fails_before_side_effects(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.mkv"
            source.touch()
            info = media(source)
            for index in (-1, 2, True, "1"):
                output = Path(temp) / "missing_out"
                with self.subTest(index=index), patch("core._require_binary") as require:
                    with self.assertRaises(DiscForgeError):
                        create_bluray(info, "BD25", str(output), "ffmpeg", "tsmuxer",
                                      False, False, threading.Event(), lambda _: None,
                                      lambda _: None, audio_index=index)
                    require.assert_not_called()
                    self.assertFalse(output.exists())

    def test_missing_track_metadata_rejects_nonfirst_choice(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.mkv"
            source.touch()
            info = replace(media(source), audio_details=())
            output = Path(temp) / "missing_out"
            with self.assertRaisesRegex(DiscForgeError, "заново проанализируйте"):
                create_bluray(info, "BD25", str(output), "ffmpeg", "tsmuxer",
                              False, False, threading.Event(), lambda _: None,
                              lambda _: None, audio_index=1)
            self.assertFalse(output.exists())

    def test_pipeline_reaches_audio_encoder_with_selected_track(self):
        class StopAfterAudio(Exception):
            pass

        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.mkv"
            source.touch()
            info = media(source)
            observed = []

            def encode(command, *unused):
                observed.append(command)
                if len(observed) == 2:
                    raise StopAfterAudio()

            with patch("core._require_binary"), patch("core.run_command", side_effect=encode):
                with self.assertRaises(StopAfterAudio):
                    create_bluray(info, "BD25", str(Path(temp) / "out"), "ffmpeg",
                                  "tsmuxer", False, False, threading.Event(),
                                  lambda _: None, lambda _: None, audio_index=1)
            self.assertEqual(len(observed), 2)
            audio = observed[1]
            self.assertEqual(audio[audio.index("-map") + 1], "0:a:1")
            self.assertEqual(audio[audio.index("-ac") + 1], "6")
            self.assertEqual(list((Path(temp) / "out").glob("DiscForge_work_*")), [])

    def test_cli_forwards_selected_audio_index(self):
        info = media("test.mkv")
        with patch("cli.probe_media", return_value=info), patch(
                "cli.create_bluray", return_value=Path("done")) as create, (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO())):
            code = cli_main(["build", "test.mkv", "--audio-index", "1", "--folder"])
        self.assertEqual(code, 0)
        self.assertEqual(create.call_args.kwargs["audio_index"], 1)

    def test_cli_explicit_audio_on_silent_video_is_error(self):
        info = media("silent.mkv", with_audio=False)
        with patch("cli.probe_media", return_value=info), patch(
                "cli.create_bluray") as create, (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO())):
            code = cli_main(["build", "silent.mkv", "--audio-index", "0"])
        self.assertEqual(code, 1)
        create.assert_not_called()

    def test_cli_default_preserves_first_audio(self):
        info = media("test.mkv")
        with patch("cli.probe_media", return_value=info), patch(
                "cli.create_bluray", return_value=Path("done")) as create, (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO())):
            code = cli_main(["build", "test.mkv", "--folder"])
        self.assertEqual(code, 0)
        self.assertEqual(create.call_args.kwargs["audio_index"], 0)


if __name__ == "__main__":
    unittest.main()
