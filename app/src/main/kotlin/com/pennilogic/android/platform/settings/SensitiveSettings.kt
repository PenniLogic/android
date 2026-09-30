package com.pennilogic.android.platform.settings

/**
 * How the running build reached the device, as far as the platform tells the app about itself
 * (`PackageManager.getInstallSourceInfo`). Restricted settings on API 33+ key off this: the platform
 * locks sensitive settings for builds that arrived from a user-acquired file, never for builds that
 * arrived through a store session or `adb`.
 */
enum class InstallSource(
    val id: String,
    val description: String,
    /** True when API 33+ applies the restricted-settings lock to builds from this source. */
    val restrictedOnApi33Plus: Boolean,
    /** True when the build was reinstalled by backup-and-restore or device-to-device transfer. */
    val restored: Boolean = false,
) {
    PLAY_STORE(
        id = "play_store",
        description = "Installed or updated by Google Play (installer com.android.vending, package source store).",
        restrictedOnApi33Plus = false,
    ),
    SESSION_STORE(
        id = "session_store",
        description =
            "Installed by another store through a PackageInstaller session with a store, other or " +
                "unspecified package source.",
        restrictedOnApi33Plus = false,
    ),
    ADB(
        id = "adb",
        description =
            "Installed with `adb install` or Android Studio: initiated by com.android.shell, no installing " +
                "package recorded.",
        restrictedOnApi33Plus = false,
    ),
    LEGACY_SIDELOAD(
        id = "legacy_sideload",
        description =
            "Installed from a file through the intent-based package installer (browser download, mail " +
                "attachment, file manager).",
        restrictedOnApi33Plus = true,
    ),
    SESSION_FILE(
        id = "session_file",
        description =
            "Installed through a PackageInstaller session that declared PACKAGE_SOURCE_LOCAL_FILE or " +
                "PACKAGE_SOURCE_DOWNLOADED_FILE.",
        restrictedOnApi33Plus = true,
    ),
    RESTORED_BY_PLAY(
        id = "restored_by_play",
        description =
            "Reinstalled by Google Play during device setup or restore; app data follows the backup rules " +
                "(this app excludes all of it).",
        restrictedOnApi33Plus = false,
        restored = true,
    ),
    RESTORED_BY_TRANSFER(
        id = "restored_by_transfer",
        description =
            "Copied by a device-to-device transfer as a non-Play package; the platform records no store, " +
                "so it is treated as a sideload.",
        restrictedOnApi33Plus = true,
        restored = true,
    ),
    ;

    companion object {
        fun fromId(id: String): InstallSource? = entries.firstOrNull { it.id == id }
    }
}

/** Kind of platform control a sensitive setting is. */
enum class SettingKind(
    val id: String,
) {
    /** Special app access toggled in system settings (no runtime prompt exists). */
    SPECIAL_ACCESS("special_access"),

    /** Runtime permission requested with a system prompt. */
    RUNTIME_PERMISSION("runtime_permission"),

    /** A default-app role held through the role manager. */
    ROLE("role"),
}

/**
 * Sensitive settings that a sideloaded or restored build may not be able to enable. The set is the
 * Android 15 CDD restricted-settings scope plus the notification runtime permission, with the API
 * level from which the platform locks each one for builds from a restricted [InstallSource].
 */
