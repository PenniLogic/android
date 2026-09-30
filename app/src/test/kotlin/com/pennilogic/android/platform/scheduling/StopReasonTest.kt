package com.pennilogic.android.platform.scheduling

import com.pennilogic.android.platform.state.CaptureHealthCondition
import com.pennilogic.android.platform.state.PlatformCapabilityReason
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class StopReasonTest {
    @Test
    fun `platform values mirror JobParameters and WorkInfo constants`() {
        assertEquals(StopReason.QUOTA, StopReason.fromPlatform(10))
        assertEquals(StopReason.BACKGROUND_RESTRICTION, StopReason.fromPlatform(11))
        assertEquals(StopReason.APP_STANDBY, StopReason.fromPlatform(12))
        assertEquals(StopReason.USER, StopReason.fromPlatform(13))
        assertEquals(StopReason.TIMEOUT_ABANDONED, StopReason.fromPlatform(16))
        assertEquals(StopReason.NOT_STOPPED, StopReason.fromPlatform(-256))
        assertEquals(StopReason.UNKNOWN, StopReason.fromPlatform(-512))
        assertEquals(
            "a value this baseline does not know never crashes",
            StopReason.UNKNOWN,
            StopReason.fromPlatform(99),
        )
        val values = StopReason.entries.map { it.platformValue }
        assertEquals(values.size, values.toSet().size)
        assertEquals(
            (0..16).toList(),
            StopReason.entries
                .map { it.platformValue }
                .filter { it >= 0 }
                .sorted(),
        )
    }

    @Test
    fun `identifiers are unique snake_case within each vocabulary`() {
        val identifier = Regex("[a-z][a-z0-9_]*")
        val vocabularies =
            listOf(
                StopReason.entries.map { it.id },
                StandbyBucket.entries.map { it.id },
                StopDisposition.entries.map { it.id },
            )
        for (ids in vocabularies) {
            assertEquals(ids.size, ids.toSet().size)
            ids.forEach { assertTrue(it, identifier.matches(it)) }
        }
    }

    @Test
    fun `standby buckets mirror UsageStatsManager and only restricted pauses capture`() {
        assertEquals(StandbyBucket.ACTIVE, StandbyBucket.fromPlatform(10))
        assertEquals(StandbyBucket.WORKING_SET, StandbyBucket.fromPlatform(20))
        assertEquals(StandbyBucket.FREQUENT, StandbyBucket.fromPlatform(30))
        assertEquals(StandbyBucket.RARE, StandbyBucket.fromPlatform(40))
        assertEquals(StandbyBucket.RESTRICTED, StandbyBucket.fromPlatform(45))
        assertEquals("hidden exempted bucket", StandbyBucket.UNKNOWN, StandbyBucket.fromPlatform(5))
        assertEquals("hidden never bucket", StandbyBucket.UNKNOWN, StandbyBucket.fromPlatform(50))
        assertEquals(listOf(StandbyBucket.RESTRICTED), StandbyBucket.entries.filter { it.pausesCapture })
    }

    @Test
    fun `every stop reason has a disposition that releases claimed items`() {
        for (reason in StopReason.entries) {
            val disposition = StopReasonPolicy.disposition(reason)
            if (reason == StopReason.NOT_STOPPED) {
                assertEquals(StopDisposition.NONE, disposition)
            } else {
                assertTrue("$reason -> $disposition", disposition != StopDisposition.NONE)
                assertTrue(disposition.description.startsWith("Release in-flight items"))
            }
        }
        assertEquals(StopDisposition.DEFER_TO_SCHEDULER, StopReasonPolicy.disposition(StopReason.QUOTA))
        assertEquals(StopDisposition.DEFER_TO_SCHEDULER, StopReasonPolicy.disposition(StopReason.APP_STANDBY))
        assertEquals(
            StopDisposition.DEFER_TO_SCHEDULER,
            StopReasonPolicy.disposition(StopReason.CONSTRAINT_CONNECTIVITY),
        )
        assertEquals(StopDisposition.RETRY_WITH_BACKOFF, StopReasonPolicy.disposition(StopReason.TIMEOUT))
        assertEquals(StopDisposition.RETRY_WITH_BACKOFF, StopReasonPolicy.disposition(StopReason.UNKNOWN))
        assertEquals(
            StopDisposition.PAUSE_UNTIL_USER_ACTION,
            StopReasonPolicy.disposition(StopReason.BACKGROUND_RESTRICTION),
        )
        assertEquals(StopDisposition.PAUSE_UNTIL_USER_ACTION, StopReasonPolicy.disposition(StopReason.USER))
        assertEquals(StopDisposition.CANCELLED, StopReasonPolicy.disposition(StopReason.CANCELLED_BY_APP))
        assertEquals(StopDisposition.RECORD_DEFECT, StopReasonPolicy.disposition(StopReason.TIMEOUT_ABANDONED))
    }

    @Test
    fun `a quota stop pauses sync but never capture, restrictions do`() {
        assertNull(StopReasonPolicy.captureHealthReason(StopReason.QUOTA, StandbyBucket.ACTIVE))
        assertNull(StopReasonPolicy.captureHealthReason(StopReason.APP_STANDBY, StandbyBucket.RARE))
        assertNull(StopReasonPolicy.captureHealthReason(StopReason.TIMEOUT, StandbyBucket.WORKING_SET))
        assertEquals(
            PlatformCapabilityReason.BACKGROUND_RESTRICTED,
            StopReasonPolicy.captureHealthReason(StopReason.BACKGROUND_RESTRICTION, StandbyBucket.ACTIVE),
        )
        assertEquals(
            PlatformCapabilityReason.STANDBY_BUCKET_RESTRICTED,
            StopReasonPolicy.captureHealthReason(StopReason.APP_STANDBY, StandbyBucket.RESTRICTED),
        )
        assertEquals(
            PlatformCapabilityReason.STANDBY_BUCKET_RESTRICTED,
            StopReasonPolicy.captureHealthReason(StopReason.QUOTA, StandbyBucket.RESTRICTED),
        )
        StopReason.entries.forEach { reason ->
            StandbyBucket.entries.forEach { bucket ->
                StopReasonPolicy.captureHealthReason(reason, bucket)?.let {
                    assertEquals("$reason/$bucket", CaptureHealthCondition.CAPTURE_PAUSED_BY_PLATFORM, it.condition)
                }
            }
        }
    }
}
