# Changelog

Each version's section becomes its GitHub release notes, which Home Assistant shows in the update dialog.
Add a section here before tagging a release.

## [0.2.0] - 2026-11-09

### New
- **Treatments calendar**: every Nightscout treatment (meals, boluses, notes, temporary targets) as a calendar
  event, including ones logged for a future time. Use a calendar trigger to set an alarm before a planned meal; see
  the README for an example automation.
- **Last carbs** and **Last insulin** sensors, with the time, event type and notes as attributes.

### Note
- Log future meals as notes rather than with carbs filled in, so Nightscout doesn't count them in carbs on board
  before they're eaten.

## [0.1.0] - 2026-11-09

### New
- Glucose, glucose change, trend and last-reading sensors.
- Insulin on board and carbs on board (Nightscout `iob` and `cob` plugins).
- AR2 forecast at 15 and 30 minutes, with the full predicted curve as an attribute.
- Current basal (disabled by default).
- Setup from the UI with a read-only access token or the API secret, re-authentication, and an adjustable update
  interval.
- Follows the site's mg/dL or mmol/L setting.
