"""
Author Name: Fahad Akon, Miles Padot, Mohammed Shrime
EID:         E02704365
Class:       COSC311
Assignment:  Milestone 1 of Drone Project
Date:        11/05/2025
"""
from codrone_edu.drone import *
import time
import math
import statistics  # Used for calculating variance
import csv  # For saving flight results
from pathlib import Path

from data_pipeline import TelemetryLogger, sample_from_drone, unique_csv_path
from s3_upload import upload_csv_files_to_s3

# --- Flight States ---
STATE_IDLE = 0
STATE_FLYING = 1
STATE_LANDED = 2
STATE_EMERGENCY = 3
STATE_PRACTICE_LAND = 4  # For Challenge 3


# --- Linked List Classes (for Challenge 5) ---
class FlightNode:
    """A node for the linked list, holding one flight's stats."""

    def __init__(self, stats_data):
        self.data = stats_data  # This is the stats dictionary
        self.next = None


class FlightHistory:
    """
    A linked list to store the history of all completed flights.
    We will also use this to save all data to a file.
    """

    def __init__(self):
        self.head = None
        self.count = 0

    def append(self, stats_data):
        """Adds a new flight's stats to the end of the list."""
        new_node = FlightNode(stats_data)
        self.count += 1
        if not self.head:
            self.head = new_node
            return

        last = self.head
        while last.next:
            last = last.next
        last.next = new_node

    def save_to_file(self, filename=None):
        """Save one row per flight to CSV and return the created path."""
        all_flights = []
        current = self.head
        while current:
            all_flights.append(current.data)
            current = current.next

        try:
            if filename is None:
                path = unique_csv_path("flight_results")
            else:
                requested = Path(filename)
                path = unique_csv_path(requested.stem, requested.parent)
            fields = [
                "pilot_name", "landing_type", "sample_count", "flight_time",
                "max_speed", "min_speed", "max_accel", "yaw_variance"
            ]
            with open(path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()
                writer.writerows(all_flights)
            print(f"Successfully saved {len(all_flights)} flight(s) to {path}")
            return path
        except Exception as e:
            print(f"Error saving file: {e}")
            return None

    def print_leaderboard(self):
        """Prints a sorted leaderboard to the console."""
        if not self.head:
            print("Leaderboard is empty.")
            return

        # 1. Convert linked list to a regular Python list for easy sorting
        all_flights = []
        current = self.head
        while current:
            all_flights.append(current.data)
            current = current.next

        # 2. Sort by flight time (key for the min-heap)
        all_flights.sort(key=lambda x: x.get('flight_time', float('inf')))

        # 3. Print the sorted list
        print("--- CURRENT LEADERBOARD (Sorted by Time) ---")
        for i, item in enumerate(all_flights):
            print(f"  {i + 1}. Pilot: {item.get('pilot_name', 'N/A')}, "
                  f"Time: {item.get('flight_time', 0):.2f}s, "
                  f"Max Speed: {item.get('max_speed', 0):.2f}")
        print("------------------------------------------\n")


def calculate_flight_stats(data_list, flight_time, pilot_name):
    """
    Processes the collected flight data list to calculate required stats.
    Now includes pilot_name.
    """
    all_speeds = [math.sqrt(d['vx'] ** 2 + d['vy'] ** 2) for d in data_list]
    all_accels = [math.sqrt(d['ax'] ** 2 + d['ay'] ** 2 + d['az'] ** 2) for d in data_list]
    all_yaws = [d['yaw'] for d in data_list]

    if len(set(all_yaws)) < 2:
        yaw_var = 0.0
    else:
        yaw_var = statistics.variance(all_yaws)

    stats = {
        "pilot_name": pilot_name,  # Added pilot name
        "landing_type": "competition",
        "sample_count": len(data_list),
        "flight_time": flight_time,
        "max_speed": max(all_speeds, default=0.0),
        "min_speed": min(all_speeds, default=0.0),
        "max_accel": max(all_accels, default=0.0),
        "yaw_variance": yaw_var
    }
    return stats


def flight_state_name(drone_obj):
    """Accept both the library's string states and enum states."""
    state = drone_obj.get_flight_state()
    name = getattr(state, "name", str(state)).split(".")[-1]
    return name.replace("_", "").replace(" ", "").lower()


def _begin_telemetry_logger(logger):
    """Start a TelemetryLogger once when the drone becomes airborne."""
    if logger is not None:
        return logger
    logger = TelemetryLogger(deferred_write=True)
    print("Telemetry logging in memory; it will be saved when the competition ends.")
    return logger


def _close_telemetry_logger(logger):
    """Close the in-memory logger and return it for saving at competition end."""
    if logger is None:
        return None
    logger.close()
    print(f"Telemetry buffered ({logger.sample_count} samples).")
    return logger


def start_flight(drone_obj, pilot_name):
    """
    This function contains the entire flight loop.
    It blocks the main program until the flight is over (land, practice, or crash).
    Returns (flight_stats, deferred_telemetry_logger).
    flight_stats is set only for a competition landing; otherwise None.
    Telemetry is held in memory until the competition is ended from the menu.
    """
    print(f"\n--- Starting Flight for {pilot_name} ---")
    print("CONTROLS: L1(Takeoff) | R1(Comp. Land) | R2(Prac. Land) | L2(Emergency)")

    current_state = STATE_IDLE
    flight_data_list = []
    start_time = 0
    airborne_seen = False
    ground_since = None
    controller_flight = False
    landing_already_complete = False
    logger = None

    try:
        while True:
            reported_state = flight_state_name(drone_obj)
            airborne = reported_state in {"takeoff", "takingoff", "flight", "flying", "landing"}
            grounded = reported_state in {"ready", "stop", "stopped", "landed"}

            # Emergency and practice buttons retain their original meaning.
            if drone_obj.l2_pressed():
                current_state = STATE_EMERGENCY
            elif current_state == STATE_FLYING and drone_obj.r2_pressed():
                current_state = STATE_PRACTICE_LAND
            elif current_state == STATE_FLYING and drone_obj.r1_pressed():
                current_state = STATE_LANDED
            elif current_state == STATE_IDLE:
                if airborne:
                    print("\n--- CONTROLLER TAKEOFF DETECTED ---")
                    current_state = STATE_FLYING
                    controller_flight = True
                    start_time = time.monotonic()
                    logger = _begin_telemetry_logger(logger)
                elif drone_obj.l1_pressed():
                    print("\n--- TAKEOFF ---")
                    drone_obj.takeoff()
                    current_state = STATE_FLYING
                    start_time = time.monotonic()
                    logger = _begin_telemetry_logger(logger)

            if current_state == STATE_FLYING:
                if airborne:
                    airborne_seen = True
                    ground_since = None
                elif grounded and airborne_seen:
                    if ground_since is None:
                        ground_since = time.monotonic()
                    elif time.monotonic() - ground_since >= 0.5:
                        print("\n--- LANDING DETECTED ---")
                        landing_already_complete = True
                        current_state = STATE_LANDED
                else:
                    # Unknown states are not evidence of a completed landing.
                    ground_since = None
                if reported_state in {"error", "accident"}:
                    current_state = STATE_EMERGENCY

            # --- Handle Flying State ---
            if current_state == STATE_FLYING:
                # Let the controller continue controlling a controller-started flight.
                if not controller_flight and reported_state != "landing":
                    # 2. Get joystick values for flight control
                    pitch = max(-60, min(60, drone_obj.get_left_joystick_y()))
                    roll = max(-60, min(60, drone_obj.get_left_joystick_x()))
                    throttle = max(-60, min(60, drone_obj.get_right_joystick_y()))
                    yaw_input = max(-60, min(60, drone_obj.get_right_joystick_x()))

                    # 3. Set drone movement
                    drone_obj.set_pitch(pitch)
                    drone_obj.set_roll(roll)
                    drone_obj.set_throttle(throttle)
                    drone_obj.set_yaw(yaw_input)
                    drone_obj.move()

                # 4. --- Data Collection (leaderboard stats + full telemetry CSV) ---
                relative_time = time.monotonic() - start_time

                data_point = {
                    "time": relative_time,
                    "alt": drone_obj.get_height("cm"),
                    "vx": drone_obj.get_flow_velocity_x(),
                    "vy": drone_obj.get_flow_velocity_y(),
                    "ax": drone_obj.get_accel_x(),
                    "ay": drone_obj.get_accel_y(),
                    "az": drone_obj.get_accel_z(),
                    "yaw": drone_obj.get_angle_z()
                }
                flight_data_list.append(data_point)

                if logger is not None:
                    logger.log_sample(sample_from_drone(drone_obj))

                # 5. Output live data
                telem_count = logger.sample_count if logger is not None else 0
                print(f"FLYING | Pilot: {pilot_name} | Time: {relative_time:5.1f}s | "
                      f"Points: {len(flight_data_list)} | CSV: {telem_count}", end='\r')

            elif current_state == STATE_IDLE:
                print(f"IDLE: {pilot_name}, take off with the controller or press L1...", end='\r')

            # --- Handle Flight End States ---
            if current_state == STATE_LANDED:
                print("\n--- COMPETITION LANDING ---")
                if not landing_already_complete:
                    drone_obj.land()
                end_time = time.monotonic()
                flight_time = end_time - start_time
                print(f"Flight complete. Total time: {flight_time:.2f}s")

                csv_path = _close_telemetry_logger(logger)
                logger = None

                stats = calculate_flight_stats(flight_data_list, flight_time, pilot_name)
                if not flight_data_list:
                    print("No telemetry collected; saving landing and flight time only.")
                print("--- FLIGHT STATS (to be saved) ---")
                print(f"  Pilot:        {stats['pilot_name']}")
                print(f"  Flight Time:  {stats['flight_time']:.2f}s")
                print(f"  Max Speed:    {stats['max_speed']:.2f} units")
                print(f"  Max Accel:    {stats['max_accel']:.2f} units")
                print(f"  Yaw Variance: {stats['yaw_variance']:.2f} deg^2")
                print("----------------------------------\n")
                return stats, csv_path

            elif current_state == STATE_PRACTICE_LAND:
                print("\n--- PRACTICE LANDING ---")
                drone_obj.land()
                csv_path = _close_telemetry_logger(logger)
                logger = None
                print("Practice flight complete. Leaderboard stats will not be saved.")
                return None, csv_path

            elif current_state == STATE_EMERGENCY:
                print("\n--- EMERGENCY STOP / CRASH DETECTED ---")
                drone_obj.emergency_stop()
                csv_path = _close_telemetry_logger(logger)
                logger = None
                print("Flight aborted. Leaderboard stats will not be saved.")
                return None, csv_path

            # Loop delay for ~20Hz data capture
            time.sleep(0.05)
    finally:
        # Crash / Ctrl+C: still flush whatever telemetry was written.
        if logger is not None:
            _close_telemetry_logger(logger)


# --- Main Program ---
def main():
    drone = Drone()
    flight_history = FlightHistory()  # This is our linked list
    pending_telemetry = []
    csv_files_to_upload = []

    try:
        print("Pairing drone...")
        drone.pair()
        print("Drone paired!")

        # --- Main Competition Menu Loop ---
        while True:
            print("\n--- Drone Racing Competition Menu ---")
            print("  1. Start New Flight")
            print("  2. End Competition and Save Results")
            choice = input("Enter your choice (1 or 2): ").strip()

            if choice == '1':
                pilot_name = input("Enter pilot's name: ").strip()
                if not pilot_name:
                    print("Pilot name cannot be empty.")
                    continue

                # This function will block until the flight is over
                flight_stats, telemetry_logger = start_flight(drone, pilot_name)

                if flight_stats:
                    flight_history.append(flight_stats)
                    print(f"Flight for {pilot_name} added to the competition results.")

                if telemetry_logger and telemetry_logger.sample_count:
                    pending_telemetry.append(telemetry_logger)

                # Show the updated leaderboard after every flight
                flight_history.print_leaderboard()

            elif choice == '2':
                print("Ending competition...")
                if flight_history.count:
                    results_path = flight_history.save_to_file()
                    if results_path:
                        csv_files_to_upload.append(results_path)
                else:
                    print("No completed competition flights to save.")
                for logger in pending_telemetry:
                    telemetry_path = logger.save()
                    if telemetry_path:
                        print(f"Saved {logger.sample_count} telemetry samples to {telemetry_path}")
                        csv_files_to_upload.append(telemetry_path)
                try:
                    uploaded_count = upload_csv_files_to_s3(csv_files_to_upload)
                    print(f"Uploaded {uploaded_count} CSV file(s) from this competition to S3.")
                except Exception as upload_error:
                    print(f"S3 upload failed; local CSV files are still saved: {upload_error}")
                break  # Exit the menu loop

            else:
                print("Invalid choice. Please enter 1 or 2.")

    except Exception as e:
        print(f"\nAn error occurred: {e}")

    finally:
        # This ensures the drone lands and disconnects safely
        print("\nClosing connection...")
        drone.land()
        drone.close()


    print("\nProgram finished.")
    # You can access the final data here if needed,
    # but it's already saved under testData/.


if __name__ == "__main__":
    main()

