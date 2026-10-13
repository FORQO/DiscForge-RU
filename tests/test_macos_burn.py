"""Pure unit tests for macOS burn command. They never touch optical hardware."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import macos_burn


class MacBurnTests(unittest.TestCase):
    def make_iso(self, directory: str, sectors: int = 8) -> Path:
        image = Path(directory) / "test.iso"
        image.write_bytes(b"\0" * (sectors * 2048))
        return image

    def test_iso_validation_and_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = self.make_iso(tmp)
            self.assertEqual(macos_burn.validate_image(image), image.resolve())
            cmd = macos_burn.burn_command(image, device="drive-1", speed=4)
            self.assertEqual(cmd[:2], ["hdiutil", "burn"])
            self.assertIn("-verifyburn", cmd)
            self.assertIn("-nosynthesize", cmd)
            self.assertIn("-forceclose", cmd)
            self.assertEqual(cmd[-4:], ["-device", "drive-1", "-speed", "4"])

    def test_invalid_iso(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(macos_burn.MacBurnError):
                macos_burn.validate_image(Path(tmp) / "missing.iso")
            bad = Path(tmp) / "bad.iso"
            bad.write_bytes(b"not aligned")
            with self.assertRaises(macos_burn.MacBurnError):
                macos_burn.validate_image(bad)
            bad.write_bytes(b"")
            with self.assertRaises(macos_burn.MacBurnError):
                macos_burn.validate_image(bad)
            wrong = Path(tmp) / "movie.mkv"
            wrong.touch()
            with self.assertRaises(macos_burn.MacBurnError):
                macos_burn.validate_image(wrong)

    def test_non_udf_iso_does_not_touch_recorder(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = self.make_iso(tmp, sectors=300)
            with patch("macos_burn.sys.platform", "darwin"), \
                 patch("macos_burn.subprocess.run") as runner:
                with self.assertRaisesRegex(macos_burn.MacBurnError, "UDF 2.50"):
                    macos_burn.burn(image)
                runner.assert_not_called()

    def test_check_rejects_non_udf_without_hardware(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = self.make_iso(tmp, sectors=300)
            with patch("macos_burn.subprocess.run") as runner:
                self.assertEqual(macos_burn.main(["check", str(image)]), 1)
                runner.assert_not_called()

    def test_check_uses_explicit_profile_without_hardware(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = self.make_iso(tmp, sectors=300)
            good = SimpleNamespace(valid=True, errors=[])
            with patch("macos_burn.inspect_udf", return_value=good) as inspector, \
                 patch("macos_burn.subprocess.run") as runner:
                self.assertEqual(macos_burn.main(["check", str(image), "--profile", "BD50"]), 0)
                inspector.assert_called_once_with(image.resolve(), profile="BD50")
                runner.assert_not_called()

    def test_explicit_bd25_profile_rejects_oversized_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = self.make_iso(tmp, sectors=300)
            with patch.dict(macos_burn.CAPACITIES, {"BD25": 2048 * 100}):
                with self.assertRaisesRegex(macos_burn.MacBurnError, "BD25"):
                    macos_burn.validate_for_burning(image, profile="BD25")

    def test_auto_profile_uses_bd50_above_bd25_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = self.make_iso(tmp, sectors=300)
            good = SimpleNamespace(valid=True, errors=[])
            with patch.dict(macos_burn.CAPACITIES, {"BD25": 2048 * 100}), \
                 patch("macos_burn.inspect_udf", return_value=good) as inspector:
                self.assertEqual(macos_burn.validate_for_burning(image), image.resolve())
                inspector.assert_called_once_with(image.resolve(), profile="BD50")

    def test_symlink_rejected_before_udf_inspection(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = self.make_iso(tmp)
            link = Path(tmp) / "alias.iso"
            try:
                link.symlink_to(image)
            except (OSError, NotImplementedError):
                self.skipTest("Symlinks unavailable")
            with patch("macos_burn.inspect_udf") as inspector:
                with self.assertRaisesRegex(macos_burn.MacBurnError, "Символическая"):
                    macos_burn.validate_for_burning(link)
                inspector.assert_not_called()

    def test_speed_and_device_rejected(self):
        with self.assertRaises(macos_burn.MacBurnError):
            macos_burn.burn_command(Path("test.iso"), speed=0)
        with self.assertRaises(macos_burn.MacBurnError):
            macos_burn.burn_command(Path("test.iso"), speed=25)
        with self.assertRaises(macos_burn.MacBurnError):
            macos_burn.burn_command(Path("test.iso"), speed=True)
        with self.assertRaises(macos_burn.MacBurnError):
            macos_burn.burn_command(Path("test.iso"), device="-erase")

    def test_dry_run_never_executes(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = self.make_iso(tmp)
            with patch("macos_burn.inspect_udf",
                       return_value=SimpleNamespace(valid=True, errors=[])) as inspector, \
                 patch("macos_burn.subprocess.run") as runner, patch("builtins.print"):
                self.assertEqual(macos_burn.main(["burn", str(image), "--dry-run"]), 0)
                inspector.assert_called_once()
                runner.assert_not_called()

    def test_dry_run_rejects_non_udf_without_hardware(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = self.make_iso(tmp, sectors=300)
            with patch("macos_burn.subprocess.run") as runner:
                self.assertEqual(macos_burn.main(["burn", str(image), "--dry-run"]), 1)
                runner.assert_not_called()

    def test_without_yes_never_executes(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = self.make_iso(tmp)
            with patch("macos_burn.subprocess.run") as runner:
                self.assertEqual(macos_burn.main(["burn", str(image)]), 1)
                runner.assert_not_called()

    def test_platform_guard(self):
        with patch("macos_burn.sys.platform", "linux"):
            with self.assertRaises(macos_burn.MacBurnError):
                macos_burn.list_drives()
            with self.assertRaises(macos_burn.MacBurnError):
                macos_burn.burn("/tmp/test.iso")

    def test_success_and_failure_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = self.make_iso(tmp)
            with patch("macos_burn.sys.platform", "darwin"), \
                 patch("macos_burn.inspect_udf", return_value=SimpleNamespace(valid=True, errors=[])), \
                 patch("macos_burn.subprocess.run") as runner:
                runner.return_value.returncode = 0
                macos_burn.burn(image)
                self.assertTrue(runner.called)
                runner.return_value.returncode = 2
                with self.assertRaises(macos_burn.MacBurnError):
                    macos_burn.burn(image)


    def test_opt_in_mounted_bdmv_check_without_optical_drive(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = self.make_iso(tmp)
            good = SimpleNamespace(valid=True, errors=[])
            with patch("macos_burn.inspect_udf", return_value=good), \
                 patch("tools.macos_mounted_bdmv.inspect_mounted_bdmv") as inspector, \
                 patch("macos_burn.subprocess.run") as recorder:
                self.assertEqual(macos_burn.main(["check", str(image), "--inspect-bdmv"]), 0)
                inspector.assert_called_once_with(image.resolve(), profile="BD25")
                recorder.assert_not_called()

    def test_invalid_mounted_bdmv_blocks_dry_run_and_burn(self):
        from tools.macos_mounted_bdmv import MountedBDMVError
        with tempfile.TemporaryDirectory() as tmp:
            image = self.make_iso(tmp)
            good = SimpleNamespace(valid=True, errors=[])
            with patch("macos_burn.inspect_udf", return_value=good), \
                 patch("tools.macos_mounted_bdmv.inspect_mounted_bdmv",
                       side_effect=MountedBDMVError("Invalid BDMV")) as inspector, \
                 patch("macos_burn.subprocess.run") as recorder:
                self.assertEqual(macos_burn.main(["burn", str(image), "--dry-run",
                                                  "--inspect-bdmv"]), 1)
                with patch("macos_burn.sys.platform", "darwin"):
                    with self.assertRaisesRegex(macos_burn.MacBurnError, "Invalid BDMV"):
                        macos_burn.burn(image, inspect_bdmv=True)
                self.assertEqual(inspector.call_count, 2)
                recorder.assert_not_called()

    def test_udf_preflight_must_pass_before_mounted_bdmv_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = self.make_iso(tmp, sectors=300)
            with patch("tools.macos_mounted_bdmv.inspect_mounted_bdmv") as inspector:
                with self.assertRaisesRegex(macos_burn.MacBurnError, "UDF 2.50"):
                    macos_burn.validate_for_burning(image, inspect_bdmv=True)
                inspector.assert_not_called()

    def test_opt_in_mounted_bdmv_passes_before_actual_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = self.make_iso(tmp)
            good = SimpleNamespace(valid=True, errors=[])
            with patch("macos_burn.sys.platform", "darwin"), \
                 patch("macos_burn.inspect_udf", return_value=good), \
                 patch("tools.macos_mounted_bdmv.inspect_mounted_bdmv") as inspector, \
                 patch("macos_burn.subprocess.run",
                       return_value=SimpleNamespace(returncode=0)) as recorder:
                macos_burn.burn(image, inspect_bdmv=True)
                inspector.assert_called_once_with(image.resolve(), profile="BD25")
                self.assertEqual(recorder.call_count, 1)
                self.assertEqual(recorder.call_args.args[0][:2], ["hdiutil", "burn"])


if __name__ == "__main__":
    unittest.main()
