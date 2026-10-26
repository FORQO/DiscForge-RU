import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from core import make_video_command, create_bluray, DiscForgeError
from quality_trial import TrialPlan, reference_command, variant_command
from video_treatment import frame_filter, validate
from test_quality_trial import movie


class TreatmentTests(unittest.TestCase):
    def test_reference_and_full_encode_share_exact_filter(self):
        for nvenc in (True, False):
            cmd = make_video_command('ffmpeg', 'source', 'out', 8_000_000, nvenc,
                                     enhance=True, quiet_rate=1)
            plan = TrialPlan(2, 3, (8_000_000,), enhance=True, quiet_rate=1)
            ref = reference_command('ffmpeg', movie(), 'ref', plan, 71)
            self.assertEqual(cmd[cmd.index('-vf')+1], ref[ref.index('-vf')+1])
            self.assertEqual(cmd[cmd.index('-vf')+1], frame_filter())
            self.assertLess(cmd.index('-readrate'), cmd.index('-i'))
            self.assertLess(ref.index('-readrate'), ref.index('-i'))
            self.assertIn('hqdn3d=', frame_filter())
            self.assertIn('flags=lanczos', frame_filter())
            self.assertNotIn('-hwaccel', ref)
            trial = variant_command('ffmpeg', 'ref', 'out', 8_000_000, nvenc, False, 71, 30_000_000, 1)
            self.assertNotIn('-vf', trial)  # No second enhancement.
            self.assertIn('-readrate', trial)

    def test_off_is_unchanged_and_invalid_controls_rejected(self):
        cmd = make_video_command('ffmpeg', 'source', 'out', 8_000_000, True)
        self.assertNotIn('-readrate', cmd)
        self.assertNotIn('hqdn3d', cmd[cmd.index('-vf')+1])
        for enhance, rate in ((1, 1), (False, True), (False, float('nan')), (False, -1), (False, 3)):
            with self.assertRaises(ValueError):
                validate(enhance, rate)
        with self.assertRaisesRegex(ValueError, 'CPU'):
            reference_command('ffmpeg', movie(), 'ref', TrialPlan(0, 1, (8_000_000,), enhance=True), 23, True)

    def test_explicit_copy_cannot_silently_skip_enhancement(self):
        for mode in ('copy', 'video_copy'):
            with self.assertRaisesRegex(DiscForgeError, 'требует перекодирования'):
                create_bluray(movie(), 'BD25', '.', 'ffmpeg', 'tsmuxer', False, True,
                              threading.Event(), lambda _: None, lambda _: None, processing_mode=mode, enhance=True)


class TreatmentUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_trial_controls_bind_treatment_to_plan(self):
        from quality_dialog import QualityDialog
        dialog = QualityDialog(movie(), {'enhance': True, 'quiet_rate': 1}, 8_000_000)
        try:
            self.assertTrue(dialog._plan().enhance)
            self.assertEqual(dialog._plan().quiet_rate, 1)
            self.assertFalse(dialog.comparison_mode.isHidden())
            self.assertEqual(dialog.comparison_mode.count(), 3)
        finally:
            dialog.reject()
            dialog.deleteLater()


if __name__ == '__main__':
    unittest.main()
