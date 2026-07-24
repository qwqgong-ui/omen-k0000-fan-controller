import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from omen_k0000_fan_controller.cli import FanWriter


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


if __name__ == "__main__":
    unittest.main()
