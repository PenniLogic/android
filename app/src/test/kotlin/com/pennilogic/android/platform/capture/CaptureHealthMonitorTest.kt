package com.pennilogic.android.platform.capture

import android.content.Context
import android.os.Build
import androidx.test.core.app.ApplicationProvider
import com.pennilogic.android.platform.scheduling.StandbyBucket
import com.pennilogic.android.platform.scheduling.StopReason
import com.pennilogic.android.platform.state.ClientState
import com.pennilogic.android.platform.state.PlatformCapabilityReason
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

class CaptureHealthTest {
    @Test
    fun `values bind to the published conditions and states`() {
        assertEquals("healthy", CaptureHealth.Healthy.id)
        assertNull(CaptureHealth.Healthy.clientState)
        val paused = CaptureHealth.Paused(PlatformCapabilityReason.FORCE_STOPPED, 5)
        assertEquals("paused", paused.id)
        assertEquals(ClientState.DEGRADED, paused.clientState)
        val blocked =
            CaptureHealth.Blocked(
                PlatformCapabilityReason.RESTRICTED_SETTING_LOCKED,
                "android.permission.BIND_NOTIFICATION_LISTENER_SERVICE",
            )
        assertEquals("blocked", blocked.id)
        assertEquals(ClientState.PERMISSION_DENIED, blocked.clientState)
        assertEquals("device", blocked.cause.id)
    }

    @Test
    fun `a pause cannot carry a blocking reason and vice versa`() {
        assertThrows(IllegalArgumentException::class.java) {
            CaptureHealth.Paused(PlatformCapabilityReason.LISTENER_ACCESS_NOT_GRANTED, 1)
        }
        assertThrows(IllegalArgumentException::class.java) {
            CaptureHealth.Blocked(PlatformCapabilityReason.FORCE_STOPPED, null)
        }
    }

    @Test
    fun `the event carries api level, stop reason, bucket and state and nothing else`() {
        val event =
            CaptureHealthEvent(
                apiLevel = 36,
                captureHealth = CaptureHealth.Paused(PlatformCapabilityReason.FORCE_STOPPED, 1),
                stopReason = StopReason.QUOTA,
                standbyBucket = StandbyBucket.RARE,
                rawStandbyBucket = 40,
            )
        assertEquals(
            "{\"event\":\"capture_health\",\"api_level\":36,\"target_api\":36,\"stop_reason\":\"quota\"," +
                "\"standby_bucket\":\"rare\",\"standby_bucket_raw\":40,\"capture_health\":\"paused\"," +
                "\"condition\":\"capture_paused_by_platform\",\"reason\":\"force_stopped\"}",
            event.toJson(),
        )
        val drainEvent =
            CaptureHealthEvent(
                apiLevel = 31,
                captureHealth = null,
                stopReason = null,
                standbyBucket = StandbyBucket.UNKNOWN,
                rawStandbyBucket = 5,
            )
        assertEquals(
            "{\"event\":\"capture_health\",\"api_level\":31,\"target_api\":36,\"stop_reason\":null," +
                "\"standby_bucket\":\"unknown\",\"standby_bucket_raw\":5,\"capture_health\":null,\"condition\":null,\"reason\":null}",
            drainEvent.toJson(),
        )
        val keys = Regex("\"([a-z_]+)\":").findAll(event.toJson()).map { it.groupValues[1] }.toSet()
        assertEquals(
            setOf(
                "event",
                "api_level",
                "target_api",
                "stop_reason",
                "standby_bucket",
                "standby_bucket_raw",
                "capture_health",
                "condition",
                "reason",
            ),
            keys,
        )
    }
}

class CaptureHealthMonitorTest {
    private val signals = FakePlatformSignals()
    private val store = InMemoryCaptureHealthStore()
    private var now = 1_000L
    private val monitor = CaptureHealthMonitor(store, TrackingPauseDetector(signals), signals) { now }

    @Test
    fun `a healthy start stays healthy`() {
        signals.forceStopped = false
        assertEquals(CaptureHealth.Healthy, monitor.onProcessStart())
        assertEquals(CaptureHealth.Healthy, monitor.current())
    }

