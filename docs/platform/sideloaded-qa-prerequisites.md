# Sideloaded QA prerequisites (developer verification, install path, restricted settings)

Android platform baseline, android#57 (`T-AND-07`). Executable form:
`python scripts/qa_sideload_prerequisites.py` (read-only; unit-tested in
`scripts/tests/test_qa_sideload_prerequisites.py`). This page states what a QA device and a
tester need before a sideloaded PenniLogic build can be installed and exercised, and which paths
are supported. Nothing here weakens production policy: the app behaves identically whatever the
install path, and the recovery paths are the platform's own.

## 1. What changes in 2026

Android developer verification requires apps to be registered to a developer with a verified
identity before users can install them on certified devices. The user-facing protections start on
**30 September 2026** for users in Brazil, Indonesia, Singapore and Thailand installing through the
participating stores (Google Play, HONOR App Market, OPPO App Market, Galaxy Store, Palm Store,
V-Appstore, GetApps) on certified devices running Android 7+, and expand **globally in 2027** to
all apps on certified devices. From August 2026 the developer APIs, the limited-distribution account
type and the power-user **advanced flow** are available. An unregistered app installs only through
`adb` or, for a tester who completes the one-time advanced flow (developer mode on, a check that
nobody is coaching them, an explicit risk acknowledgement), the "Install anyway" path.

Consequences for PenniLogic QA:

| Build | Package name | Registration | Supported install paths for QA |
| --- | --- | --- | --- |
| `debug` | `com.pennilogic.android.debug` | **Never registered.** It is signed with the per-machine debug keystore that AGP generates in `~/.android/`; registering that key would tie the package to one workstation and is not done. | `adb install -r` (exempt from verification and from restricted settings); the tester's advanced flow on their own device. |
| `release` | `com.pennilogic.android` | Registered automatically through Play Console once the release signing key is provisioned (a release ticket; no key exists in this repository). Google Play registers eligible apps for verified Play developers; a manual claim uploads an APK signed with the release key. | Play internal testing / internal app sharing (a Play install: no verification prompt, no restricted setting); `adb install -r` for a locally built unsigned or self-signed release. |

Neither package name is registered by this ticket, no Android Developer Console account is created,
and no signing material is added.

## 2. Device prerequisites

| Prerequisite | Why | Check |
| --- | --- | --- |
| API level 26 or higher; QA matrix levels are 31, 33, 35 and 36 | `minSdk` 26 (provisional); behaviour differences are recorded per QA level in [`android-behavior-matrix.md`](android-behavior-matrix.md) | `adb shell getprop ro.build.version.sdk` |
| Developer options and USB debugging on | Needed for `adb install` and for the power-user advanced flow | `adb shell settings get global development_settings_enabled` → `1`; the device appears in `adb devices -l` as `device` |
| Play Protect "Verify apps over USB" state known | With it on, the first `adb install` may show a one-time Play Protect prompt; QA answers it. **Never disable Play Protect** to make a build install. | `adb shell settings get global verifier_verify_adb_installs` (`0` = off) |
| Know whether Google Play is present | Developer verification and Play-install paths apply only to certified devices with Play; the `google_apis` API 31 image has none, the `google_apis_playstore` API 36 image has it | `adb shell pm list packages com.android.vending` |
| Exactly one target, or `--serial` | The script refuses to guess | `adb devices -l` |

## 3. Install paths and what they imply

| Path | Restricted settings (API 33+) | Developer verification | When to use |
| --- | --- | --- | --- |
| `adb install -r app/build/outputs/apk/debug/app-debug.apk` (or `./gradlew installDebug`) | Not applied on an AOSP-default device: no file source is declared (the platform records `com.android.shell` as the initiating package, no installing package and package source `OTHER`); on API 35+ a device whose OEM/GMS configuration declares a trusted-installer list may still guard it (see [`restricted-settings-matrix.md`](restricted-settings-matrix.md)) | Exempt | Default for every QA device and emulator |
| Play internal testing / internal app sharing | Not applied (Play install) | Registered through Play | Release-candidate QA on certified devices, once release signing exists |
| Copying the APK to the device and opening it from a file manager, browser download or mail | **Applied** when the installer marks the install as file-sourced (the AOSP/Google package installer does): notification access, accessibility and (API 35+) device admin, overlay, usage access, SMS permission and default SMS/phone roles are locked until the user allows restricted settings for the app | Blocked in enforced regions unless the tester completes the advanced flow | Only to reproduce the sideloaded-user experience; see the recovery path below |

The full source × setting × API table is in
[`restricted-settings-matrix.md`](restricted-settings-matrix.md).

## 4. Supported recovery path when a sideloaded build is locked

1. Try to enable the setting once (for example notification access), so the platform shows the
   "Restricted setting" dialog and records the attempt.
2. Settings > Apps > PenniLogic > three-dot menu > **Allow restricted settings**; authenticate.
3. Enable the setting through its normal page.
4. Alternative that avoids the lock entirely: `adb install -r` the same APK (data is kept).

Not supported, and not something the app does: changing the restricted-settings app-op from a
shell, hidden APIs or reflection, disabling Play Protect, lowering `targetSdk`, or a build variant
that bypasses the lock. This document does not spell out the shell command on purpose, and
`RestrictedSettingsMatrixDocTest` fails if it ever appears here. The script reports the current
`ACCESS_RESTRICTED_SETTINGS` mode for diagnosis and never changes it; read it as follows: `allow`
means the user unlocked restricted settings for the app; `errored`, `deny` or `ignore` means the
platform locked them; **unset means "not decided yet" on API 35 and 36** (the platform decides from
the install source when a restricted setting is first toggled) and "not locked" on API 33 and 34
(where the platform sets the app-op at install time for file-sourced installs).

## 5. Running the check

```text
python scripts/qa_sideload_prerequisites.py                  # single connected device
python scripts/qa_sideload_prerequisites.py --serial emulator-5554 --json
python scripts/qa_sideload_prerequisites.py --package com.pennilogic.android --strict
```

Exit code 0 means every blocking prerequisite is met; advisories (developer options off, Play
Protect verify on, a locked install source, developer verification applicable, a locked app-op) are
listed and, with `--strict`, also fail the run. Exit code 1 is a blocking finding: no `adb`, no or
ambiguous device, or an API level below the minimum. Exit code 2 is a usage error, for example a
`--package` value that is not an Android package name; the value is validated before anything reaches
the device shell. The script runs only `adb` queries (`getprop`, `settings get`, `pm list packages`,
`dumpsys package`, `appops get`), matches the exact `package:<id>` line (so `com.pennilogic.android`
is not reported as installed when only `com.pennilogic.android.debug` is present) and prints
`physical-device` instead of a hardware serial.

## 6. Evidence for this baseline

- `scripts/tests/test_qa_sideload_prerequisites.py` (CI): parsing, install-source classification
  mirroring the app's `InstallSourceClassifier`, blocking versus advisory findings, exit codes.
- Local run against the API 36 emulator recorded in the pull request; no device grant of a
  notification listener is claimed, because the listener is a capture feature ticket.
- Evidence pasted into a pull request must not contain a hardware serial or any other device
  identifier: the script prints emulator serials only and reports a physical device as
  `physical-device`; do not add the serial back by hand.

## Sources

- Android developer verification: overview, FAQ and timeline
  (developer.android.com/developer-verification; Android Developers Blog, March 2026).
- Android 13 restricted settings and the Android 15 CDD restricted-settings scope.
- `PackageManager.getInstallSourceInfo`, `PackageInstaller.PACKAGE_SOURCE_*`.
