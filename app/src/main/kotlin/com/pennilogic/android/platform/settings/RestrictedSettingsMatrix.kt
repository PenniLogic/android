package com.pennilogic.android.platform.settings

import com.pennilogic.android.platform.PlatformBaseline
import com.pennilogic.android.platform.state.CaptureHealthCondition
import com.pennilogic.android.platform.state.PlatformCapabilityReason

/**
 * The supported way for QA to reach a setting that a sideloaded or restored build cannot enable
 * directly. Every path is something the user does on the device or a different install method for
 * the same build; none changes app behaviour, production policy or the platform's own protections.
 */
enum class RecoveryPath(
    val id: String,
    val steps: List<String>,
) {
    NORMAL_SETTINGS_PATH(
        id = "normal_settings_path",
        steps =
            listOf(
                "Open the setting from the app's recovery action, which deep-links to the platform's own " +
                    "settings page or prompt.",
                "Enable it. No further step exists; the platform does not gate this install source.",
            ),
    ),
    ALLOW_RESTRICTED_SETTINGS(
        id = "allow_restricted_settings",
        steps =
            listOf(
                "Try to enable the setting once so the platform shows its \"Restricted setting\" dialog and " +
                    "records the attempt.",
                "Open Settings > Apps > PenniLogic > three-dot menu > \"Allow restricted settings\" and " +
                    "authenticate.",
                "Return to the setting and enable it through the normal path.",
                "Alternative for QA devices: reinstall the same APK with `adb install -r` (data is kept), " +
                    "which the platform does not treat as a sideload.",
            ),
    ),
    GRANT_AGAIN(
        id = "grant_again",
        steps =
            listOf(
                "Open the setting from the app's recovery action; the restored build shows the taxonomy " +
                    "state until it is granted.",
                "Grant it again. The platform may have restored a runtime permission on its own; the app " +
                    "never assumes so and checks.",
            ),
    ),
    NONE(
        id = "none",
        steps = listOf("Nothing to do: the setting does not exist on this API level."),
    ),
}

/** One cell of the restricted-settings matrix. */
data class RestrictedSettingsCell(
    val source: InstallSource,
    val setting: SensitiveSetting,
    val apiLevel: Int,
    val availability: SettingAvailability,
) {
    val recoveryPath: RecoveryPath
        get() =
            when (availability) {
                SettingAvailability.USER_ENABLEABLE -> {
                    RecoveryPath.NORMAL_SETTINGS_PATH
                }

                SettingAvailability.RESTRICTED_SETTING_LOCKED -> {
                    RecoveryPath.ALLOW_RESTRICTED_SETTINGS
                }

                SettingAvailability.RESTRICTED_AND_REGRANT_REQUIRED -> {
                    RecoveryPath.ALLOW_RESTRICTED_SETTINGS
                }

                SettingAvailability.REGRANT_REQUIRED -> {
                    RecoveryPath.GRANT_AGAIN
                }

                SettingAvailability.NOT_APPLICABLE -> {
                    RecoveryPath.NONE
                }
            }

    /**
     * The platform-capability reason a surface renders when this setting is needed and not granted.
     * Capture settings bind to `capture_blocked_by_setting`; every other sensitive setting is the
     * generic `device_permission_not_granted` condition and has no finer reason of its own.
     */
    val reasonWhenNotGranted: PlatformCapabilityReason?
        get() =
            when {
                availability == SettingAvailability.NOT_APPLICABLE -> {
                    null
                }

                !setting.usedByPenniLogic -> {
                    null
                }

                availability.isLocked -> {
                    PlatformCapabilityReason.RESTRICTED_SETTING_LOCKED
                }

                setting == SensitiveSetting.NOTIFICATION_LISTENER -> {
                    PlatformCapabilityReason.LISTENER_ACCESS_NOT_GRANTED
                }

                setting == SensitiveSetting.SMS_PERMISSION -> {
                    PlatformCapabilityReason.CAPTURE_PERMISSION_NOT_GRANTED
                }

                else -> {
                    null
                }
            }

    /** The taxonomy condition of a not-granted setting; the generic condition for non-capture settings. */
    val conditionWhenNotGranted: CaptureHealthCondition?
        get() =
            when {
                availability == SettingAvailability.NOT_APPLICABLE -> null
                else -> reasonWhenNotGranted?.condition ?: CaptureHealthCondition.DEVICE_PERMISSION_NOT_GRANTED
            }
}

