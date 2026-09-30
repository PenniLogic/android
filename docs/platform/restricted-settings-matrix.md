# Restricted-settings matrix: Play-installed, sideloaded and restored builds

Android platform baseline, android#57 (`T-AND-07`). Source of truth:
`app/src/main/kotlin/com/pennilogic/android/platform/settings/` (`SensitiveSettings.kt`,
`RestrictedSettingsMatrix.kt`, `InstallSourceProbe.kt`). The table block below is generated from
that code and `RestrictedSettingsMatrixDocTest` fails when it differs; `RestrictedSettingsMatrixTest`
asserts the platform facts stated here. Behaviour is described per API level of the QA matrix
(`T-QA-12`: 31, 33, 35, 36) plus 34, where restricted settings were unchanged.

## What the platform does

- **API 31 (Android 12).** No restricted settings exist. Every special access, runtime permission
  and role is enabled through its normal settings page or prompt for every install source.
- **API 33 (Android 13).** Restricted settings appear. A build installed from a user-acquired file
  (browser download, mail attachment, file manager through the intent-based package installer, or a
  `PackageInstaller` session that declared `PACKAGE_SOURCE_LOCAL_FILE` / `PACKAGE_SOURCE_DOWNLOADED_FILE`)
  cannot have its **notification listener** or **accessibility service** enabled: the toggle shows
  "Restricted setting" and the platform records the attempt. Builds installed by Google Play, by
  another store's install session or by `adb install` are not affected.
- **API 34 (Android 14).** Same set and sources as API 33.
- **API 35 (Android 15).** The Android 15 CDD extends the lock for the same sources to device admin,
  display over other apps, usage access, the SMS runtime permission and the default SMS and phone
  roles. The notification runtime permission (`POST_NOTIFICATIONS`) is never locked.
- **API 36 (Android 16).** Same set as API 35. Separately, from 30 September 2026 certified devices in
  Brazil, Indonesia, Singapore and Thailand block installs of apps whose developer is not verified
  (global rollout 2027); that is an install-time check covered by
  [`sideloaded-qa-prerequisites.md`](sideloaded-qa-prerequisites.md), not a restricted setting.
- **Restored builds.** Backup and device-to-device transfer never carry special-access grants over,
  and the app does not assume a runtime permission was restored either: after a restore the user
  grants again. A build reinstalled by Play during restore is a Play install for locking purposes; a
  non-Play package copied by a transfer records no store and is treated as a sideload.

## Install sources

| Identifier | How the platform reports it | Locked on API 33+ |
| --- | --- | --- |
| `play_store` | `installingPackageName == com.android.vending` (package source `STORE`) | no |
| `session_store` | another installing package with a store, other or unspecified package source | no |
| `adb` | no installing package; initiating package `com.android.shell` or none | no |
| `legacy_sideload` | installing or initiating package is the system package installer | yes |
| `session_file` | package source `LOCAL_FILE` or `DOWNLOADED_FILE`, whoever installed it | yes |
| `restored_by_play` | Play reinstall during device setup or restore | no (grants re-required) |
| `restored_by_transfer` | non-Play package copied by device-to-device transfer | yes (grants re-required) |

The app reads its own source with `PackageManager.getInstallSourceInfo` (API 30+; `packageSource`
from API 33) and maps it with `InstallSourceClassifier`: a declared file source always wins, the
system package installer under any vendor name is the intent-based sideload path, and any other
installing package that used an install session is a store, because the platform only locks
file-sourced and legacy installs. When the platform reports nothing (API 26-29) the app sends the
user to the normal settings page, where the platform itself shows the restricted-setting dialog if
the lock applies. The app never reads or changes the platform's `ACCESS_RESTRICTED_SETTINGS` state
and uses no hidden API for it.

## Availability values

- `user_enableable` — enable through the normal settings page or prompt.
- `restricted_setting_locked` — the platform shows "Restricted setting"; the user unlocks the app
  once through App info, then enables the setting normally. The app cannot unlock it.
