package com.pennilogic.android.platform.scheduling

/**
 * Why the platform stopped a job, in one vocabulary for JobScheduler (`JobParameters.getStopReason()`,
 * API 31+) and WorkManager (`WorkInfo.getStopReason()` / `ListenableWorker.getStopReason()`), whose
 * integer values are identical. Android 16 adds [TIMEOUT_ABANDONED] and enforces the runtime quota
 * also for jobs started in the top state or alongside a foreground service, so [QUOTA] and
 * [APP_STANDBY] are ordinary outcomes a drain must survive without losing or duplicating work.
 */
enum class StopReason(
    val id: String,
    val platformValue: Int,
    val category: Category,
) {
    UNDEFINED("undefined", 0, Category.OTHER),
    CANCELLED_BY_APP("cancelled_by_app", 1, Category.APP),
    PREEMPT("preempt", 2, Category.TRANSIENT),
    TIMEOUT("timeout", 3, Category.TRANSIENT),
    DEVICE_STATE("device_state", 4, Category.TRANSIENT),
    CONSTRAINT_BATTERY_NOT_LOW("constraint_battery_not_low", 5, Category.CONSTRAINT),
    CONSTRAINT_CHARGING("constraint_charging", 6, Category.CONSTRAINT),
    CONSTRAINT_CONNECTIVITY("constraint_connectivity", 7, Category.CONSTRAINT),
    CONSTRAINT_DEVICE_IDLE("constraint_device_idle", 8, Category.CONSTRAINT),
    CONSTRAINT_STORAGE_NOT_LOW("constraint_storage_not_low", 9, Category.CONSTRAINT),
    QUOTA("quota", 10, Category.QUOTA),
    BACKGROUND_RESTRICTION("background_restriction", 11, Category.USER_RESTRICTION),
    APP_STANDBY("app_standby", 12, Category.QUOTA),
    USER("user", 13, Category.USER_RESTRICTION),
    SYSTEM_PROCESSING("system_processing", 14, Category.TRANSIENT),
    ESTIMATED_APP_LAUNCH_TIME_CHANGED("estimated_app_launch_time_changed", 15, Category.TRANSIENT),

    /** Android 16: the job timed out without the app holding its JobParameters (a defect, not a platform mood). */
    TIMEOUT_ABANDONED("timeout_abandoned", 16, Category.DEFECT),

    /** WorkManager: the worker was not stopped (`WorkInfo.STOP_REASON_NOT_STOPPED`). */
    NOT_STOPPED("not_stopped", -256, Category.OTHER),

    /** WorkManager: stopped for a reason the platform does not expose (`WorkInfo.STOP_REASON_UNKNOWN`, API < 31). */
    UNKNOWN("unknown", -512, Category.OTHER),
    ;

    enum class Category {
        /** The app itself cancelled the work. */
        APP,

        /** The system will run the work again soon; nothing for the app to decide. */
        TRANSIENT,

        /** A declared constraint stopped holding; the scheduler re-runs when it holds again. */
        CONSTRAINT,

        /** Execution quota or standby bucket exhausted; the scheduler re-runs when quota returns. */
        QUOTA,

        /** The user restricted the app; only the user can lift it. */
        USER_RESTRICTION,

        /** The app's own defect. */
        DEFECT,

        OTHER,
    }

    companion object {
        /** Maps a platform value; values this baseline does not know become [UNKNOWN] rather than a crash. */
        fun fromPlatform(value: Int): StopReason = entries.firstOrNull { it.platformValue == value } ?: UNKNOWN

        fun fromId(id: String): StopReason? = entries.firstOrNull { it.id == id }
    }
}

/**
 * App standby bucket (`UsageStatsManager.getAppStandbyBucket()`). Only the public buckets are named;
 * the hidden exempted and never buckets, and anything else, map to [UNKNOWN] with the raw value kept
 * for the observability event.
 */
enum class StandbyBucket(
    val id: String,
    val platformValue: Int,
) {
    ACTIVE("active", 10),
    WORKING_SET("working_set", 20),
    FREQUENT("frequent", 30),
    RARE("rare", 40),
    RESTRICTED("restricted", 45),
    UNKNOWN("unknown", -1),
    ;

    /** Only the restricted bucket throttles background work hard enough to pause capture. */
    val pausesCapture: Boolean
        get() = this == RESTRICTED

    companion object {
        fun fromPlatform(value: Int): StandbyBucket = entries.firstOrNull { it.platformValue == value } ?: UNKNOWN

        fun fromId(id: String): StandbyBucket? = entries.firstOrNull { it.id == id }
    }
}
