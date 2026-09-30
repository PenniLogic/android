package com.pennilogic.android.platform.capture

import com.pennilogic.android.platform.scheduling.StandbyBucket
import com.pennilogic.android.platform.state.PlatformCapabilityReason
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * The detector is driven by the shape of the platform's answers, not by an API level it never reads:
 * `wasForceStopped()` is non-null on API 35+ and null below, `lastExitReason()` is non-null on API
 * 30+ and null below. `AndroidPlatformSignalsTest` proves those guards on the real SDK runtimes.
 */
class TrackingPauseDetectorTest {
    private val signals = FakePlatformSignals()
    private val detector = TrackingPauseDetector(signals)

    @Test
    fun `a healthy start pauses nothing`() {
        signals.forceStopped = false
        assertNull(detector.detect())
    }

    @Test
    fun `the platform's force-stop answer decides on its own`() {
        signals.forceStopped = true
        signals.exitReason = null
        assertEquals(PlatformCapabilityReason.FORCE_STOPPED, detector.detect())
        signals.forceStopped = true
        signals.exitReason = 4 // REASON_CRASH: the start-info answer still wins
        assertEquals(PlatformCapabilityReason.FORCE_STOPPED, detector.detect())
    }

    @Test
    fun `a swipe from recents is not a force-stop, whatever the exit reason says`() {
        // API 35+: wasForceStopped() == false, last exit REASON_USER_REQUESTED (force-stop OR swipe).
        signals.forceStopped = false
        signals.exitReason = AndroidPlatformSignals.REASON_USER_REQUESTED
        assertNull("receivers and jobs are intact after a swipe", detector.detect())
    }

    @Test
    fun `without a start-info answer the ambiguous exit reason is inconclusive`() {
        // API 30-34: no wasForceStopped(); REASON_USER_REQUESTED cannot be told apart from a swipe.
        signals.forceStopped = null
        signals.exitReason = AndroidPlatformSignals.REASON_USER_REQUESTED
        assertNull("documented limitation: not detectable, capture health is re-probed instead", detector.detect())
        signals.exitReason = 4 // REASON_CRASH: a crash is not a pause either
        assertNull(detector.detect())
    }

    @Test
    fun `a stopped profile is a stop on every level that reports exit reasons`() {
        signals.forceStopped = null
        signals.exitReason = AndroidPlatformSignals.REASON_USER_STOPPED
        assertEquals(PlatformCapabilityReason.FORCE_STOPPED, detector.detect())
        signals.forceStopped = false
        assertEquals(
            "start info does not report profile stops",
            PlatformCapabilityReason.FORCE_STOPPED,
            detector.detect(),
        )
    }

    @Test
    fun `below api 30 nothing can be detected and capture is re-probed instead`() {
        signals.forceStopped = null
        signals.exitReason = null
        assertNull(detector.detect())
    }

    @Test
    fun `a stop inside a non-managed profile is the private space`() {
        signals.profile = ProfileKind.OTHER_PROFILE
        signals.forceStopped = true
        assertEquals(PlatformCapabilityReason.PRIVATE_SPACE_PAUSED, detector.detect())
        signals.forceStopped = null
        signals.exitReason = AndroidPlatformSignals.REASON_USER_STOPPED
        assertEquals(PlatformCapabilityReason.PRIVATE_SPACE_PAUSED, detector.detect())
        signals.profile = ProfileKind.MANAGED
        assertEquals(
            "a work profile stop is a plain force-stop",
            PlatformCapabilityReason.FORCE_STOPPED,
            detector.detect(),
        )
    }

    @Test
    fun `restrictions are reported when nothing was stopped, force-stop wins otherwise`() {
        signals.forceStopped = false
        signals.backgroundRestricted = true
        assertEquals(PlatformCapabilityReason.BACKGROUND_RESTRICTED, detector.detect())
        signals.backgroundRestricted = false
        signals.bucket = StandbyBucket.RESTRICTED
        assertEquals(PlatformCapabilityReason.STANDBY_BUCKET_RESTRICTED, detector.detect())
        signals.bucket = StandbyBucket.RARE
        assertNull("rare is throttled but not paused", detector.detect())
        signals.forceStopped = true
        signals.backgroundRestricted = true
        assertEquals(PlatformCapabilityReason.FORCE_STOPPED, detector.detect())
    }
}