    @Test
    fun `after a force-stop the app shows tracking paused until capture health is restored`() {
        signals.forceStopped = true
        val paused = monitor.onProcessStart()
        assertEquals(CaptureHealth.Paused(PlatformCapabilityReason.FORCE_STOPPED, 1_000), paused)
        assertEquals(ClientState.DEGRADED, paused.clientState)

        // The next start is clean, but a start alone never clears the pause.
        signals.clear()
        now = 2_000
        assertEquals(CaptureHealth.Paused(PlatformCapabilityReason.FORCE_STOPPED, 1_000), monitor.onProcessStart())

        // A probe without registered components proves nothing.
        assertEquals(
            CaptureHealth.Paused(PlatformCapabilityReason.FORCE_STOPPED, 1_000),
            monitor.onHealthProbeSucceeded(),
        )

        monitor.onCaptureComponentsRegistered()
        assertEquals("registration alone is not health", "paused", monitor.current().id)
        assertEquals(CaptureHealth.Healthy, monitor.onHealthProbeSucceeded())
        assertEquals(CaptureHealth.Healthy, monitor.current())
    }

    @Test
    fun `a new start voids the previous process's registration`() {
        signals.forceStopped = true
        monitor.onProcessStart()
        monitor.onCaptureComponentsRegistered()
        signals.clear()
        monitor.onProcessStart()
        assertEquals(
            "the probe must wait for this process's registration",
            "paused",
            monitor.onHealthProbeSucceeded().id,
        )
        assertEquals(CaptureHealth.Healthy, monitor.onHealthRestored())
    }

    @Test
    fun `a private-space stop is reported with its own reason and recovers the same way`() {
        signals.profile = ProfileKind.OTHER_PROFILE
        signals.forceStopped = true
        assertEquals(
            CaptureHealth.Paused(PlatformCapabilityReason.PRIVATE_SPACE_PAUSED, 1_000),
            monitor.onProcessStart(),
        )
        signals.clear()
        assertEquals(CaptureHealth.Healthy, monitor.onHealthRestored())
    }

    @Test
    fun `a newer platform reason replaces the reason but keeps the pause`() {
        signals.forceStopped = true
        monitor.onProcessStart()
        signals.clear()
        signals.backgroundRestricted = true
        now = 5_000
        assertEquals(
            CaptureHealth.Paused(PlatformCapabilityReason.BACKGROUND_RESTRICTED, 1_000),
            monitor.onProcessStart(),
        )
    }

    @Test
    fun `a blocked setting renders ahead of a pause and clears independently`() {
        signals.forceStopped = true
        monitor.onProcessStart()
        val blocked =
            monitor.onBlockedBySetting(
                PlatformCapabilityReason.RESTRICTED_SETTING_LOCKED,
                "android.permission.BIND_NOTIFICATION_LISTENER_SERVICE",
            )
        assertEquals(ClientState.PERMISSION_DENIED, blocked.clientState)
        assertEquals("paused", monitor.onSettingGranted().id)
        assertThrows(IllegalArgumentException::class.java) {
            monitor.onBlockedBySetting(PlatformCapabilityReason.FORCE_STOPPED, null)
        }
    }

    @Test
    fun `a runtime pause from a worker is recorded, replaces the reason and never clears`() {
        signals.forceStopped = false
        assertEquals(CaptureHealth.Healthy, monitor.onProcessStart())
        now = 3_000
        assertEquals(
            CaptureHealth.Paused(PlatformCapabilityReason.STANDBY_BUCKET_RESTRICTED, 3_000),
            monitor.onPlatformPause(PlatformCapabilityReason.STANDBY_BUCKET_RESTRICTED),
        )
        now = 4_000
        assertEquals(
            "the pause continues from its first observation under the newer reason",
            CaptureHealth.Paused(PlatformCapabilityReason.BACKGROUND_RESTRICTED, 3_000),
            monitor.onPlatformPause(PlatformCapabilityReason.BACKGROUND_RESTRICTED),
        )
        signals.clear()
        assertEquals("a clean start never clears a pause", "paused", monitor.onProcessStart().id)
        assertThrows(IllegalArgumentException::class.java) {
            monitor.onPlatformPause(PlatformCapabilityReason.LISTENER_ACCESS_NOT_GRANTED)
        }
        assertEquals("cleared only by restoration, like every pause", CaptureHealth.Healthy, monitor.onHealthRestored())
    }