/**
 * What a Play-installed, sideloaded or restored build can and cannot enable, per sensitive setting
 * and API level, and the supported QA recovery path for each cell.
 *
 * The cell values hold for a device with AOSP defaults; the platform rules behind them are:
 *
 * - **API 33/34** (`InstallPackageHelper.enableRestrictedSettings`, AOSP 13): the lock is applied at
 *   install time iff the installer marked the install as file-sourced (`PACKAGE_SOURCE_LOCAL_FILE`
 *   or `PACKAGE_SOURCE_DOWNLOADED_FILE`); the installer's identity plays no part. It covers the
 *   notification listener and accessibility services.
 * - **API 35/36** (`EnhancedConfirmationService.isPackageEcmGuarded`, Permission module, Android 15
 *   and 16): file-sourced installs are always guarded; otherwise a package is exempt only when the
 *   device trusts installs from non-allowlisted installers (AOSP ships an empty trusted-installer
 *   list, so this is the default), or the installer is preinstalled or allowlisted. The decision is
 *   made lazily when a setting is first toggled. The set grows to device admin, display over other
 *   apps, usage access, the SMS runtime permission and the default SMS and phone roles.
 * - **Restored builds**: the platform attempts to restore listener approvals, accessibility settings
 *   and runtime grants; none is guaranteed, so a restored build treats every grant as one to re-check
 *   and ask for again.
 *
 * The app reads the package source the installer declared from API 33 (`AndroidInstallSourceReader`
 * via `InstallSourceInfo.getPackageSource()`; the getter does not exist on API 30–32, where the
 * snapshot carries no source). A declared file source is the platform's own lock criterion and always
 * wins. For every other report — a source that is unspecified, `OTHER` or `STORE`, or no source at all
 * on API 30–32 — `InstallSourceClassifier` falls back to the installing and initiating package names:
 * Google Play and the shell are recognised directly, and the legacy sideload path through two
 * proxies: an installing or initiating package that is a package installer app, and an install whose
 * installing package is gone while the initiating package is not the shell. A proxy can claim a lock
 * the platform did not apply; the app's posture is safe either way, because an unproven lock only
 * changes the destination of the recovery action.
 *
 * `docs/platform/restricted-settings-matrix.md` renders this matrix; `RestrictedSettingsMatrixTest`
 * keeps the document identical to the code and asserts the invariants above.
 */
object RestrictedSettingsMatrix {
    /** API 31 has no restricted settings; 33 introduces them; 34 keeps them; 35 extends them; 36 is the target. */
    val API_LEVELS: List<Int> = (PlatformBaseline.QA_API_LEVELS + 34).sorted()

    fun availability(
        source: InstallSource,
        setting: SensitiveSetting,
        apiLevel: Int,
    ): SettingAvailability {
        if (apiLevel < setting.existsFromApi) return SettingAvailability.NOT_APPLICABLE
        val restrictedFrom = setting.restrictedFromApi
        val locked = restrictedFrom != null && apiLevel >= restrictedFrom && source.restrictedOnApi33Plus
        return when {
            locked && source.restored -> SettingAvailability.RESTRICTED_AND_REGRANT_REQUIRED
            locked -> SettingAvailability.RESTRICTED_SETTING_LOCKED
            source.restored -> SettingAvailability.REGRANT_REQUIRED
            else -> SettingAvailability.USER_ENABLEABLE
        }
    }

    fun cell(
        source: InstallSource,
        setting: SensitiveSetting,
        apiLevel: Int,
    ): RestrictedSettingsCell =
        RestrictedSettingsCell(source, setting, apiLevel, availability(source, setting, apiLevel))

    /** Every cell, in source, setting, API order. */
    fun cells(): List<RestrictedSettingsCell> =
        InstallSource.entries.flatMap { source ->
            SensitiveSetting.entries.flatMap { setting ->
                API_LEVELS.map { apiLevel -> cell(source, setting, apiLevel) }
            }
        }

    /** Settings a build from [source] cannot enable on [apiLevel] without the restricted-settings unlock. */
    fun lockedSettings(
        source: InstallSource,
        apiLevel: Int,
    ): List<SensitiveSetting> = SensitiveSetting.entries.filter { availability(source, it, apiLevel).isLocked }
}
