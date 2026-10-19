"""Entity metadata and state contracts, using real integration constructors."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from support import load_entity_platforms


SENSOR, NUMBER = load_entity_platforms()


class EntityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.info = {
            "device": {"id": "SL-TEST"},
            "mapping": {"battery_soc": "battery"},
        }
        self.coordinator = SimpleNamespace(
            api=SimpleNamespace(host="gateway.local", async_write=AsyncMock()),
            data={"data": {"battery": 100, "program_1_soc": 80}},
            data_available=True,
            async_request_refresh=AsyncMock(),
        )

    def test_battery_alias_has_percent_metadata_and_keeps_identity_and_numeric_state(self):
        sensor = SENSOR.ShevLoggerSensor(
            self.coordinator, self.info, {"key": "battery", "name": "Battery"}
        )
        self.assertEqual(sensor._attr_native_unit_of_measurement, "%")
        self.assertEqual(sensor._attr_device_class, "battery")
        self.assertEqual(sensor._attr_state_class, "measurement")
        self.assertEqual(sensor._attr_unique_id, "SL-TEST_battery")
        self.assertEqual(sensor.extra_state_attributes["key"], "battery")
        self.assertEqual(sensor._attr_name, "Battery")
        self.assertIsInstance(sensor.native_value, int)
        self.assertEqual(sensor.native_value, 100)
        self.coordinator.data["data"]["battery"] = 75
        self.assertEqual(sensor.native_value, 75)
        self.coordinator.data["data"]["battery"] = None
        self.assertIsNone(sensor.native_value)
        self.assertFalse(sensor.available)

    def test_unrelated_electrical_values_and_modifiers_are_preserved(self):
        for key, value, unit, device_class in (
            ("battery_current", 0.04, "A", "current"),
            ("battery_power", -21, "W", "power"),
            ("battery_voltage", 53.5, "V", "voltage"),
            ("battery_temperature", 22.5, "°C", "temperature"),
        ):
            with self.subTest(key=key):
                self.coordinator.data["data"][key] = value
                sensor = SENSOR.ShevLoggerSensor(
                    self.coordinator, self.info, {"key": key, "modifier": 2}
                )
                self.assertEqual(sensor._attr_native_unit_of_measurement, unit)
                self.assertEqual(sensor._attr_device_class, device_class)
                self.assertEqual(sensor.native_value, value * 2)

    async def test_soc_control_retains_number_semantics_and_original_write_key(self):
        number = NUMBER.ShevLoggerNumber(
            self.coordinator,
            self.info,
            {"key": "program_1_soc", "min": 0, "max": 100, "step": 1},
        )
        self.assertEqual(number._attr_native_unit_of_measurement, "%")
        self.assertIsNone(number._attr_device_class)
        self.assertEqual(number._attr_native_min_value, 0)
        self.assertEqual(number._attr_native_max_value, 100)
        self.assertEqual(number._attr_native_step, 1)
        self.assertEqual(number.native_value, 80)
        self.assertEqual(number._attr_unique_id, "SL-TEST_program_1_soc")
        await number.async_set_native_value(90)
        self.coordinator.api.async_write.assert_awaited_once_with("program_1_soc", 90)
        self.coordinator.async_request_refresh.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
