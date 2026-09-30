package com.pennilogic.android.platform.capture

import com.pennilogic.android.platform.scheduling.StandbyBucket
import com.pennilogic.android.platform.state.PlatformCapabilityReason
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class TrackingPauseDetectorTest {
    private val signals = FakePlatformSignals()
    private val detector = TrackingPauseDetector(signals)

    @Test
    fun `a healthy start on api 36 pauses nothing`() {
        signals.forceStopped = false
        assertNull(detector.detect())
    }

    @Test
    fun `api 35 and 36 report a force-stop through ApplicationStartInfo`() {
        for (api in listOf(35, 36)) {
            signals.apiLevel = api
            signals.forceStopped = true
            assertEquals("api $api", PlatformCapabilityReason.FORCE_STOPPED, detector.detect())
        }
    }

    @Test
    fun `api 30 to 34 fall back to the last exit reason`() {
        for (api in listOf(30, 31, 33, 34)) {
            signals.apiLevel = api
            signals.forceStopped = null
            signals.exitReason = AndroidPlatformSignals.REASON_USER_REQUESTED
            assertEquals("api $api", PlatformCapabilityReason.FORCE_STOPPED, detector.detect())
            signals.exitReason = 4 // REASON_CRASH: a crash is not a pause
            assertNull("api $api crash", detector.detect())
        }
    }

    @Test
    fun `below api 30 nothing can be detected and capture is re-probed instead`() {
        signals.apiLevel = 26
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
