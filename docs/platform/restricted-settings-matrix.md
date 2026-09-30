# Restricted-settings matrix: Play-installed, sideloaded and restored builds

Android platform baseline, android#57 (`T-AND-07`). Source of truth:
`app/src/main/kotlin/com/pennilogic/android/platform/settings/` (`SensitiveSettings.kt`,
`RestrictedSettingsMatrix.kt`, `InstallSourceProbe.kt`). The table block below is generated from
that code and `RestrictedSettingsMatrixDocTest` fails when it differs; `RestrictedSettingsMatrixTest`
asserts the platform facts stated here. Behaviour is described per API level of the QA matrix
(`T-QA-12`: 31, 33, 35, 36) plus 34, where restricted settings were unchanged.

## What the platform does

The cell values below hold for a device with AOSP defaults; the rules behind them are taken from the
platform sources cited in the Sources section, not from the pull request text.

- **API 31 (Android 12).** No restricted settings exist. Every special access, runtime permission
  and role is enabled through its normal settings page or prompt for every install source.
- **API 33 (Android 13).** Restricted settings appear. The lock is applied **at install time** and
  keyed on the **package source** the installer declared: `InstallPackageHelper.enableRestrictedSettings`
  sets the `ACCESS_RESTRICTED_SETTINGS` app-op to `errored` iff the install was marked
  `PACKAGE_SOURCE_LOCAL_FILE` or `PACKAGE_SOURCE_DOWNLOADED_FILE`. The installer's identity plays no
  part. The AOSP and Google package-installer apps mark file installs that way, so a browser
  download, mail attachment or file-manager install is locked in the common case; Google Play, a
  store using an install session with a store or unspecified source, and `adb install` are not. The
  locked settings are the **notification listener** and **accessibility services**: the toggle shows
  "Restricted setting" and the platform records the attempt.
- **API 34 (Android 14).** Same rule and set as API 33.
- **API 35 (Android 15).** The rule becomes **installer-trust based** (Enhanced Confirmation Mode,
  `EnhancedConfirmationService.isPackageEcmGuarded` in the Permission module): file-sourced installs
  are always guarded; any other package is exempt only if the device trusts installs from
  non-allowlisted installers (AOSP ships an empty `enhanced-confirmation-trusted-installer` list, so
  this is the default), or the installer is preinstalled, or the installer is allowlisted. On a device
  whose OEM or GMS configuration declares a trusted installer, `adb` (no installer) and non-allowlisted
  store sessions **are** guarded. The decision is **lazy**: the app-op stays unset until a restricted
  setting is first toggled, so an unset app-op means "not decided yet", not "unlocked". The set grows
  to device admin, display over other apps, usage access, the SMS runtime permission and the default
  SMS and phone roles (the Android 15 CDD list). The notification runtime permission
  (`POST_NOTIFICATIONS`) is never a restricted setting.
- **API 36 (Android 16).** Same rule and set as API 35. Separately, from 30 September 2026 certified
  devices in Brazil, Indonesia, Singapore and Thailand block installs of apps whose developer is not
  verified (global rollout 2027); that is an install-time check covered by
  [`sideloaded-qa-prerequisites.md`](sideloaded-qa-prerequisites.md), not a restricted setting.
- **Restored builds.** The platform *attempts* to restore listener approvals (`NotificationBackupHelper`),
  enabled accessibility services (`SettingsBackupAgent`) and runtime grants (`PermissionBackupHelper`),
  but none is guaranteed to arrive, so a restored build treats every grant as one to re-check and ask
  for again. A build reinstalled by Play during restore is a Play install for locking purposes; a
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
from API 33) and maps it with `InstallSourceClassifier`. A declared file source is the platform's own
criterion and always wins. The `legacy_sideload` value is the app's **proxy**, not a platform rule:
it is chosen when the installing or initiating package is a package-installer app (the AOSP, Google
or a vendor build of it), or when the installing package is gone while the initiating package is not
the shell (an installer that was uninstalled). Any other installing package that used an install
session is a store. A proxy can classify as locked an install the platform never locked (a vendor
installer that left the package source unset), and on API 35+ a device with a trusted-installer list
can lock an `adb` or store install the matrix shows as unlocked; the app's posture is safe either way,
because the classification only chooses the destination of the recovery action and the platform
itself shows the restricted-setting dialog if the lock applies. When the platform reports nothing
(API 26-29) the app sends the user to the normal settings page. The app never reads or changes the
platform's `ACCESS_RESTRICTED_SETTINGS` state and uses no hidden API for it.
## Availability values

- `user_enableable` — enable through the normal settings page or prompt.
- `restricted_setting_locked` — the platform shows "Restricted setting"; the user unlocks the app
  once through App info, then enables the setting normally. The app cannot unlock it.
- `regrant_required` — enableable, but the restore is not guaranteed to have carried the grant over; the app re-checks and asks again.
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
reason `restricted_setting_locked`. The copy stays the canonical device-cause copy of the taxonomy;
what the reason changes is only the destination of the single `review_access` action — the app's App
info page, where the platform offers the unlock, instead of the setting's own page. Every other
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

What is **not** a recovery path, and what the app never does: changing the restricted-settings
app-op from a shell, calling hidden APIs or reflection, asking the user to disable Play Protect,
lowering `targetSdk`, or shipping a build variant that bypasses the lock. This document deliberately
does not spell out the shell command, and `RestrictedSettingsMatrixDocTest` fails if it or the QA
prerequisites document ever does. Production policy is identical for
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
| `regrant_required` | Not guaranteed to be carried over by restore or transfer; the app re-checks and asks again through the normal path. | `grant_again` |
| `restricted_and_regrant_required` | Not guaranteed to be carried over by the transfer and locked by restricted settings; unlock via App info, then grant again. | `allow_restricted_settings` |
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

- AOSP `android-13.0.0_r3`, `services/core/java/com/android/server/pm/InstallPackageHelper.java`,
  `enableRestrictedSettings`: the API 33/34 lock is applied at install time iff the package source
  is `PACKAGE_SOURCE_LOCAL_FILE` or `PACKAGE_SOURCE_DOWNLOADED_FILE`.
- AOSP `packages/modules/Permission`, `android-15.0.0_r1` and `android-16.0.0_r1`,
  `EnhancedConfirmationService.isPackageEcmGuarded`: file sources always guarded; otherwise exempt
  only via the trusted-installer configuration or a preinstalled/allowlisted installer; lazy decision.
  `frameworks/base/data/etc/enhanced-confirmation.xml` is empty on both tags.
- Android 15 Compatibility Definition Document, restricted-settings section (the protected set:
  accessibility, notification listener, device admin, display over other apps, usage access, the SMS
  runtime permission, the dialer and SMS roles; App info unlock mandated since Android 13).
- AOSP backup: `SystemBackupAgent` → `NotificationBackupHelper` (listener approvals),
  `SettingsBackupAgent` (`enabled_accessibility_services`), `PermissionBackupHelper` (runtime grants):
  restore is attempted, not guaranteed.
- `PackageManager.getInstallSourceInfo`, `InstallSourceInfo.getPackageSource`,
  `PackageInstaller.Session.setPackageSource` (API 33), `NotificationManagerCompat.getEnabledListenerPackages`.