enum class SensitiveSetting(
    val id: String,
    val label: String,
    val kind: SettingKind,
    /** First API level on which the restricted-settings lock applies to this setting; null = never. */
    val restrictedFromApi: Int?,
    /** First API level on which the setting exists as a user-controlled setting at all. */
    val existsFromApi: Int,
    /** Whether PenniLogic uses the setting; unused settings are recorded so QA can rule them out. */
    val usedByPenniLogic: Boolean,
    /** The platform's own name for the permission or setting, for the `permission` signal attribute. */
    val platformName: String,
) {
    NOTIFICATION_LISTENER(
        id = "notification_listener",
        label = "Notification access (notification listener service)",
        kind = SettingKind.SPECIAL_ACCESS,
        restrictedFromApi = 33,
        existsFromApi = 18,
        usedByPenniLogic = true,
        platformName = "android.permission.BIND_NOTIFICATION_LISTENER_SERVICE",
    ),
    ACCESSIBILITY_SERVICE(
        id = "accessibility_service",
        label = "Accessibility service",
        kind = SettingKind.SPECIAL_ACCESS,
        restrictedFromApi = 33,
        existsFromApi = 14,
        usedByPenniLogic = false,
        platformName = "android.permission.BIND_ACCESSIBILITY_SERVICE",
    ),
    DEVICE_ADMIN(
        id = "device_admin",
        label = "Device admin app",
        kind = SettingKind.SPECIAL_ACCESS,
        restrictedFromApi = 35,
        existsFromApi = 8,
        usedByPenniLogic = false,
        platformName = "android.permission.BIND_DEVICE_ADMIN",
    ),
    DISPLAY_OVER_OTHER_APPS(
        id = "display_over_other_apps",
        label = "Display over other apps",
        kind = SettingKind.SPECIAL_ACCESS,
        restrictedFromApi = 35,
        existsFromApi = 23,
        usedByPenniLogic = false,
        platformName = "android.permission.SYSTEM_ALERT_WINDOW",
    ),
    USAGE_ACCESS(
        id = "usage_access",
        label = "Usage access",
        kind = SettingKind.SPECIAL_ACCESS,
        restrictedFromApi = 35,
        existsFromApi = 21,
        usedByPenniLogic = false,
        platformName = "android.permission.PACKAGE_USAGE_STATS",
    ),
    SMS_PERMISSION(
        id = "sms_permission",
        label = "SMS runtime permission (receive and read)",
        kind = SettingKind.RUNTIME_PERMISSION,
        restrictedFromApi = 35,
        existsFromApi = 23,
        usedByPenniLogic = true,
        platformName = "android.permission.RECEIVE_SMS",
    ),
    DEFAULT_SMS_ROLE(
        id = "default_sms_role",
        label = "Default SMS app role",
        kind = SettingKind.ROLE,
        restrictedFromApi = 35,
        existsFromApi = 19,
        usedByPenniLogic = false,
        platformName = "android.app.role.SMS",
    ),
    DEFAULT_DIALER_ROLE(
        id = "default_dialer_role",
        label = "Default phone app role",
        kind = SettingKind.ROLE,
        restrictedFromApi = 35,
        existsFromApi = 23,
        usedByPenniLogic = false,
        platformName = "android.app.role.DIALER",
    ),
    POST_NOTIFICATIONS(
        id = "post_notifications",
        label = "Notifications runtime permission",
        kind = SettingKind.RUNTIME_PERMISSION,
        restrictedFromApi = null,
        existsFromApi = 33,
        usedByPenniLogic = true,
        platformName = "android.permission.POST_NOTIFICATIONS",
    ),
    ;

    companion object {
        fun fromId(id: String): SensitiveSetting? = entries.firstOrNull { it.id == id }
    }
}

/** What a build from a given source can do with a sensitive setting on a given API level. */
enum class SettingAvailability(
    val id: String,
    val summary: String,
) {
    /** The user can enable it through the normal system settings or prompt. */
    USER_ENABLEABLE("user_enableable", "Enableable through the normal settings path or prompt."),

    /**
     * The platform shows the "Restricted setting" dialog; the user unlocks it once per app through
     * App info, then enables it normally. The app cannot unlock it.
     */
    RESTRICTED_SETTING_LOCKED(
        "restricted_setting_locked",
        "Locked by restricted settings; unlock once via App info > Allow restricted settings, then enable normally.",
    ),

    /** Enableable, but backup or transfer did not carry the grant over; the user grants it again. */
    REGRANT_REQUIRED(
        "regrant_required",
        "Not carried over by restore or transfer; grant again through the normal path.",
    ),

    /** Locked by restricted settings and, in addition, not carried over by the restore. */
    RESTRICTED_AND_REGRANT_REQUIRED(
        "restricted_and_regrant_required",
        "Not carried over by the transfer and locked by restricted settings; unlock via App info, then grant again.",
    ),

    /** The setting does not exist as a user control on this API level. */
    NOT_APPLICABLE("not_applicable", "No such user-controlled setting on this API level."),
    ;

    /** True when the platform's restricted-settings lock stands between the user and the setting. */
    val isLocked: Boolean
        get() = this == RESTRICTED_SETTING_LOCKED || this == RESTRICTED_AND_REGRANT_REQUIRED
}