- `regrant_required` — enableable, but the restore did not carry the grant over; grant again.
- `restricted_and_regrant_required` — both of the above.
- `not_applicable` — the setting does not exist as a user control on this API level.

## What a sideloaded or restored build cannot enable

On API 33 and 34 a `legacy_sideload`, `session_file` or `restored_by_transfer` build cannot enable
notification access or an accessibility service until the user unlocks restricted settings for it.
On API 35 and 36 the same builds additionally cannot enable device admin, display over other apps,
usage access, the SMS runtime permission or the default SMS and phone roles. PenniLogic uses
notification access, the SMS runtime permission and the notification runtime permission; the other
rows are recorded so QA can rule them out. For the two capture settings the app renders the
taxonomy condition `capture_blocked_by_setting` (state `permission_denied`, cause `device`) with the
reason `restricted_setting_locked`, whose recovery copy points at the unlock below; every other
setting renders the generic `device_permission_not_granted` condition.

## Supported QA recovery paths (production policy unchanged)

- `normal_settings_path` — the app's recovery action deep-links to the platform's own settings page
  or prompt (`Settings.ACTION_NOTIFICATION_LISTENER_DETAIL_SETTINGS`, the permission prompt, the
  role request); nothing else is needed.
- `allow_restricted_settings` — the platform's own unlock: try the setting once so the dialog is
  shown, then Settings > Apps > PenniLogic > three-dot menu > **Allow restricted settings**,
  authenticate, and enable the setting normally. For QA devices the alternative is to reinstall the
  same APK with `adb install -r` (data is kept), which the platform does not treat as a sideload, or
  to install through Play internal testing.
- `grant_again` — after a restore, grant the setting again through the normal path.
- `none` — nothing to do on this API level.

What is **not** a recovery path, and what the app never does: changing `ACCESS_RESTRICTED_SETTINGS`
with `appops`, calling hidden APIs or reflection, asking the user to disable Play Protect, lowering
`targetSdk`, or shipping a build variant that bypasses the lock. Production policy is identical for
every install source; only the copy of the recovery action differs.

<!-- matrix:begin (generated from RestrictedSettingsMatrix; do not edit by hand) -->
### `play_store`

Installed or updated by Google Play (installer com.android.vending, package source store). Restricted-settings lock on API 33+: no. Restored build: no.

| Setting | Used | 31 | 33 | 34 | 35 | 36 | Not granted on 36 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `notification_listener` | yes | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `capture_blocked_by_setting` / `listener_access_not_granted` |
| `accessibility_service` | no | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `device_permission_not_granted` |
| `device_admin` | no | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `device_permission_not_granted` |
| `display_over_other_apps` | no | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `device_permission_not_granted` |
| `usage_access` | no | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `device_permission_not_granted` |
| `sms_permission` | yes | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `capture_blocked_by_setting` / `capture_permission_not_granted` |
| `default_sms_role` | no | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `device_permission_not_granted` |
| `default_dialer_role` | no | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `device_permission_not_granted` |
| `post_notifications` | yes | `not_applicable` | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `device_permission_not_granted` |

### `session_store`

Installed by another store through a PackageInstaller session with a store, other or unspecified package source. Restricted-settings lock on API 33+: no. Restored build: no.

| Setting | Used | 31 | 33 | 34 | 35 | 36 | Not granted on 36 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `notification_listener` | yes | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `capture_blocked_by_setting` / `listener_access_not_granted` |
| `accessibility_service` | no | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `device_permission_not_granted` |
| `device_admin` | no | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `device_permission_not_granted` |
| `display_over_other_apps` | no | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `device_permission_not_granted` |
| `usage_access` | no | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `device_permission_not_granted` |
| `sms_permission` | yes | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `capture_blocked_by_setting` / `capture_permission_not_granted` |
| `default_sms_role` | no | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `device_permission_not_granted` |
| `default_dialer_role` | no | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `device_permission_not_granted` |
| `post_notifications` | yes | `not_applicable` | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `device_permission_not_granted` |

