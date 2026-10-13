"""Side-effect-free audio stream selection tests, independent of Qt/FFmpeg."""
import unittest

from tools.audio_tracks import AudioTrackError, parse_audio_tracks, select_audio_track


class AudioTrackMetadataTests(unittest.TestCase):
    def test_interleaved_audio_uses_audio_only_indices(self):
        tracks = parse_audio_tracks({"streams": [
            {"codec_type": "video"},
            {"codec_type": "audio", "channels": 2, "codec_name": "aac"},
            {"codec_type": "subtitle"},
            {"codec_type": "audio", "channels": 6, "codec_name": "eac3",
             "tags": {"language": "rus"}},
        ]})
        self.assertEqual([t.ffmpeg_map for t in tracks], ["0:a:0", "0:a:1"])
        self.assertEqual(select_audio_track(tracks, 1).channels, 6)
        self.assertEqual(select_audio_track(tracks, 1).language, "rus")

    def test_invalid_indices_are_rejected(self):
        tracks = parse_audio_tracks({"streams": [{"codec_type": "audio", "channels": 2}]})
        for index in (-1, 1, True, 0.0, "0", None):
            with self.subTest(index=index), self.assertRaises(AudioTrackError):
                select_audio_track(tracks, index)

    def test_no_audio_has_no_valid_selection(self):
        tracks = parse_audio_tracks({"streams": [{"codec_type": "video"}]})
        self.assertEqual(tracks, ())
        with self.assertRaises(AudioTrackError):
            select_audio_track(tracks, 0)

    def test_bad_probe_shape_fails_closed(self):
        for data in ({}, {"streams": None}, {"streams": "no"}, None):
            with self.subTest(data=data), self.assertRaises(AudioTrackError):
                parse_audio_tracks(data)

    def test_bad_channels_fail_closed(self):
        for bad in (-1, 0, 33, True, "6", None, 2.5):
            with self.subTest(channels=bad), self.assertRaises(AudioTrackError):
                parse_audio_tracks({"streams": [{"codec_type": "audio", "channels": bad}]})

    def test_unrecognized_tags_use_und_language(self):
        tracks = parse_audio_tracks({"streams": [
            {"codec_type": "audio", "channels": 1, "tags": "bad"},
            {"codec_type": "audio", "channels": 2, "tags": {"language": 7}},
        ]})
        self.assertEqual([t.language for t in tracks], ["und", "und"])


if __name__ == "__main__":
    unittest.main()
