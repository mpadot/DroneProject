"""Continuous CoDrone EDU telemetry logging to CSV.

Teammates collect samples (from the drone or from a dict) and pass them here.
This module owns:
  - a stable timestamp + sensor column schema
  - flushing each row so a crash does not lose the flight
  - missing-sample handling (empty cells + missing_fields)
  - gap detection when the time between rows is too large

Expected teammate usage:

    from data_pipeline import TelemetryLogger, sample_from_drone

    logger = TelemetryLogger()
    # inside the flight loop:
    logger.log_sample(sample_from_drone(drone))
    csv_path = logger.close()
"""

from __future__ import annotations

import csv
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


# CoDrone EDU's 7 onboard sensors. Column names stay fixed so S3 files
# from different flights can be joined later.
SCHEMA = [
    "timestamp_utc",
    "timestamp_unix_ms",
    "sample_index",
    "interval_ms",
    "gap_detected",
    "missing_fields",
    # 1. Accelerometer
    "accel_x",
    "accel_y",
    "accel_z",
    # 2. Gyroscope (orientation + angular velocity)
    "angle_roll",
    "angle_pitch",
    "angle_yaw",
    "angular_speed_x",
    "angular_speed_y",
    "angular_speed_z",
    # 3. Barometer
    "pressure_pa",
    "elevation_m",
    # 4. Bottom range (height above ground)
    "height_cm",
    "bottom_range_cm",
    # 5. Front range
    "front_range_cm",
    # 6. Optical flow (position + velocity)
    "pos_x_cm",
    "pos_y_cm",
    "pos_z_cm",
    "flow_velocity_x",
    "flow_velocity_y",
    "speed_cm_s",
    # 7. Color sensors (often unavailable in flight — that is OK)
    "color_front",
    "color_back",
    # Extra status, not one of the 7
    "battery_pct",
    "temperature_c",
]

SENSOR_COLUMNS = [
    "accel_x",
    "accel_y",
    "accel_z",
    "angle_roll",
    "angle_pitch",
    "angle_yaw",
    "angular_speed_x",
    "angular_speed_y",
    "angular_speed_z",
    "pressure_pa",
    "elevation_m",
    "height_cm",
    "bottom_range_cm",
    "front_range_cm",
    "pos_x_cm",
    "pos_y_cm",
    "pos_z_cm",
    "flow_velocity_x",
    "flow_velocity_y",
    "speed_cm_s",
    "color_front",
    "color_back",
    "battery_pct",
    "temperature_c",
]

# If two samples are farther apart than this, mark gap_detected=1.
DEFAULT_GAP_THRESHOLD_MS = 500

DATA_DIR = Path(__file__).resolve().parent / "data" / "flights"


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _safe_get(drone: Any, method_name: str, *args: Any) -> Any:
    """Call a drone getter; return None if the read fails or the method is missing."""
    try:
        method = getattr(drone, method_name)
        value = method(*args) if args else method()
        if value is None:
            return None
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return value
    except Exception:
        return None


def sample_from_drone(drone: Any) -> dict[str, Any]:
    """Read one telemetry snapshot from a CoDrone EDU `Drone` object.

    Teammates can call this inside their flight loop. Any failed getter
    becomes None and is recorded in missing_fields by TelemetryLogger.
    Color sensors are expected to be missing while flying.
    """
    flow_x = _safe_get(drone, "get_flow_velocity_x")
    flow_y = _safe_get(drone, "get_flow_velocity_y")
    speed = None
    try:
        if flow_x is not None and flow_y is not None:
            speed = round(math.hypot(float(flow_x), float(flow_y)), 4)
    except (TypeError, ValueError):
        speed = None

    colors = _safe_get(drone, "get_colors")
    color_front = None
    color_back = None
    if isinstance(colors, (list, tuple)) and len(colors) >= 2:
        color_front, color_back = colors[0], colors[1]
    elif colors is not None:
        color_front = colors

    return {
        "accel_x": _safe_get(drone, "get_accel_x"),
        "accel_y": _safe_get(drone, "get_accel_y"),
        "accel_z": _safe_get(drone, "get_accel_z"),
        "angle_roll": _safe_get(drone, "get_angle_x"),
        "angle_pitch": _safe_get(drone, "get_angle_y"),
        "angle_yaw": _safe_get(drone, "get_angle_z"),
        "angular_speed_x": _safe_get(drone, "get_angular_speed_x"),
        "angular_speed_y": _safe_get(drone, "get_angular_speed_y"),
        "angular_speed_z": _safe_get(drone, "get_angular_speed_z"),
        "pressure_pa": _safe_get(drone, "get_pressure"),
        "elevation_m": _safe_get(drone, "get_elevation"),
        "height_cm": _safe_get(drone, "get_height"),
        "bottom_range_cm": _safe_get(drone, "get_bottom_range"),
        "front_range_cm": _safe_get(drone, "get_front_range"),
        "pos_x_cm": _safe_get(drone, "get_pos_x"),
        "pos_y_cm": _safe_get(drone, "get_pos_y"),
        "pos_z_cm": _safe_get(drone, "get_pos_z"),
        "flow_velocity_x": flow_x,
        "flow_velocity_y": flow_y,
        "speed_cm_s": speed,
        "color_front": color_front,
        "color_back": color_back,
        "battery_pct": _safe_get(drone, "get_battery"),
        "temperature_c": _safe_get(drone, "get_drone_temperature")
        if hasattr(drone, "get_drone_temperature")
        else _safe_get(drone, "get_temperature"),
    }


