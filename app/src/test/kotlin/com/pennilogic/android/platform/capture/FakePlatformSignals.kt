package com.pennilogic.android.platform.capture

import com.pennilogic.android.platform.scheduling.StandbyBucket
import com.pennilogic.android.platform.state.PlatformCapabilityReason

/** Scriptable [PlatformSignals] for the detector and monitor tests. */
class FakePlatformSignals(
    override var apiLevel: Int = 36,
    var forceStopped: Boolean? = null,
    var exitReason: Int? = null,
    var profile: ProfileKind = ProfileKind.PERSONAL,
    var bucket: StandbyBucket = StandbyBucket.ACTIVE,
    var rawBucket: Int = 10,
    var backgroundRestricted: Boolean = false,
) : PlatformSignals {
    override fun wasForceStopped(): Boolean? = forceStopped

    override fun lastExitReason(): Int? = exitReason

    override fun profileKind(): ProfileKind = profile

    override fun standbyBucket(): Pair<StandbyBucket, Int> = bucket to rawBucket

    override fun isBackgroundRestricted(): Boolean = backgroundRestricted

    fun clear() {
        forceStopped = false
        exitReason = null
        backgroundRestricted = false
        bucket = StandbyBucket.ACTIVE
        rawBucket = 10
    }
}

/** Convenience for tests that need a paused reason. */
val PAUSE_REASONS: List<PlatformCapabilityReason> =
    listOf(
        PlatformCapabilityReason.FORCE_STOPPED,
        PlatformCapabilityReason.PRIVATE_SPACE_PAUSED,
        PlatformCapabilityReason.STANDBY_BUCKET_RESTRICTED,
        PlatformCapabilityReason.BACKGROUND_RESTRICTED,
    )
