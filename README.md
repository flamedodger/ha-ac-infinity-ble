# AC Infinity Bluetooth for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![Validate](https://github.com/viss/ha-ac-infinity-ble/actions/workflows/validate.yml/badge.svg)](https://github.com/viss/ha-ac-infinity-ble/actions/workflows/validate.yml)

=== /!\ warning /!\ ===

I got sick of not being able to control my huge 8" inline duct fan via HA - the old, now abandoned bluetooth repo worked for a bit, then aci changed stuff and it broke again. I was granted access to daybreak and since its positioned as a 'blueteam llm' I figured - what the hell, lets reverse some android apps and see what happens! My first go at it was this - lets reverse the ac infinity android app, tear out all the bluetooth comms protocol data, and see if we can monkey island style rub that all over the old borked HA integration and see if we can tune it up. 

well.. it worked. It took 2 days, and 8 versions, and there was a lot of me putting a brick in a gym sock and bashing daybreak in the face with it til it got stuff right, but the result is a functional HA integration that I am now using! My controller is a 'controller 69 with bluetooth', so anyone else with that controller should be able to take advantage of this. I havent tried plugging other stuff in (i have a humidifier too and some lights), but it seems like a good start

if youre vehemently against putting llm code on your HA installation, thats totally cool -please feel free to mine this codebase to tear out the protocol data so you can insert it into the broken/abandoned repo by hand!

what follows is text out of daybreak describing the process
-----


Local Bluetooth control for AC Infinity Controller 67/69-family devices. This
unofficial community fork repairs the abandoned
[`hunterjm/ac-infinity-hacs`](https://github.com/hunterjm/ac-infinity-hacs)
integration and vendors a repaired copy of
[`hunterjm/ac-infinity-ble`](https://github.com/hunterjm/ac-infinity-ble), so
Home Assistant installs the tested protocol code instead of the old
`ac-infinity-ble==0.4.3` package.

The implementation has been live-protocol tested with a Bluetooth Controller
69 (type 7, protocol version 3) controlling a fan on port 1.

## What is fixed

- Manual setup filters unrelated Bluetooth advertisements instead of raising
  the known `500 Internal Server Error`.
- Controller 69 units that BlueZ exposes as a name-only `ACI-E` advertisement
  are discovered through an exact-name fallback. Setup verifies the expected
  AC Infinity GATT characteristics before sending its read-only model query.
- Config-entry data is JSON serializable.
- Responses must match frame length, CRC, command, and sequence. Unsolicited
  `1e ff` telemetry cannot accidentally complete a pending command.
- The integration keeps one GATT connection and notification subscription,
  avoiding repeated scan/connect/disconnect delays and BlueZ `EOFError` churn.
- OFF uses the mode-only packet, preserving the stored ON speed.
- Fan state reports zero while OFF instead of exposing the stored preset as
  actual output.
- Current Home Assistant `TURN_ON`, `TURN_OFF`, and `SET_SPEED` features are
  declared explicitly.
- Temperature, humidity, VPD, fan state, and fan percentage update from live
  notifications and periodic validated model reads.
- Reload/shutdown is idempotent: repeated HA cleanup calls cannot fail before
  releasing the GATT connection. Timeout and EOF failures now drop stale clients
  and retry the complete connect/subscribe/command sequence.
- Connect, notification subscription, writes, and disconnect cleanup all have
  explicit deadlines. Initial setup cannot remain stuck at `Initializing` when
  BlueZ or a GATT operation stops responding.
- Integration reload has its own shutdown deadline. If a lower-level connect
  remains wedged while holding the connection lock, HA abandons that poisoned
  local client instead of leaving the reload spinner blocked indefinitely.
- Config-entry startup asks BlueZ to close any locally owned stale connection
  for the controller's exact Bluetooth address before opening a new session.
  This cleanup runs once per setup/reload and is itself time-bounded.

## Installation

### HACS custom repository (recommended)

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=viss&repository=ha-ac-infinity-ble&category=integration)

1. Open **HACS → ⋮ → Custom repositories**.
2. Add `https://github.com/viss/ha-ac-infinity-ble` as an **Integration**.
3. Find **AC Infinity**, choose **Download**, and restart Home Assistant.
4. Open **Settings → Devices & services → Add integration → AC Infinity**.

### Manual installation

1. Remove or disable the old `ac_infinity` custom integration files.
2. Copy `custom_components/ac_infinity` from this repository to
   `/config/custom_components/ac_infinity` on Home Assistant.
3. Restart Home Assistant.
4. Force-close the AC Infinity phone app. The controller accepts one Bluetooth
   client, so it will not advertise while the phone owns the connection.
5. Open **Settings → Devices & services → Add integration → AC Infinity** and
   choose the discovered controller.

Home Assistant 2025.2 or newer is required. A local Bluetooth adapter or an
ESPHome Bluetooth proxy with active connections enabled must be in range.

The integration intentionally holds the BLE connection for responsive
automations and live telemetry. Reload or disable the integration before using
the phone app.

## Automation examples

Turn the fan on at level 5 (50 percent):

```yaml
actions:
  - action: fan.turn_on
    target:
      entity_id: fan.controller_69_fan
    data:
      percentage: 50
```

Turn it off:

```yaml
actions:
  - action: fan.turn_off
    target:
      entity_id: fan.controller_69_fan
```

Set speed from a humidity automation:

```yaml
triggers:
  - trigger: numeric_state
    entity_id: sensor.controller_69_humidity
    above: 70
actions:
  - action: fan.set_percentage
    target:
      entity_id: fan.controller_69_fan
    data:
      percentage: 70
```

The controller has ten physical levels, exposed as 10-percent increments.

## Diagnostics

Enable debug logging when troubleshooting discovery or control:

```yaml
logger:
  logs:
    custom_components.ac_infinity: debug
    custom_components.ac_infinity.vendor.ac_infinity_ble: debug
```

If setup finds nothing, first verify that the phone app is force-closed and
that Home Assistant's Bluetooth integration can see connectable devices.

## Current scope

- The repaired build targets local BLE, not the AC Infinity cloud API.
- Controller 69 port 1 is the live-tested path.
- Multiple independently controlled ports on one Controller 69 need a future
  multi-entity design; the old integration also exposed only one fan.
- The included tests validate captured Controller 69 frames and state races,
  but final validation must run inside the user's Home Assistant Bluetooth
  environment.

New work and modifications in this repository are copyright Viss and released
under the root MIT license. Required notices for inherited upstream portions
are scoped separately in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md);
those notices do not claim authorship of this project's new work.

AC Infinity product names and trademarks belong to their respective owners.
This project is not affiliated with or endorsed by AC Infinity.
