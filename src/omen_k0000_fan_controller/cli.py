#!/usr/bin/env python3
"""
omen-k0000-fan-controller。

The scheduler consumes the OMEN Command Center platform JSON in this bundle and
drives the Linux hp-wmi hwmon PWM interface. Fan table speeds are OEM units
(hundreds of RPM); Linux exposes a 0..255 PWM value, so the writer scales the
target speed against the loaded table maximum.
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "data/Hendricks_N20E.json"

PROFILE_TO_JSON_KEY = {
    "default": "SwFanControlCustomDefault",
    "performance": "SwFanControlCustomPerformance",
    "fan-curve": "SwFanControlCustomFanCurve",
}

SENSOR_ORDER = ("CPU", "GPU", "IR")


@dataclass(frozen=True)
class SensorCandidate:
    kind: str
    path: Path
    score: int
    name: str
    label: str


@dataclass
class SensorReading:
    raw: Dict[str, float]
    smoothed: Dict[str, float]


@dataclass(frozen=True)
class FanCurve:
    profile: str
    lambda_increase: float
    lambda_decrease: float
    tables: Dict[str, List[Tuple[float, int]]]
    throttle_c: Optional[float]
    max_speed: int

    @classmethod
    def from_platform_json(cls, path: Path, profile: str) -> "FanCurve":
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)

        key = PROFILE_TO_JSON_KEY[profile]
        block = data[key]
        fan_table = block["FanTable"]
        tables = {
            "CPU": pair_table(
                fan_table["Fan_Table_CPU_Temperature_List"],
                fan_table["Fan_Table_CPU_Fan_Speed_List"],
            ),
            "GPU": pair_table(
                fan_table["Fan_Table_GPU_Temperature_List"],
                fan_table["Fan_Table_GPU_Fan_Speed_List"],
            ),
            "IR": pair_table(
                fan_table["Fan_Table_IR_Temperature_List"],
                fan_table["Fan_Table_IR_Fan_Speed_List"],
            ),
        }
        speeds = [speed for table in tables.values() for _, speed in table]
        throttle_key = (
            "temperatureThrottlingPerformance"
            if profile == "performance"
            else "temperatureThrottlingBalance"
        )
        return cls(
            profile=profile,
            lambda_increase=float(block["Lamda_Increase"]),
            lambda_decrease=float(block["Lamda_Decrease"]),
            tables=tables,
            throttle_c=float(data[throttle_key]) if throttle_key in data else None,
            max_speed=max(speeds),
        )

    def target_for(self, sensor: str, temp_c: float) -> int:
        table = self.tables[sensor]
        speed = table[0][1]
        for threshold, next_speed in table:
            if temp_c >= threshold:
                speed = next_speed
            else:
                break
        return speed

    def target_speed(self, temps: Mapping[str, float]) -> Tuple[int, Dict[str, int]]:
        per_sensor: Dict[str, int] = {}
        for sensor in SENSOR_ORDER:
            if sensor in temps:
                per_sensor[sensor] = self.target_for(sensor, temps[sensor])

        if not per_sensor:
            raise RuntimeError("no usable temperature readings")

        target = max(per_sensor.values())
        if self.throttle_c is not None:
            cpu_temp = temps.get("CPU")
            if cpu_temp is not None and cpu_temp >= self.throttle_c:
                target = self.max_speed
        return target, per_sensor


class EwmaFilter:
    def __init__(self, lambda_increase: float, lambda_decrease: float) -> None:
        self.lambda_increase = lambda_increase
        self.lambda_decrease = lambda_decrease
        self.values: Dict[str, float] = {}

    def update(self, raw: Mapping[str, float]) -> Dict[str, float]:
        result: Dict[str, float] = {}
        for sensor, current in raw.items():
            previous = self.values.get(sensor)
            if previous is None:
                result[sensor] = current
                continue
            lam = self.lambda_increase if current >= previous else self.lambda_decrease
            result[sensor] = previous * lam + current * (1.0 - lam)
        self.values.update(result)
        return result


class SensorReader:
    def __init__(
        self,
        paths: Mapping[str, Optional[Path]],
        enabled: Sequence[str],
        gpu_temp_policy: str,
        simulate: Optional[Mapping[str, float]] = None,
    ) -> None:
        self.paths = paths
        self.enabled = tuple(enabled)
        self.gpu_temp_policy = gpu_temp_policy
        self.gpu_runtime_status_path = find_runtime_status_for_path(paths.get("GPU"))
        self.gpu_skip_logged = False
        self.simulate = dict(simulate) if simulate else None

        if "GPU" in self.enabled and paths.get("GPU") is not None:
            if self.gpu_temp_policy == "active-only":
                if self.gpu_runtime_status_path is not None:
                    logging.info(
                        "using GPU runtime status: %s", self.gpu_runtime_status_path
                    )
                else:
                    logging.warning(
                        "GPU runtime status not found; GPU temperature reads will be skipped"
                    )

    @classmethod
    def discover(
        cls,
        enabled: Sequence[str],
        cpu_path: Optional[str],
        gpu_path: Optional[str],
        ir_path: Optional[str],
        gpu_temp_policy: str,
        simulate: Optional[Mapping[str, float]],
    ) -> "SensorReader":
        if simulate:
            return cls(
                {},
                enabled=enabled,
                gpu_temp_policy=gpu_temp_policy,
                simulate=simulate,
            )

        discovered = discover_temperature_sensors()
        explicit = {
            "CPU": Path(cpu_path) if cpu_path else None,
            "GPU": Path(gpu_path) if gpu_path else None,
            "IR": Path(ir_path) if ir_path else None,
        }
        paths: Dict[str, Optional[Path]] = {}
        for sensor in enabled:
            paths[sensor] = explicit[sensor] or discovered.get(sensor)

        found = {key: path for key, path in paths.items() if path is not None}
        if not found:
            raise RuntimeError(
                "no enabled temperature sensors found; pass an explicit temp path or use --simulate"
            )

        for sensor, path in found.items():
            logging.info("using %s temperature sensor: %s", sensor, path)
        missing = [sensor for sensor in enabled if paths.get(sensor) is None]
        if missing:
            logging.warning("missing sensors will be ignored: %s", ", ".join(missing))
        return cls(paths, enabled=enabled, gpu_temp_policy=gpu_temp_policy)

    def read(self) -> Dict[str, float]:
        if self.simulate is not None:
            return {
                sensor: self.simulate[sensor]
                for sensor in self.enabled
                if sensor in self.simulate
            }

        readings: Dict[str, float] = {}
        for sensor, path in self.paths.items():
            if path is None:
                continue
            if sensor == "GPU" and self.gpu_temp_policy == "active-only":
                if not self.gpu_is_runtime_active():
                    continue
            try:
                readings[sensor] = read_temp_c(path)
            except OSError as exc:
                logging.warning("failed to read %s from %s: %s", sensor, path, exc)
        return readings

    def gpu_is_runtime_active(self) -> bool:
        if self.gpu_runtime_status_path is None:
            if not self.gpu_skip_logged:
                logging.debug("skipping GPU temperature read: no runtime_status path")
                self.gpu_skip_logged = True
            return False

        status = read_optional_text(self.gpu_runtime_status_path)
        if status == "active":
            self.gpu_skip_logged = False
            return True

        if not self.gpu_skip_logged:
            logging.debug("skipping GPU temperature read: runtime_status=%s", status)
            self.gpu_skip_logged = True
        return False


class FanWriter:
    def __init__(
        self,
        pwm_path: Optional[Path],
        pwm_enable_path: Optional[Path],
        max_speed: int,
        dry_run: bool,
        restore_auto: bool,
    ) -> None:
        self.pwm_path = pwm_path
        self.pwm_enable_path = pwm_enable_path
        self.max_speed = max_speed
        self.dry_run = dry_run
        self.restore_auto = restore_auto
        self.last_pwm: Optional[int] = None
        self.manual_enabled = False

    @classmethod
    def discover(
        cls,
        hwmon: Optional[str],
        pwm: Optional[str],
        pwm_enable: Optional[str],
        max_speed: int,
        dry_run: bool,
        restore_auto: bool,
    ) -> "FanWriter":
        pwm_path = Path(pwm) if pwm else None
        enable_path = Path(pwm_enable) if pwm_enable else None

        if (pwm_path is None) != (enable_path is None):
            raise RuntimeError("--pwm and --pwm-enable must be supplied together")

        if pwm_path is None:
            hwmon_path = Path(hwmon) if hwmon else find_hp_hwmon()
            if hwmon_path is not None:
                pwm_path = hwmon_path / "pwm1"
                enable_path = hwmon_path / "pwm1_enable"

        if pwm_path is None or enable_path is None:
            if dry_run:
                logging.warning("hp-wmi PWM not found; dry-run will only print decisions")
                return cls(None, None, max_speed, dry_run, restore_auto)
            raise RuntimeError(
                "hp-wmi PWM not found; ensure the kernel hp-wmi hwmon support is loaded"
            )

        logging.info("using fan PWM: %s", pwm_path)
        logging.info("using fan PWM mode: %s", enable_path)
        return cls(pwm_path, enable_path, max_speed, dry_run, restore_auto)

    def apply_speed(self, speed: int) -> int:
        clamped = max(0, min(speed, self.max_speed))
        pwm = round((clamped / self.max_speed) * 255) if self.max_speed > 0 else 0
        pwm = max(0, min(pwm, 255))

        if self.dry_run:
            logging.info("dry-run: target speed=%s pwm=%s", clamped, pwm)
            self.last_pwm = pwm
            return pwm

        assert self.pwm_path is not None
        assert self.pwm_enable_path is not None
        if not self.manual_enabled:
            write_text(self.pwm_enable_path, "1\n")
            self.manual_enabled = True
        if pwm != self.last_pwm:
            write_text(self.pwm_path, f"{pwm}\n")
            self.last_pwm = pwm
        return pwm

    def cleanup(self) -> None:
        if self.dry_run or not self.restore_auto or self.pwm_enable_path is None:
            return
        try:
            write_text(self.pwm_enable_path, "2\n")
            logging.info("restored hp-wmi fan mode to automatic")
        except OSError as exc:
            logging.warning("failed to restore automatic fan mode: %s", exc)


class PlatformProfile:
    def __init__(self, requested: str, dry_run: bool) -> None:
        self.requested = requested
        self.dry_run = dry_run
        self.path = Path("/sys/firmware/acpi/platform_profile")
        self.choices_path = Path("/sys/firmware/acpi/platform_profile_choices")

    def apply(self) -> None:
        if self.requested == "keep":
            return
        if not self.path.exists() or not self.choices_path.exists():
            logging.warning("platform_profile sysfs interface is not available")
            return
        choices = self.choices_path.read_text(encoding="utf-8").split()
        if self.requested not in choices:
            logging.warning(
                "platform profile %s not supported; choices: %s",
                self.requested,
                ", ".join(choices),
            )
            return
        if self.dry_run:
            logging.info("dry-run: would set platform_profile=%s", self.requested)
            return
        write_text(self.path, f"{self.requested}\n")
        logging.info("set platform_profile=%s", self.requested)


class Scheduler:
    def __init__(
        self,
        curve: FanCurve,
        reader: SensorReader,
        writer: FanWriter,
        log_every: int,
    ) -> None:
        self.curve = curve
        self.reader = reader
        self.writer = writer
        self.filter = EwmaFilter(curve.lambda_increase, curve.lambda_decrease)
        self.log_every = log_every
        self.ticks = 0
        self.last_speed: Optional[int] = None

    def tick(self) -> SensorReading:
        raw = self.reader.read()
        if not raw:
            raise RuntimeError("all temperature sensor reads failed")

        smoothed = self.filter.update(raw)
        speed, per_sensor = self.curve.target_speed(smoothed)
        pwm = self.writer.apply_speed(speed)

        changed = speed != self.last_speed
        should_log = changed or self.ticks % self.log_every == 0
        if should_log:
            logging.info(
                "temps raw=%s ewma=%s per_sensor=%s target_speed=%s pwm=%s",
                format_float_map(raw),
                format_float_map(smoothed),
                per_sensor,
                speed,
                pwm,
            )
        self.last_speed = speed
        self.ticks += 1
        return SensorReading(raw=raw, smoothed=smoothed)


def pair_table(temps: Sequence[float], speeds: Sequence[int]) -> List[Tuple[float, int]]:
    if len(temps) != len(speeds):
        raise ValueError("temperature and fan-speed lists have different lengths")
    return sorted((float(temp), int(speed)) for temp, speed in zip(temps, speeds))


def read_temp_c(path: Path) -> float:
    value = float(path.read_text(encoding="utf-8").strip())
    return value / 1000.0 if abs(value) > 1000 else value


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def find_runtime_status_for_path(path: Optional[Path]) -> Optional[Path]:
    if path is None:
        return None

    try:
        resolved = path.resolve()
    except OSError:
        return None

    fallback: Optional[Path] = None
    for parent in (resolved, *resolved.parents):
        runtime_status = parent / "power/runtime_status"
        if not runtime_status.exists():
            continue
        if fallback is None:
            fallback = runtime_status

        pci_class = read_optional_text(parent / "class").lower()
        if pci_class.startswith("0x03"):
            return runtime_status

    return fallback


def discover_temperature_sensors() -> Dict[str, Path]:
    candidates: Dict[str, List[SensorCandidate]] = {sensor: [] for sensor in SENSOR_ORDER}
    for hwmon in Path("/sys/class/hwmon").glob("hwmon*"):
        name = read_optional_text(hwmon / "name").lower()
        for input_path in hwmon.glob("temp*_input"):
            stem = input_path.name[: -len("_input")]
            label = read_optional_text(hwmon / f"{stem}_label").lower()
            for candidate in score_temperature_candidate(name, label, input_path):
                candidates[candidate.kind].append(candidate)

    result: Dict[str, Path] = {}
    for sensor, sensor_candidates in candidates.items():
        if not sensor_candidates:
            continue
        best = max(sensor_candidates, key=lambda item: item.score)
        result[sensor] = best.path
        logging.debug(
            "selected %s temp candidate: %s name=%s label=%s score=%s",
            sensor,
            best.path,
            best.name,
            best.label,
            best.score,
        )
    return result


def score_temperature_candidate(
    name: str, label: str, path: Path
) -> Iterable[SensorCandidate]:
    text = f"{name} {label}"

    cpu_score = 0
    if name in {"coretemp", "k10temp", "zenpower"}:
        cpu_score += 50
    if any(token in text for token in ("package", "tctl", "tdie", "cpu")):
        cpu_score += 30
    if "ccd" in text:
        cpu_score += 10
    if cpu_score:
        yield SensorCandidate("CPU", path, cpu_score, name, label)

    gpu_score = 0
    if name in {"amdgpu", "nvidia", "nouveau"}:
        gpu_score += 50
    if any(token in text for token in ("gpu", "junction", "edge", "hotspot")):
        gpu_score += 25
    if "junction" in text or "hotspot" in text:
        gpu_score += 10
    if gpu_score:
        yield SensorCandidate("GPU", path, gpu_score, name, label)

    ir_score = 0
    if any(token in text for token in ("ir", "surface", "skin", "ambient")):
        ir_score += 70
    if ir_score:
        yield SensorCandidate("IR", path, ir_score, name, label)


def find_hp_hwmon() -> Optional[Path]:
    for hwmon in Path("/sys/class/hwmon").glob("hwmon*"):
        if read_optional_text(hwmon / "name") != "hp":
            continue
        if (hwmon / "pwm1").exists() and (hwmon / "pwm1_enable").exists():
            return hwmon
    return None


def read_optional_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def parse_simulate(value: Optional[str]) -> Optional[Dict[str, float]]:
    if not value:
        return None
    result: Dict[str, float] = {}
    for part in value.split(","):
        if not part:
            continue
        if "=" not in part:
            raise argparse.ArgumentTypeError(
                "simulate entries must look like CPU=80,GPU=70,IR=42"
            )
        key, raw_temp = part.split("=", 1)
        sensor = key.strip().upper()
        if sensor not in SENSOR_ORDER:
            raise argparse.ArgumentTypeError(f"unknown simulated sensor: {sensor}")
        result[sensor] = float(raw_temp)
    if not result:
        raise argparse.ArgumentTypeError("no simulated temperatures supplied")
    return result


def parse_sensors(value: str) -> Tuple[str, ...]:
    sensors: List[str] = []
    for part in value.split(","):
        sensor = part.strip().upper()
        if not sensor:
            continue
        if sensor == "ALL":
            return SENSOR_ORDER
        if sensor not in SENSOR_ORDER:
            raise argparse.ArgumentTypeError(f"unknown sensor: {sensor}")
        if sensor not in sensors:
            sensors.append(sensor)
    if not sensors:
        raise argparse.ArgumentTypeError("at least one sensor must be enabled")
    return tuple(sensors)


def format_float_map(values: Mapping[str, float]) -> Dict[str, float]:
    return {key: round(value, 1) for key, value in values.items()}


def check_board(ignore_board: bool, dry_run: bool) -> None:
    board = read_optional_text(Path("/sys/class/dmi/id/board_name"))
    if not board:
        logging.warning("unable to read DMI board_name")
        return
    if board == "8A4D":
        logging.info("detected supported board: %s", board)
        return
    message = f"detected board {board}, but this bundle targets 8A4D"
    if ignore_board or dry_run:
        logging.warning("%s", message)
        return
    raise RuntimeError(f"{message}; pass --ignore-board to override")


def dump_curve(curve: FanCurve) -> None:
    print(f"profile: {curve.profile}")
    print(f"lambda_increase: {curve.lambda_increase}")
    print(f"lambda_decrease: {curve.lambda_decrease}")
    print(f"throttle_c: {curve.throttle_c}")
    print(f"max_speed: {curve.max_speed}")
    for sensor in SENSOR_ORDER:
        print(sensor)
        for temp, speed in curve.tables[sensor]:
            print(f"  {temp:g} C -> {speed}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="omen-k0000-fan-controller"
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--profile",
        choices=sorted(PROFILE_TO_JSON_KEY),
        default="performance",
        help="fan table profile loaded from the OMEN platform JSON",
    )
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--once", action="store_true", help="run a single scheduler tick")
    parser.add_argument("--dry-run", action="store_true", help="do not write sysfs")
    parser.add_argument("--dump-curve", action="store_true", help="print the loaded curve")
    parser.add_argument(
        "--sensors",
        type=parse_sensors,
        default=("CPU", "GPU"),
        help="comma-separated sensors to monitor: CPU, GPU, IR, or ALL; default: CPU,GPU",
    )
    parser.add_argument(
        "--simulate",
        type=parse_simulate,
        help="skip sysfs sensors and use values like CPU=80,GPU=70,IR=42",
    )
    parser.add_argument("--cpu-temp", help="explicit CPU temp*_input sysfs path")
    parser.add_argument("--gpu-temp", help="explicit GPU temp*_input sysfs path")
    parser.add_argument(
        "--gpu-temp-policy",
        choices=("active-only", "always"),
        default="active-only",
        help="read GPU temperature only when runtime_status is active, or always",
    )
    parser.add_argument("--ir-temp", help="explicit IR/surface temp*_input sysfs path")
    parser.add_argument("--hwmon", help="explicit hp hwmon directory")
    parser.add_argument("--pwm", help="explicit pwm1 path")
    parser.add_argument("--pwm-enable", help="explicit pwm1_enable path")
    parser.add_argument(
        "--fan-speed-max",
        type=int,
        help="OEM fan speed value that maps to PWM 255; defaults to table maximum",
    )
    parser.add_argument(
        "--platform-profile",
        default="keep",
        help="optional platform_profile value to write, or keep",
    )
    parser.add_argument(
        "--no-restore-auto",
        action="store_true",
        help="do not return hp-wmi fan mode to automatic on exit",
    )
    parser.add_argument("--ignore-board", action="store_true")
    parser.add_argument("--log-every", type=int, default=10)
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    configure_logging(args.verbose)

    curve = FanCurve.from_platform_json(args.config, args.profile)
    if args.dump_curve:
        dump_curve(curve)
        return 0

    check_board(args.ignore_board, args.dry_run)
    PlatformProfile(args.platform_profile, args.dry_run).apply()

    max_speed = args.fan_speed_max or curve.max_speed
    reader = SensorReader.discover(
        args.sensors,
        args.cpu_temp,
        args.gpu_temp,
        args.ir_temp,
        gpu_temp_policy=args.gpu_temp_policy,
        simulate=args.simulate,
    )
    writer = FanWriter.discover(
        args.hwmon,
        args.pwm,
        args.pwm_enable,
        max_speed=max_speed,
        dry_run=args.dry_run,
        restore_auto=not args.no_restore_auto,
    )
    scheduler = Scheduler(curve, reader, writer, log_every=max(1, args.log_every))

    stop = False

    def handle_signal(signum: int, _frame: object) -> None:
        nonlocal stop
        logging.info("received signal %s, stopping", signum)
        stop = True

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    try:
        scheduler.tick()
        while not args.once and not stop:
            time.sleep(args.interval)
            scheduler.tick()
    finally:
        writer.cleanup()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(1)
    except Exception as exc:
        logging.error("%s", exc)
        raise SystemExit(1)
