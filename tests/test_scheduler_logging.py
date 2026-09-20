import logging
import unittest
from typing import Dict, List, Mapping, Tuple

from omen_k0000_fan_controller.cli import Scheduler, describe_reading


class StubCurve:
    """Maps every sensor to a caller-controlled level, with no smoothing."""

    # lambda 0 makes EwmaFilter pass raw values straight through, so a test
    # controls exactly what reaches the log.
    lambda_increase = 0.0
    lambda_decrease = 0.0

    def __init__(self) -> None:
        self.level = 24
        self.per_sensor: Dict[str, int] = {"CPU": 24, "SPD": 24}

    def target_level(self, temps: Mapping[str, float]) -> Tuple[int, Dict[str, int]]:
        return self.level, dict(self.per_sensor)


class StubReader:
    def __init__(self) -> None:
        self.temps: Dict[str, float] = {"CPU": 50.0, "SPD": 60.2}

    def read(self) -> Dict[str, float]:
        return dict(self.temps)


class StubWriter:
    def __init__(self) -> None:
        self.pwm = 106

    def apply_level(self, level: int) -> int:
        return self.pwm


class SchedulerLoggingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.curve = StubCurve()
        self.reader = StubReader()
        self.writer = StubWriter()
        self.records: List[str] = []

        handler = logging.Handler()
        handler.emit = lambda record: self.records.append(record.getMessage())
        self.logger = logging.getLogger()
        self.previous_level = self.logger.level
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)
        self.addCleanup(self.logger.removeHandler, handler)
        self.addCleanup(self.logger.setLevel, self.previous_level)

    def make_scheduler(self, keepalive: float = 0.0) -> Scheduler:
        return Scheduler(
            self.curve,
            self.reader,
            self.writer,
            log_every=10,
            log_keepalive=keepalive,
        )

    def test_unchanged_decision_is_not_reprinted(self) -> None:
        """The heartbeat used to fire on tick count alone.

        Raw temperatures move on every sample, so gating on them would let
        practically every heartbeat through; the decision is what must be
        unchanged for a line to be redundant.
        """
        scheduler = self.make_scheduler()
        for tick in range(120):
            # Temperatures drift the way real ones do, while the decision holds.
            self.reader.temps["CPU"] = 50.0 + (tick % 5) * 0.3
            scheduler.tick()

        self.assertEqual(
            len(self.records),
            1,
            f"120 ticks at a steady level logged {len(self.records)} lines, want 1",
        )

    def test_changed_decision_still_logs_immediately(self) -> None:
        scheduler = self.make_scheduler()
        scheduler.tick()
        self.records.clear()

        self.curve.level = 30
        self.curve.per_sensor = {"CPU": 30, "SPD": 24}
        self.writer.pwm = 132
        scheduler.tick()

        self.assertEqual(len(self.records), 1)
        self.assertIn("target_level=30", self.records[0])
        self.assertIn("pwm=132", self.records[0])

    def test_keepalive_breaks_a_long_silence(self) -> None:
        scheduler = self.make_scheduler(keepalive=0.0001)
        scheduler.tick()
        self.records.clear()
        # last_log_at is monotonic, so any positive elapsed time exceeds the
        # keepalive above without the test having to sleep.
        scheduler.last_log_at = scheduler.last_log_at - 1.0
        scheduler.tick()

        self.assertEqual(len(self.records), 1, "keepalive did not break the silence")

    def test_keepalive_zero_disables_the_liveness_line(self) -> None:
        scheduler = self.make_scheduler(keepalive=0.0)
        scheduler.tick()
        self.records.clear()
        scheduler.last_log_at = scheduler.last_log_at - 10_000.0
        for _ in range(50):
            scheduler.tick()

        self.assertEqual(self.records, [])


class DescribeReadingTest(unittest.TestCase):
    def test_settled_filter_drops_the_ewma_copy(self) -> None:
        raw = {"CPU": 50.0, "SPD": 60.2}
        line = describe_reading(raw, dict(raw), {"CPU": 24, "SPD": 24}, 24)

        self.assertNotIn("ewma=", line)
        self.assertNotIn("per_sensor=", line)
        self.assertIn("raw={'CPU': 50.0, 'SPD': 60.2}", line)

    def test_diverging_values_are_kept(self) -> None:
        raw = {"CPU": 69.0, "SPD": 60.2}
        smoothed = {"CPU": 61.4, "SPD": 60.2}
        line = describe_reading(raw, smoothed, {"CPU": 21, "SPD": 24}, 24)

        self.assertIn("ewma=", line)
        self.assertIn("per_sensor=", line)

    def test_rounding_decides_whether_ewma_is_redundant(self) -> None:
        """Both are rounded to one decimal before printing, so a difference
        smaller than that is not a difference on the line."""
        raw = {"CPU": 50.00}
        line = describe_reading(raw, {"CPU": 50.04}, {"CPU": 24}, 24)

        self.assertNotIn("ewma=", line)


if __name__ == "__main__":
    unittest.main()