### `adb`

Installed with `adb install` or Android Studio: initiated by com.android.shell, no installing package recorded. Restricted-settings lock on API 33+: no. Restored build: no.

| Setting | Used | 31 | 33 | 34 | 35 | 36 | Not granted on 36 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `notification_listener` | yes | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `capture_blocked_by_setting` / `listener_access_not_granted` |
| `accessibility_service` | no | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `device_permission_not_granted` |
| `device_admin` | no | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `device_permission_not_granted` |
| `display_over_other_apps` | no | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `device_permission_not_granted` |
| `usage_access` | no | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `device_permission_not_granted` |
| `sms_permission` | yes | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `capture_blocked_by_setting` / `capture_permission_not_granted` |
| `default_sms_role` | no | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `device_permission_not_granted` |
| `default_dialer_role` | no | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `device_permission_not_granted` |
| `post_notifications` | yes | `not_applicable` | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `device_permission_not_granted` |

### `legacy_sideload`

Installed from a file through the intent-based package installer (browser download, mail attachment, file manager). Restricted-settings lock on API 33+: yes. Restored build: no.

| Setting | Used | 31 | 33 | 34 | 35 | 36 | Not granted on 36 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `notification_listener` | yes | `user_enableable` | `restricted_setting_locked` | `restricted_setting_locked` | `restricted_setting_locked` | `restricted_setting_locked` | `capture_blocked_by_setting` / `restricted_setting_locked` |
| `accessibility_service` | no | `user_enableable` | `restricted_setting_locked` | `restricted_setting_locked` | `restricted_setting_locked` | `restricted_setting_locked` | `device_permission_not_granted` |
| `device_admin` | no | `user_enableable` | `user_enableable` | `user_enableable` | `restricted_setting_locked` | `restricted_setting_locked` | `device_permission_not_granted` |
| `display_over_other_apps` | no | `user_enableable` | `user_enableable` | `user_enableable` | `restricted_setting_locked` | `restricted_setting_locked` | `device_permission_not_granted` |
| `usage_access` | no | `user_enableable` | `user_enableable` | `user_enableable` | `restricted_setting_locked` | `restricted_setting_locked` | `device_permission_not_granted` |
| `sms_permission` | yes | `user_enableable` | `user_enableable` | `user_enableable` | `restricted_setting_locked` | `restricted_setting_locked` | `capture_blocked_by_setting` / `restricted_setting_locked` |
| `default_sms_role` | no | `user_enableable` | `user_enableable` | `user_enableable` | `restricted_setting_locked` | `restricted_setting_locked` | `device_permission_not_granted` |
| `default_dialer_role` | no | `user_enableable` | `user_enableable` | `user_enableable` | `restricted_setting_locked` | `restricted_setting_locked` | `device_permission_not_granted` |
| `post_notifications` | yes | `not_applicable` | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `device_permission_not_granted` |

### `session_file`

Installed through a PackageInstaller session that declared PACKAGE_SOURCE_LOCAL_FILE or PACKAGE_SOURCE_DOWNLOADED_FILE. Restricted-settings lock on API 33+: yes. Restored build: no.