def simulate_sample(index: int) -> dict[str, Any]:
    """Fake a moving drone so CSV + S3 can be tested without PH 517."""
    t = index / 10.0
    return {
        "accel_x": round(math.sin(t) * 20, 3),
        "accel_y": round(math.cos(t) * 15, 3),
        "accel_z": 980,
        "angle_roll": round(math.sin(t / 2) * 8, 3),
        "angle_pitch": round(math.cos(t / 3) * 6, 3),
        "angle_yaw": round((index * 1.5) % 360 - 180, 3),
        "angular_speed_x": round(math.cos(t) * 5, 3),
        "angular_speed_y": round(math.sin(t) * 4, 3),
        "angular_speed_z": 12.0,
        "pressure_pa": 101325 - index,
        "elevation_m": 40 + index * 0.01,
        "height_cm": 50 + (index % 40),
        "bottom_range_cm": 50 + (index % 40),
        "front_range_cm": 120 - (index % 30),
        "pos_x_cm": index * 2,
        "pos_y_cm": round(math.sin(t) * 10, 3),
        "pos_z_cm": 50 + (index % 40),
        "flow_velocity_x": 20,
        "flow_velocity_y": round(math.cos(t) * 3, 3),
        "speed_cm_s": round(math.hypot(20, math.cos(t) * 3), 4),
        "color_front": None,
        "color_back": None,
        "battery_pct": max(20, 95 - index // 20),
        "temperature_c": 31.5,
    }


class TelemetryLogger:
    """Append-only CSV logger with a fixed schema and crash-safe flushes."""

    def __init__(
        self,
        output_path: str | os.PathLike[str] | None = None,
        gap_threshold_ms: int = DEFAULT_GAP_THRESHOLD_MS,
    ) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if output_path is None:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            output_path = DATA_DIR / f"flight_{stamp}.csv"
        self.path = Path(output_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.gap_threshold_ms = gap_threshold_ms
        self._sample_index = 0
        self._last_unix_ms: int | None = None
        self._closed = False
        self._write_failures = 0

        new_file = not self.path.exists() or self.path.stat().st_size == 0
        self._fh = self.path.open("a", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(
            self._fh,
            fieldnames=SCHEMA,
            extrasaction="ignore",
        )
        if new_file:
            self._writer.writeheader()
            self._fh.flush()
            os.fsync(self._fh.fileno())

    def log_sample(self, sample: Mapping[str, Any] | None) -> dict[str, Any]:
        """Normalize one sample, write it, and return the stored row."""
        if self._closed:
            raise RuntimeError("TelemetryLogger is already closed.")

        unix_ms = _now_ms()
        interval_ms = ""
        gap_detected = 0
        if self._last_unix_ms is not None:
            interval_ms = unix_ms - self._last_unix_ms
            if interval_ms > self.gap_threshold_ms:
                gap_detected = 1
        self._last_unix_ms = unix_ms

        incoming = dict(sample or {})
        missing: list[str] = []
        row: dict[str, Any] = {
            "timestamp_utc": _utc_iso(),
            "timestamp_unix_ms": unix_ms,
            "sample_index": self._sample_index,
            "interval_ms": interval_ms,
            "gap_detected": gap_detected,
        }

        for column in SENSOR_COLUMNS:
            value = incoming.get(column, None)
            if value is None or value == "":
                row[column] = ""
                missing.append(column)
            else:
                row[column] = value

        row["missing_fields"] = "|".join(missing)
        self._sample_index += 1
        self._write_row(row)
        return row

    def _write_row(self, row: Mapping[str, Any]) -> None:
        try:
            self._writer.writerow(row)
            self._fh.flush()
            os.fsync(self._fh.fileno())
        except OSError as exc:
            self._write_failures += 1
            print(
                f"[data_pipeline] CSV write failed "
                f"(failure #{self._write_failures}): {exc}"
            )

    @property
    def sample_count(self) -> int:
        return self._sample_index

    @property
    def write_failures(self) -> int:
        return self._write_failures

    def close(self) -> Path:
        if not self._closed:
            try:
                self._fh.flush()
                os.fsync(self._fh.fileno())
            except OSError:
                pass
            self._fh.close()
            self._closed = True
        return self.path

    def __enter__(self) -> "TelemetryLogger":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def run_simulated_flight(seconds: float = 10.0, hz: float = 10.0) -> Path:
    """Write a few hundred simulated points and print them to the screen."""
    interval = 1.0 / hz
    with TelemetryLogger() as logger:
        index = 0
        deadline = time.time() + seconds
        while time.time() < deadline:
            row = logger.log_sample(simulate_sample(index))
            print(
                f"#{row['sample_index']:04d}  "
                f"alt={row['height_cm']}cm  "
                f"speed={row['speed_cm_s']}  "
                f"yaw={row['angle_yaw']}  "
                f"missing={row['missing_fields'] or '-'}"
            )
            index += 1
            time.sleep(interval)
        path = logger.close()
        print(f"\nWrote {logger.sample_count} samples to {path}")
        return path


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Log CoDrone EDU telemetry to CSV.")
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Write simulated telemetry (no drone required).",
    )
    parser.add_argument("--seconds", type=float, default=15.0)
    parser.add_argument("--hz", type=float, default=10.0)
    args = parser.parse_args()
    if args.simulate:
        run_simulated_flight(seconds=args.seconds, hz=args.hz)
    else:
        print(
            "This file is the CSV logger. Import TelemetryLogger from your "
            "collector, or run:  python data_pipeline.py --simulate"
        )
