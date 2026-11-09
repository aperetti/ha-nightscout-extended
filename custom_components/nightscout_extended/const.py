"""Constants for Nightscout Extended."""

DOMAIN = "nightscout_extended"

CONF_USE_API_SECRET = "use_api_secret"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_SCAN_INTERVAL = 60  # seconds; CGM readings arrive every 5 minutes (1 for some sensors)
MIN_SCAN_INTERVAL = 30

UNIT_MGDL = "mg/dL"
UNIT_MMOL = "mmol/L"
