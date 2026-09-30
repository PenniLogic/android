package com.pennilogic.android.platform.capture

import android.app.ActivityManager
import android.app.usage.UsageStatsManager
import android.content.Context
import android.os.Build
import android.os.UserManager
import com.pennilogic.android.platform.scheduling.StandbyBucket

/** The kind of user profile the process runs in, as far as the public SDK can tell. */
enum class ProfileKind(
    val id: String,
) {
    /** The device owner's personal profile. */
    PERSONAL("personal"),

    /** A managed (work) profile. */
    MANAGED("managed"),

    /**
     * A profile that is neither personal nor managed: the private space on Android 15+, or a clone
     * profile. The public SDK does not distinguish the two, so both are treated as the private space.
     */
    OTHER_PROFILE("other_profile"),
}

/** What the platform reports about the process's own history and standing; fakeable in tests. */
interface PlatformSignals {
    val apiLevel: Int

    /**
     * True when this is the first process start after the app was force-stopped (API 35+, from
     * `ApplicationStartInfo.wasForceStopped()`); null when the platform cannot say (below API 35).
     * A non-null answer decides the force-stop case alone.
     */
    fun wasForceStopped(): Boolean?

    /**
     * `ApplicationExitInfo.getReason()` of the most recent exit of this app (API 30+), or null. Only
     * `REASON_USER_STOPPED` (profile stopped) is read as a stop; see [TrackingPauseDetector].
     */
    fun lastExitReason(): Int?

    fun profileKind(): ProfileKind

    /** The standby bucket with its raw platform value, for the observability event. */
    fun standbyBucket(): Pair<StandbyBucket, Int>

    /** `ActivityManager.isBackgroundRestricted()` (API 28+). */
    fun isBackgroundRestricted(): Boolean
}

/** Platform-backed [PlatformSignals]; every call is guarded by API level and never throws to callers. */
class AndroidPlatformSignals(
    private val context: Context,
) : PlatformSignals {
    override val apiLevel: Int = Build.VERSION.SDK_INT

    private val activityManager: ActivityManager?
        get() = context.getSystemService(ActivityManager::class.java)

    override fun wasForceStopped(): Boolean? {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.VANILLA_ICE_CREAM) return null
        val starts =
            runCatching { activityManager?.getHistoricalProcessStartReasons(1) }.getOrNull() ?: return null
        return starts.firstOrNull()?.wasForceStopped()
    }

    override fun lastExitReason(): Int? {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return null
        val exits =
            runCatching { activityManager?.getHistoricalProcessExitReasons(null, 0, 1) }.getOrNull() ?: return null
        return exits.firstOrNull()?.reason
    }

    override fun profileKind(): ProfileKind {
        // isProfile() is public from API 33; the private space itself only exists from API 35.
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return ProfileKind.PERSONAL
        val userManager = context.getSystemService(UserManager::class.java) ?: return ProfileKind.PERSONAL
        if (!userManager.isProfile) return ProfileKind.PERSONAL
        return if (userManager.isManagedProfile) ProfileKind.MANAGED else ProfileKind.OTHER_PROFILE
    }

    override fun standbyBucket(): Pair<StandbyBucket, Int> {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.P) return StandbyBucket.UNKNOWN to -1
        val manager = context.getSystemService(UsageStatsManager::class.java) ?: return StandbyBucket.UNKNOWN to -1
        val raw = runCatching { manager.appStandbyBucket }.getOrDefault(-1)
        return StandbyBucket.fromPlatform(raw) to raw
    }

    override fun isBackgroundRestricted(): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.P) return false
        return runCatching { activityManager?.isBackgroundRestricted }.getOrNull() ?: false
    }

    companion object {
        /**
         * `ApplicationExitInfo.REASON_USER_REQUESTED` (API 30). Documented by Android as a force-stop
         * **or** a swipe from Recents (before API 34 also an app update) with no public sub-reason, so
         * [TrackingPauseDetector] does not read it as a stop; kept as the named value the documents and
         * tests refer to.
         */
        const val REASON_USER_REQUESTED: Int = 10

        /** `ApplicationExitInfo.REASON_USER_STOPPED` (API 30): the user profile the app ran in was stopped. */
        const val REASON_USER_STOPPED: Int = 11
    }
}
