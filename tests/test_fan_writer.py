import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from omen_k0000_fan_controller.cli import FanWriter, find_pwm_paths


class FanWriterDelayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        directory = Path(self.temporary_directory.name)
        self.pwm_path = directory / "pwm1"
        self.enable_path = directory / "pwm1_enable"
        self.pwm_path.write_text("0\n", encoding="utf-8")
        self.enable_path.write_text("2\n", encoding="utf-8")
        self.writer = FanWriter(
            self.pwm_path,
            self.enable_path,
            max_level=100,
            dry_run=False,
            restore_auto=False,
            decrease_delay=0.5,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    @patch("omen_k0000_fan_controller.cli.time.sleep")
    def test_only_decrease_is_delayed(self, sleep) -> None:
        self.writer.apply_level(50)
        self.writer.apply_level(75)
        sleep.assert_not_called()

        self.writer.apply_level(60)
        sleep.assert_called_once_with(0.5)

    @patch("omen_k0000_fan_controller.cli.time.sleep")
    def test_unchanged_level_is_not_delayed(self, sleep) -> None:
        self.writer.apply_level(50)
        self.writer.apply_level(50)
        sleep.assert_not_called()


class FanWriterAllChannelsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)
        for name in ("pwm1", "pwm2"):
            (self.directory / name).write_text("0\n", encoding="utf-8")
        (self.directory / "pwm1_enable").write_text("2\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_find_pwm_paths_skips_enable_attribute(self) -> None:
        self.assertEqual(
            find_pwm_paths(self.directory),
            (self.directory / "pwm1", self.directory / "pwm2"),
        )

    def test_every_fan_receives_the_same_level(self) -> None:
        writer = FanWriter.discover(
            str(self.directory),
            None,
            None,
            max_level=100,
            dry_run=False,
            restore_auto=False,
            decrease_delay=0.0,
        )
        pwm = writer.apply_level(50)

        for name in ("pwm1", "pwm2"):
            with self.subTest(channel=name):
                self.assertEqual(
                    (self.directory / name).read_text(encoding="utf-8").strip(),
                    str(pwm),
                )
        self.assertEqual(
            (self.directory / "pwm1_enable").read_text(encoding="utf-8").strip(), "1"
        )

    def test_explicit_pwm_list_is_honoured(self) -> None:
        writer = FanWriter.discover(
            None,
            f"{self.directory / 'pwm2'},{self.directory / 'pwm1'}",
            str(self.directory / "pwm1_enable"),
            max_level=100,
            dry_run=False,
            restore_auto=False,
            decrease_delay=0.0,
        )
        self.assertEqual(
            writer.pwm_paths,
            (self.directory / "pwm2", self.directory / "pwm1"),
        )


if __name__ == "__main__":
    unittest.main()
