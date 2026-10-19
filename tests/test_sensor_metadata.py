import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "shevlogger"
    / "sensor_metadata.py"
)
SPEC = importlib.util.spec_from_file_location("shevlogger_sensor_metadata", MODULE_PATH)
METADATA = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(METADATA)


class SensorMetadataTests(unittest.TestCase):
    def metadata(self, description, mapping=None):
        return METADATA.resolve_sensor_metadata(description, mapping)

    def test_soc_alias_uses_profile_mapping_without_guessing_battery_names(self):
        for key in ("battery", "remaining", "raw_power_register"):
            description = {"key": key, "name": "Battery", "modifier": 1}
            original = description.copy()
            with self.subTest(key=key):
                self.assertEqual(
                    self.metadata(description, {"battery_soc": key}),
                    ("%", "battery", "measurement"),
                )
                self.assertEqual(description, original)
        self.assertEqual(
            self.metadata({"key": "battery", "name": "Battery"}),
            (None, None, None),
        )

    def test_mapping_does_not_change_unrelated_battery_values_or_settings(self):
        mapping = {"battery_soc": "battery"}
        for key, unit, device_class in (
            ("battery_current", "A", "current"),
            ("battery_power", "W", "power"),
            ("battery_voltage", "V", "voltage"),
            ("battery_temperature", "°C", "temperature"),
        ):
            with self.subTest(key=key):
                self.assertEqual(
                    self.metadata({"key": key}, mapping),
                    (unit, device_class, "measurement"),
                )
        self.assertEqual(
            self.metadata({"key": "output_load_percent"}, mapping),
            ("%", None, None),
        )

    def test_explicit_metadata_takes_precedence_over_mapping(self):
        self.assertEqual(
            self.metadata(
                {
                    "key": "raw",
                    "unit": "V",
                    "deviceClass": "voltage",
                    "stateClass": "measurement",
                },
                {"battery_soc": "raw"},
            ),
            ("V", "voltage", "measurement"),
        )

    def test_mapping_retains_existing_inference_when_canonical_role_is_unknown(self):
        self.assertEqual(
            self.metadata({"key": "pv_power"}, {"vendor_extension": "pv_power"}),
            ("W", "power", "measurement"),
        )

    def test_daily_and_lifetime_energy_are_eligible_for_energy_dashboard(self):
        for key in (
            "today_production",
            "today_battery_charge",
            "today_energy_import",
            "total_energy_export",
            "pv_energy_today",
            "load_energy_total",
        ):
            with self.subTest(key=key):
                self.assertEqual(
                    self.metadata({"key": key, "name": key}),
                    ("kWh", "energy", "total_increasing"),
                )

    def test_live_power_and_battery_soc_have_measurement_state_class(self):
        self.assertEqual(
            self.metadata({"key": "pv_power", "name": "PV Power"}),
            ("W", "power", "measurement"),
        )
        self.assertEqual(
            self.metadata({"key": "battery_soc", "name": "Battery SOC"}),
            ("%", "battery", "measurement"),
        )

    def test_percentage_settings_are_not_misclassified_as_battery(self):
        self.assertEqual(
            self.metadata({"key": "output_load_percent", "name": "Load Percentage"}),
            ("%", None, None),
        )
        self.assertEqual(
            self.metadata({"key": "power_factor", "name": "Power Factor"}),
            ("%", "power_factor", "measurement"),
        )

    def test_control_names_are_not_misclassified_as_energy_counters(self):
        self.assertEqual(
            self.metadata({"key": "energy_pattern", "name": "Energy Pattern"}),
            (None, None, None),
        )

    def test_profile_metadata_remains_authoritative(self):
        description = {
            "key": "net_energy",
            "unit": "Wh",
            "deviceClass": "energy",
            "stateClass": "total",
        }
        self.assertEqual(self.metadata(description), ("Wh", "energy", "total"))


if __name__ == "__main__":
    unittest.main()