| Setting | Used | 31 | 33 | 34 | 35 | 36 | Not granted on 36 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `notification_listener` | yes | `user_enableable` | `restricted_setting_locked` | `restricted_setting_locked` | `restricted_setting_locked` | `restricted_setting_locked` | `capture_blocked_by_setting` / `restricted_setting_locked` |
| `accessibility_service` | no | `user_enableable` | `restricted_setting_locked` | `restricted_setting_locked` | `restricted_setting_locked` | `restricted_setting_locked` | `device_permission_not_granted` |
| `device_admin` | no | `user_enableable` | `user_enableable` | `user_enableable` | `restricted_setting_locked` | `restricted_setting_locked` | `device_permission_not_granted` |
| `display_over_other_apps` | no | `user_enableable` | `user_enableable` | `user_enableable` | `restricted_setting_locked` | `restricted_setting_locked` | `device_permission_not_granted` |
| `usage_access` | no | `user_enableable` | `user_enableable` | `user_enableable` | `restricted_setting_locked` | `restricted_setting_locked` | `device_permission_not_granted` |
| `sms_permission` | yes | `user_enableable` | `user_enableable` | `user_enableable` | `restricted_setting_locked` | `restricted_setting_locked` | `capture_blocked_by_setting` / `restricted_setting_locked` |
| `default_sms_role` | no | `user_enableable` | `user_enableable` | `user_enableable` | `restricted_setting_locked` | `restricted_setting_locked` | `device_permission_not_granted` |
| `default_dialer_role` | no | `user_enableable` | `user_enableable` | `user_enableable` | `restricted_setting_locked` | `restricted_setting_locked` | `device_permission_not_granted` |
| `post_notifications` | yes | `not_applicable` | `user_enableable` | `user_enableable` | `user_enableable` | `user_enableable` | `device_permission_not_granted` |

### `restored_by_play`

Reinstalled by Google Play during device setup or restore; app data follows the backup rules (this app excludes all of it). Restricted-settings lock on API 33+: no. Restored build: yes.

| Setting | Used | 31 | 33 | 34 | 35 | 36 | Not granted on 36 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `notification_listener` | yes | `regrant_required` | `regrant_required` | `regrant_required` | `regrant_required` | `regrant_required` | `capture_blocked_by_setting` / `listener_access_not_granted` |
| `accessibility_service` | no | `regrant_required` | `regrant_required` | `regrant_required` | `regrant_required` | `regrant_required` | `device_permission_not_granted` |
| `device_admin` | no | `regrant_required` | `regrant_required` | `regrant_required` | `regrant_required` | `regrant_required` | `device_permission_not_granted` |
| `display_over_other_apps` | no | `regrant_required` | `regrant_required` | `regrant_required` | `regrant_required` | `regrant_required` | `device_permission_not_granted` |
| `usage_access` | no | `regrant_required` | `regrant_required` | `regrant_required` | `regrant_required` | `regrant_required` | `device_permission_not_granted` |
| `sms_permission` | yes | `regrant_required` | `regrant_required` | `regrant_required` | `regrant_required` | `regrant_required` | `capture_blocked_by_setting` / `capture_permission_not_granted` |
| `default_sms_role` | no | `regrant_required` | `regrant_required` | `regrant_required` | `regrant_required` | `regrant_required` | `device_permission_not_granted` |
| `default_dialer_role` | no | `regrant_required` | `regrant_required` | `regrant_required` | `regrant_required` | `regrant_required` | `device_permission_not_granted` |
| `post_notifications` | yes | `not_applicable` | `regrant_required` | `regrant_required` | `regrant_required` | `regrant_required` | `device_permission_not_granted` |

### `restored_by_transfer`

Copied by a device-to-device transfer as a non-Play package; the platform records no store, so it is treated as a sideload. Restricted-settings lock on API 33+: yes. Restored build: yes.

