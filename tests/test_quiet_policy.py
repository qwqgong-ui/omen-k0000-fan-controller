import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from omen_k0000_fan_controller.cli import (
    DEFAULT_CONFIG, FanCurve, FanWriter, Scheduler, SensorReader,
)


class QuietPolicyTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.paths = tuple(self.directory / name for name in ("pwm1", "pwm2"))
        self.enable = self.directory / "pwm1_enable"
        for path in (*self.paths, self.enable):
            path.write_text("0\n")
        self.curve = FanCurve.from_platform_json(DEFAULT_CONFIG, "performance")
        self.writer = FanWriter(
            self.paths, self.enable, 58, False, True, decrease_delay=0.5,
        )
        self.reader = SensorReader(
            {}, ("CPU", "GPU", "SPD", "IR"), "active-only", 10,
            simulate={"CPU": 40.0, "SPD": 48.0},
        )
        self.scheduler = Scheduler(self.curve, self.reader, self.writer, 10)

    def outputs(self):
        return tuple(int(path.read_text()) for path in self.paths)

    def test_40_degrees_runs_cpu_at_calibrated_1500_and_stops_gpu(self):
        self.scheduler.tick()
        self.assertEqual(self.outputs(), (71, 0))
        # Model the two integer round trips in hp-wmi with max_rpm=58.
        level = 71 * 58 // 255
        self.assertEqual((level * 255 // 58) * 58 // 255, 15)

    @patch("omen_k0000_fan_controller.cli.time.sleep")
    def test_45_raw_restarts_gpu_without_waiting_for_ewma_or_delay(self, sleep):
        self.scheduler.tick()
        self.reader.simulate["CPU"] = 45.0
        reading = self.scheduler.tick()
        self.assertLess(reading.smoothed["CPU"], 45)
        self.assertEqual(self.outputs(), (92, 92))
        sleep.assert_not_called()

    def test_gpu_at_threshold_also_uses_both_fans(self):
        self.reader.simulate["GPU"] = 45.0
        self.scheduler.tick()
        self.assertEqual(self.outputs(), (92, 92))

    def test_spd_55_overrides_quiet_policy(self):
        self.reader.simulate["SPD"] = 55.0
        self.scheduler.tick()
        self.assertEqual(self.outputs(), (92, 92))

    def test_ir_higher_demand_is_preserved(self):
        self.reader.simulate["IR"] = 44.0
        self.scheduler.tick()
        self.assertEqual(self.outputs(), (145, 145))

    def test_high_temperature_keeps_maximum_cooling(self):
        self.reader.simulate["CPU"] = 95.0
        self.scheduler.tick()
        self.assertEqual(self.outputs(), (255, 255))

    @patch("omen_k0000_fan_controller.cli.time.sleep")
    def test_gpu_channel_updates_when_primary_pwm_is_unchanged(self, sleep):
        self.writer.apply_level(15, single_fan=False)
        self.writer.apply_level(15, single_fan=True)
        self.assertEqual(self.outputs(), (71, 0))
        sleep.assert_called_once_with(0.5)
        sleep.reset_mock()
        self.writer.apply_level(15, single_fan=False)
        self.assertEqual(self.outputs(), (71, 71))
        sleep.assert_not_called()

    def test_explicit_reverse_order_still_keeps_cpu_fan(self):
        writer = FanWriter(self.paths[::-1], self.enable, 58, False, True, 0)
        writer.apply_level(15, single_fan=True)
        self.assertEqual(self.outputs(), (71, 0))

    def test_cleanup_restores_automatic_mode(self):
        self.scheduler.tick()
        self.writer.cleanup()
        self.assertEqual(self.enable.read_text().strip(), "2")

    def test_only_spd_reading_cannot_stop_gpu(self):
        self.reader.simulate = {"SPD": 40.0}
        self.scheduler.tick()
        self.assertEqual(self.outputs(), (71, 71))

    def test_legacy_single_pwm_stays_nonzero(self):
        writer = FanWriter(self.paths[0], self.enable, 58, False, True, 0)
        writer.apply_level(15, single_fan=True)
        self.assertEqual(self.paths[0].read_text().strip(), "71")


if __name__ == "__main__":
    unittest.main()