    @Test
    fun `clearing erases the record and the private-space indicator with it`() {
        signals.profile = ProfileKind.OTHER_PROFILE
        signals.forceStopped = true
        monitor.onProcessStart()
        monitor.onBlockedBySetting(PlatformCapabilityReason.LISTENER_ACCESS_NOT_GRANTED, null)
        assertEquals(CaptureHealth.Healthy, monitor.clear())
        assertEquals(CaptureHealthRecord(), store.read())
    }

    @Test
    fun `the event reflects the current state and the platform bucket`() {
        signals.forceStopped = true
        signals.bucket = StandbyBucket.RESTRICTED
        signals.rawBucket = 45
        monitor.onProcessStart()
        val json = monitor.event().toJson()
        assertTrue(json, json.contains("\"capture_health\":\"paused\""))
        assertTrue(json, json.contains("\"reason\":\"force_stopped\""))
        assertTrue(json, json.contains("\"standby_bucket\":\"restricted\""))
        assertFalse(json.contains("payload"))
    }

    @Test
    fun `every pause reason survives a store round trip`() {
        for (reason in PAUSE_REASONS) {
            val store = InMemoryCaptureHealthStore(CaptureHealthRecord(pauseReason = reason, pausedSinceMillis = 7))
            val monitor =
                CaptureHealthMonitor(
                    store,
                    TrackingPauseDetector(FakePlatformSignals(forceStopped = false)),
                    signals,
                ) { 8 }
            assertEquals(CaptureHealth.Paused(reason, 7), monitor.current())
        }
    }
}

@RunWith(RobolectricTestRunner::class)
class PreferencesCaptureHealthStoreTest {
    @Test
    fun `stores only identifiers and timestamps and reads them back`() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        val store = PreferencesCaptureHealthStore(context)
        assertEquals(CaptureHealthRecord(), store.read())
        val record =
            CaptureHealthRecord(
                pauseReason = PlatformCapabilityReason.PRIVATE_SPACE_PAUSED,
                pausedSinceMillis = 123,
                componentsRegistered = true,
                blockReason = PlatformCapabilityReason.RESTRICTED_SETTING_LOCKED,
                blockPermission = "android.permission.BIND_NOTIFICATION_LISTENER_SERVICE",
            )
        store.write(record)
        assertEquals(record, PreferencesCaptureHealthStore(context).read())
        val all = context.getSharedPreferences(PreferencesCaptureHealthStore.FILE, Context.MODE_PRIVATE).all
        assertEquals(
            setOf("pause_reason", "paused_since", "components_registered", "block_reason", "block_permission"),
            all.keys,
        )
        store.write(CaptureHealthRecord())
        assertEquals(CaptureHealthRecord(), store.read())
    }

    @Test
    fun `clear erases every key of the file`() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        val store = PreferencesCaptureHealthStore(context)
        val record =
            CaptureHealthRecord(pauseReason = PlatformCapabilityReason.PRIVATE_SPACE_PAUSED, pausedSinceMillis = 9)
        store.write(record)
        store.clear()
        assertEquals(CaptureHealthRecord(), PreferencesCaptureHealthStore(context).read())
        assertTrue(context.getSharedPreferences(PreferencesCaptureHealthStore.FILE, Context.MODE_PRIVATE).all.isEmpty())
    }
}

/**
 * The API-level guards of the platform reader, executed on the QA runtimes: below 35 there is no
 * `ApplicationStartInfo`, below 33 no `UserManager.isProfile`; every read returns instead of throwing.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [31, 33, 35, 36])
class AndroidPlatformSignalsTest {
    @Test
    fun `platform signals follow the runtime api level and never throw`() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        val signals = AndroidPlatformSignals(context)
        val sdk = Build.VERSION.SDK_INT
        assertEquals(sdk, signals.apiLevel)
        val forceStopped = signals.wasForceStopped()
        if (sdk < 35) assertNull("no ApplicationStartInfo below API 35 (sdk $sdk)", forceStopped)
        signals.lastExitReason()
        assertEquals("sdk $sdk", ProfileKind.PERSONAL, signals.profileKind())
        val (bucket, raw) = signals.standbyBucket()
        assertEquals("sdk $sdk", StandbyBucket.fromPlatform(raw), bucket)
        assertFalse(signals.isBackgroundRestricted())
        assertNull("a fresh process has no detectable pause (sdk $sdk)", TrackingPauseDetector(signals).detect())
    }
}