| Setting | Used | 31 | 33 | 34 | 35 | 36 | Not granted on 36 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `notification_listener` | yes | `regrant_required` | `restricted_and_regrant_required` | `restricted_and_regrant_required` | `restricted_and_regrant_required` | `restricted_and_regrant_required` | `capture_blocked_by_setting` / `restricted_setting_locked` |
| `accessibility_service` | no | `regrant_required` | `restricted_and_regrant_required` | `restricted_and_regrant_required` | `restricted_and_regrant_required` | `restricted_and_regrant_required` | `device_permission_not_granted` |
| `device_admin` | no | `regrant_required` | `regrant_required` | `regrant_required` | `restricted_and_regrant_required` | `restricted_and_regrant_required` | `device_permission_not_granted` |
| `display_over_other_apps` | no | `regrant_required` | `regrant_required` | `regrant_required` | `restricted_and_regrant_required` | `restricted_and_regrant_required` | `device_permission_not_granted` |
| `usage_access` | no | `regrant_required` | `regrant_required` | `regrant_required` | `restricted_and_regrant_required` | `restricted_and_regrant_required` | `device_permission_not_granted` |
| `sms_permission` | yes | `regrant_required` | `regrant_required` | `regrant_required` | `restricted_and_regrant_required` | `restricted_and_regrant_required` | `capture_blocked_by_setting` / `restricted_setting_locked` |
| `default_sms_role` | no | `regrant_required` | `regrant_required` | `regrant_required` | `restricted_and_regrant_required` | `restricted_and_regrant_required` | `device_permission_not_granted` |
| `default_dialer_role` | no | `regrant_required` | `regrant_required` | `regrant_required` | `restricted_and_regrant_required` | `restricted_and_regrant_required` | `device_permission_not_granted` |
| `post_notifications` | yes | `not_applicable` | `regrant_required` | `regrant_required` | `regrant_required` | `regrant_required` | `device_permission_not_granted` |

### Recovery path per availability

| Availability | Meaning | QA recovery path |
| --- | --- | --- |
| `user_enableable` | Enableable through the normal settings path or prompt. | `normal_settings_path` |
| `restricted_setting_locked` | Locked by restricted settings; unlock once via App info > Allow restricted settings, then enable normally. | `allow_restricted_settings` |
| `regrant_required` | Not carried over by restore or transfer; grant again through the normal path. | `grant_again` |
| `restricted_and_regrant_required` | Not carried over by the transfer and locked by restricted settings; unlock via App info, then grant again. | `allow_restricted_settings` |
| `not_applicable` | No such user-controlled setting on this API level. | `none` |

#### `normal_settings_path`

1. Open the setting from the app's recovery action, which deep-links to the platform's own settings page or prompt.
2. Enable it. No further step exists; the platform does not gate this install source.

#### `allow_restricted_settings`

1. Try to enable the setting once so the platform shows its "Restricted setting" dialog and records the attempt.
2. Open Settings > Apps > PenniLogic > three-dot menu > "Allow restricted settings" and authenticate.
3. Return to the setting and enable it through the normal path.
4. Alternative for QA devices: reinstall the same APK with `adb install -r` (data is kept), which the platform does not treat as a sideload.

#### `grant_again`

1. Open the setting from the app's recovery action; the restored build shows the taxonomy state until it is granted.
2. Grant it again. The platform may have restored a runtime permission on its own; the app never assumes so and checks.

#### `none`

1. Nothing to do: the setting does not exist on this API level.
<!-- matrix:end -->

## Evidence

- `RestrictedSettingsMatrixTest` (JVM, both variants, CI): lock sets per API level and source,
  restored re-grant, recovery paths never weaken policy, taxonomy bindings.
- `InstallSourceClassifierTest`, `CaptureSettingsProbeTest`, `AndroidInstallSourceReaderTest`
  (JVM and Robolectric, CI): classification of the platform's install-source report and the reason
  a capture surface renders.
- `RestrictedSettingsMatrixDocTest` (JVM, CI): this document's table block equals the code.
- Manual QA on the API 31 and API 36 emulators follows [`sideloaded-qa-prerequisites.md`](sideloaded-qa-prerequisites.md);
  the notification listener itself is a capture feature ticket, so no on-device listener grant is
  claimed by this baseline.

## Sources

- Android 13 restricted settings and `PackageInstaller.Session.setPackageSource` (API 33).
- Android 15 Compatibility Definition Document, restricted-settings section (special permissions,
  roles and the SMS runtime permission; App info unlock mandated since Android 13).
- `PackageManager.getInstallSourceInfo`, `InstallSourceInfo.getPackageSource`,
  `NotificationManagerCompat.getEnabledListenerPackages`.
