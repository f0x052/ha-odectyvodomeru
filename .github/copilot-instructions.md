# Copilot instructions

Use this file as the repository's technical context and manual-verification
reference. Keep the root `README.md` and `info.md` focused on the project
introduction, functionality, installation, and usage.

- Follow the existing custom integration structure in
  `custom_components/odectyvodomeru` and keep English/Czech translations complete.
- Preserve account-scoped device identifiers, entity unique IDs, saved selections,
  user-assigned names, and existing consumption statistics when changing behavior.
- Keep cumulative reading semantics intact: missing data is not zero, corrections
  are not automatic resets, and no historical readings are fabricated.
- Use synthetic API responses in tests. Never commit credentials, tokens, private
  API captures, or personal account/location data.
- Keep shared screenshot assets in `images/` and anonymize real identifiers.
  Use absolute raw GitHub image URLs in both Markdown pages so HACS can render
  them. Pin URLs to the asset commit and update them when screenshots change.
  Keep `README.md` and `info.md` aligned when user-facing functionality changes.
- Use the development commands below for relevant changes and keep user-facing
  documentation aligned with the implementation.

## Project context

Custom integration for [odectyvodomeru.cz](https://odectyvodomeru.cz), targeting
Home Assistant **2026.10.4**.

## Current milestone: meter readings and hourly polling

Version 0.5.1 provides UI setup, username/password validation, duplicate-account
protection, startup authentication, reauthentication, meter selection, device
registration, cumulative reading sensors, location details, and hourly polling. Login verifies both
`POST /api/login_check` and the authenticated `GET /api/user/profile` endpoint
at `https://api.goliash.cz`.

After login, setup discovers the account's active cold-water, hot-water, and heat
meters and asks you to select at least one. None are selected automatically.
Labels include the localized meter type, manufacturer, serial number, and portal
meter ID. Removed and unsupported meters are excluded.

Each selected meter is registered as a Home Assistant device with its type,
manufacturer, and serial number. Devices use stable account-scoped portal meter
IDs, so reloads and restarts do not create duplicates or overwrite user-assigned
names. Saving Configure reloads the integration, adds selected devices, and removes
deselected devices. Removing and later reselecting a device creates a new registry
entry and does not restore its previous customizations.

The service entry is named `Odecty vodomeru (your login email)`. Existing entries
using the original default name are updated automatically after successful login
on reload or restart; custom service names are preserved. Reauthentication with
a changed login email updates the default title too. The integration heading,
devices, entity IDs, and statistics are unchanged.

Each selected device has a **Meter reading** sensor (water in m³, heat in MWh)
and diagnostic **Last reading** and **Meter information** entities. Values come from `lastState.value`
and `lastState.lastMeasurement`, not the period-consumption graph endpoint.
One shared coordinator fetches the household snapshot hourly for all meters.
The source readings may only change daily; hourly polling does not create
hourly source measurements.

Open a device's **Meter information** entity, then choose **⋮ > Details** in the
top-right corner. Its **Attributes** section shows the full building
address, flat number, floor number, building/household names and description,
building/household portal IDs, meter ID, meter type, unit, and RSSI, when supplied
by the portal. The entity's state is the meter's serial number; manufacturer and
meter type also remain in Home Assistant's device-info card. Home Assistant has
no address or flat-number fields in that card, so location is shown on this
diagnostic entity instead. The initial entity dialog shows only the serial number,
history, and activity; custom attributes are in **Details**, not on that first
screen. No areas or device names are changed automatically.

Details refresh with the existing hourly snapshot, without extra API calls.
Missing optional fields are omitted, not invented; ground floor (`0`) and
negative basement floors are preserved. Missing/invalid readings do not hide
otherwise valid device information. Account permissions, raw API payloads, and
credentials are not exposed as entity attributes.

On an expired access token, polling logs in again once with the stored
credentials and retries. Rejected credentials or restricted access trigger
reauthentication; outages mark sensors unavailable until recovery. Missing,
malformed, and out-of-order readings make only the affected meter unavailable,
never zero. A valid zero is still reported. Credentials/tokens are never logged.

This is an integration, not an add-on. It is not installed from the add-on store;
HACS metadata is provided in the root `hacs.json`, alongside `info.md`.
The primary repository and local `origin` are
`https://github.com/f0x052/ha-odectyvodomeru`. Use that public GitHub URL for HACS
custom-repository installation, documentation, and issue links.

### Integration icon

Version 0.5.1 bundles explicit light and dark 256px and 512px transparent icons
in `brand/`, supported
natively by Home Assistant 2026.3 and newer. They use the Renova symbol displayed
by the portal. The website does not currently serve a usable favicon, so the
symbol was extracted from its [full logo](https://odectyvodomeru.cz/img/renova/full-logo.png)
and the white background removed. Home Assistant uses the square icon as the logo
fallback. Explicit `dark_icon.png` and `dark_icon@2x.png` assets avoid depending
on the light-icon fallback for dark-mode requests.

The portal's brand artwork belongs to its respective owner, is not covered by
this repository's MIT license, and is used only to identify the service; this
integration is unofficial. After upgrading, restart Home Assistant and refresh
the browser if the previous placeholder is cached.

## Manual installation

1. Make a Home Assistant backup.
2. Locate the Home Assistant configuration directory: the directory containing
   `configuration.yaml`. On Home Assistant OS this is usually `/config` in your
   file editor or SSH environment; with Container, use the host directory mounted
   at `/config`.
3. Create `custom_components` inside that directory if it does not exist.
4. Copy the entire repository directory `custom_components/odectyvodomeru` into
   it. The resulting layout must be:

   ```text
   <HA configuration directory>/
     configuration.yaml
     custom_components/
       odectyvodomeru/
         __init__.py
         api.py
         brand/
           icon.png
           icon@2x.png
           dark_icon.png
           dark_icon@2x.png
         config_flow.py
         const.py
         coordinator.py
         manifest.json
         sensor.py
         strings.json
         translations/
           cs.json
           en.json
   ```

5. Restart Home Assistant, not just reload YAML.
6. Open **Settings > Devices & services > Add integration**, search for
   **Odecty vodomeru** (or **Odečty vodoměrů** in Czech), and enter the same
   credentials you use on the website, then choose the meters you want to use.
   Do not add anything to `configuration.yaml`.

If the integration is missing from the list, hard-refresh the browser after the
restart and check **Settings > System > Logs** for `odectyvodomeru`.

## Verification on your server

### Upgrading your existing installation

1. Copy the updated `custom_components/odectyvodomeru` folder over the installed
   folder and restart Home Assistant. Do not remove your existing integration.
   Confirm the integration page shows **0.5.1**, not an older installed version.
   Under **Services**, the entry should include your login email unless you
   previously gave it a custom name.
2. Open **Settings > Devices & services > Odecty vodomeru** and choose
   **Configure** (the gear icon or the entry's menu, depending on the frontend).
3. If you already saved a selection, expect those devices and their sensors to
   appear automatically after restart. A login-only entry starts with no devices.
   Configure lists your cold-water, hot-water, and heat meters with recognizable
   serial numbers.
4. Submit without selecting anything. Expect "Select at least one meter."
5. Select a subset and submit. Expect only those meters on the integration's
   device list. Open a device and check its manufacturer, type, and serial number.
6. Change the selection and submit. Deselected devices should disappear and newly
   selected devices should appear without a manual restart. Reopen Configure;
   the latest selection should be checked.
7. Cancel a selection change and reopen Configure; the previously saved selection
   must remain unchanged.
8. Rename a device, then reload the integration and restart Home Assistant. Its
   custom name and device identity should remain, with no duplicate devices.
9. Open each selected device and compare **Meter reading** and **Last reading**
   with the portal. Home Assistant may display heat in a converted unit; check
   the sensor's unit setting if you prefer MWh.
10. Wait for the next hourly poll, or use **Developer tools > Actions >
    Home Assistant: Update entity** (`homeassistant.update_entity`) on one reading
    sensor to request a shared refresh. Do not repeatedly reload just to poll.
11. Check **Developer tools > States**: readings should have `state_class: total`,
    `device_class: water` or `energy`, and the appropriate unit. After statistics
    have been collected, check **Developer tools > Statistics** for unit issues.
12. Open **Meter information** under the device's diagnostic entities and choose
    **⋮ > Details**. Under **Attributes**, confirm `address`, `flat_number`, and
    `floor_number` match the portal. They are also
    visible as attributes in **Developer tools > States**. Reload and verify
    existing reading entities and their history are unchanged.

Expect **three sensor entities per selected meter**. A selected meter absent from
a successful portal response keeps its existing device and emits a warning;
review Configure to remove it deliberately. Failed polling never deletes devices.

If discovery fails, Configure shows an error without changing saved choices.
Submit the form again to retry after resolving connectivity or portal access
issues. Authentication failures start Home Assistant's reauthentication flow;
complete it, then reopen Configure. Reauthentication preserves your selection.
Meters no longer returned by the portal are not checked or offered in the new
list; saving replaces the old selection with the currently available choices.

### New installation

1. Before successful setup, try your username with an intentionally incorrect
   password **once**. Expect a credential error with no integration entry created.
   Avoid repeated attempts because the service may rate-limit or lock accounts.
2. Submit the correct credentials. Expect a meter-selection step before any entry
   is created. Select at least one meter and submit to finish setup. Canceling this
   step must not create an entry. **Only selected devices and three sensors per
   meter are expected.**
3. Add the integration again with the same account. Expect an "already
   configured" message, not another entry.
4. Reload the existing entry from its three-dot menu. Expect it to load again
   successfully without duplicate devices; this authenticates and discovers the
   selected meters again.
5. Restart Home Assistant. Expect the entry to load using the stored credentials
   without asking you to sign in again.
6. Check **Settings > System > Logs** for integration errors. Home Assistant's
   standard warning about using a custom integration is expected.

If startup authentication is rejected, Home Assistant asks you to reauthenticate.
Use the same account; logging into a different account is rejected. Temporary
connection errors cause setup to retry instead of being reported as a bad password.

When reporting a problem, include the Home Assistant version, which step failed,
and the error text. Remove credentials, tokens, names, email addresses, and
addresses from any logs or screenshots. Do not upload a fresh unredacted HAR.

## Credentials and network access

- Home Assistant stores the username and password in its managed config-entry
  storage. It is **not encrypted by this integration**; protect the configuration
  directory and backups.
- Access tokens stay in memory and are not saved to the entry. No API responses
  or credentials are logged by this integration.
- Device location attributes may contain personal information. They are visible
  to users with entity access and can be recorded by Recorder and included in
  backups; redact them when sharing screenshots or state dumps.
- Requests use HTTPS with certificate validation and a 20-second timeout.
- Outbound access to `api.goliash.cz` is required. Polling is hourly while the
  integration is loaded, in addition to setup, reload, and Configure requests.
- Meter discovery uses `/api/structure/user-buildings` followed by
  `/api/structure/sub-structures/{id}`. No measurement-history requests are needed
  to populate the selector.
- The portal API is undocumented and may change.

## Daily, monthly, and yearly consumption graphs

The cumulative sensors use `state_class: total`, enabling Home Assistant's
long-term **sum** statistics. A newer lower value is treated as a correction or
backflow, not an automatic reset that would falsely add the entire register.
Replacement meters should have different portal IDs and separate statistics.
No `last_reset` is fabricated.

With Recorder enabled (the default), long-term statistics survive ordinary state
history purges. Do not exclude these sensors from Recorder. No backfill is
performed: the first observed register is a baseline, not historical consumption.
Statistics may take an hour to appear and require later readings before usage
appears. Home Assistant records when it observes a reading, not the source
timestamp; delayed daily readings and outages can shift usage into a later day.
These are observed consumption graphs, not exact billing-calendar reports.

Add a **Manual** dashboard card and replace the example entity ID with the actual
**Meter reading** entity ID from your device page. Use separate cards for water
and heat rather than combining m³ and MWh on one scale. The `change` statistic
shows consumption within each bucket instead of the cumulative meter register:

```yaml
type: vertical-stack
cards:
  - type: statistics-graph
    title: Daily water consumption
    chart_type: bar
    period: day
    days_to_show: 30
    stat_types: [change]
    entities: [sensor.replace_with_your_water_meter_reading]
  - type: statistics-graph
    title: Monthly water consumption
    chart_type: bar
    period: month
    days_to_show: 365
    stat_types: [change]
    entities: [sensor.replace_with_your_water_meter_reading]
  - type: statistics-graph
    title: Yearly water consumption
    chart_type: bar
    period: year
    days_to_show: 1825
    stat_types: [change]
    entities: [sensor.replace_with_your_water_meter_reading]
```

Duplicate the stack for each other water meter and the heat meter, changing the
entity IDs and titles. See the [Statistics graph card documentation](https://www.home-assistant.io/dashboards/statistics-graph/).

## Development

Use Python **3.14.2 or newer** for the pinned Home Assistant version:

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

GitHub Actions runs these checks in the `CI` workflow on pull requests targeting
`main` and on pushes to `main`. The `Tests and lint` job uses Python 3.14 and
installs `requirements-dev.txt` without substituting dependency versions.
Keep its job name stable: the `Protect main` ruleset requires this check before
merging, including for the repository owner. Review bypass does not bypass CI.
Actions are pinned to full commit SHAs and the workflow token is read-only.

The development dependencies target Home Assistant 2026.10.4. The initial local
checks used 2026.10.0b4 because the available package mirror did not contain the
target release and public package downloads were unavailable. Login and restart
persistence have since been verified manually on 2026.10.4. Meter discovery and
selection through Configure have also been verified on that server.
Device registration in version 0.3.0 has also been verified on that server.
Sensor values in version 0.4.0 have also been verified on that server.
The bundled icon in version 0.4.1 has also been verified on that server.
The service title in version 0.4.2 has also been verified on that server.
The device details in version 0.5.0 still need server verification.

Tests use synthetic API responses. Real accounts, credentials, and captured HAR
files must not be committed.
