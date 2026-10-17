"""Synthetic official-HA wire fixtures; no personal state or credentials."""
from copy import deepcopy

PREFIX = "anker_solix_solarbank_max_ac"
CONFIG = {
    "enabled": True,
    "environment": "release_official_ha_unverified",
    "haUrl": "http://environment.invalid:8123",
    "simUrl": "http://environment.invalid:45678",
    "haToken": "qa-private-ha-value",
    "simUsername": "qa-private-user",
    "simPassword": "qa-private-password",
    "siteSuffixes": {"1": "qa_site_one", "2": "qa_site_two", "3": "qa_site_three"},
    "timeoutSeconds": 10,
}


def eid(func, domain="sensor", site="1"):
    return f"{domain}.{PREFIX}_{CONFIG['siteSuffixes'][site]}_{func}"


def states():
    result = []
    for site in ("1", "2", "3"):
        values = {"soc": ("70", "%"), "solar_power": ("1250", "W"),
                  "home_load": ("550", "W"), "battery_charging_power": ("400", "W"),
                  "battery_discharging_power": ("0", "W"), "grid_import_power": ("0", "W"),
                  "grid_export_power": ("300", "W"), "device_time": ("2026-10-17 12:15:00", None)}
        for func, (value, unit) in values.items():
            attrs = {} if unit is None else {"unit_of_measurement": unit}
            result.append({"entity_id": eid(func, site=site), "state": value,
                           "attributes": attrs, "last_updated": "2026-10-17T04:15:01+00:00"})
        for func, domain, value, attrs in (
            ("operating_mode_device_runs_by_command_in_third_party_controlled", "select", "third_party_control", {"options": ["third_party_control", "self_consumption"]}),
            ("grid_flow", "select", "charge", {"options": ["charge", "discharge"]}),
            ("power_control", "number", "400", {"unit_of_measurement": "W", "min": 0, "max": 5000, "step": .1}),
        ):
            result.append({"entity_id": eid(func, domain, site), "state": value, "attributes": attrs,
                           "last_updated": "2026-10-17T04:15:01+00:00"})
    return deepcopy(result)
