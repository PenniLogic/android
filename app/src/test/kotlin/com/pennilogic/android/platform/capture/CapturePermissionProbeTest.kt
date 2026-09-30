package com.pennilogic.android.platform.capture

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.pennilogic.android.platform.state.ClientState
import com.pennilogic.android.platform.state.PlatformCapabilityReason
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf

private const val RECEIVE_SMS = "android.permission.RECEIVE_SMS"

class CapturePermissionProbeTest {
    @Test
    fun `a granted permission blocks nothing, a missing one names the reason and the permission`() {
        val granted = mutableSetOf<String>()
        val probe = CapturePermissionProbe { it in granted }
        assertEquals(PlatformCapabilityReason.CAPTURE_PERMISSION_NOT_GRANTED, probe.reason(RECEIVE_SMS))
        val blocked = probe.blocked(RECEIVE_SMS)
        val expected = CaptureHealth.Blocked(PlatformCapabilityReason.CAPTURE_PERMISSION_NOT_GRANTED, RECEIVE_SMS)
        assertEquals(expected, blocked)
        assertEquals(ClientState.PERMISSION_DENIED, blocked?.clientState)
        granted += RECEIVE_SMS
        assertNull(probe.reason(RECEIVE_SMS))
        assertNull(probe.blocked(RECEIVE_SMS))
    }

    @Test
    fun `only platform permission names are probed`() {
        val probe = CapturePermissionProbe { true }
        for (bad in listOf("", "sms", "RECEIVE_SMS", "com.example.PERMISSION", "android.permission.receive_sms")) {
            assertThrows(bad, IllegalArgumentException::class.java) { probe.reason(bad) }
        }
        assertNull(probe.reason("android.app.role.SMS"))
    }

    @Test
    fun `the blocked health feeds the monitor like any setting block`() {
        val signals = FakePlatformSignals(forceStopped = false)
        val monitor = CaptureHealthMonitor(InMemoryCaptureHealthStore(), TrackingPauseDetector(signals), signals) { 1 }
        val blocked = CapturePermissionProbe { false }.blocked(RECEIVE_SMS)!!
        assertEquals(blocked, monitor.onBlockedBySetting(blocked.reason, blocked.permission))
        assertEquals(CaptureHealth.Healthy, monitor.onSettingGranted())
    }
}

/** The platform reader against Robolectric's permission state: no request, no prompt, just a read. */
@RunWith(RobolectricTestRunner::class)
class AndroidPermissionStateReaderTest {
    @Test
    fun `reads the granted state of a runtime permission without requesting it`() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        val probe = CapturePermissionProbe(AndroidPermissionStateReader(context))
        assertEquals(
            "this baseline declares and requests no capture permission",
            PlatformCapabilityReason.CAPTURE_PERMISSION_NOT_GRANTED,
            probe.reason(RECEIVE_SMS),
        )
        shadowOf(context as android.app.Application).grantPermissions(RECEIVE_SMS)
        assertNull(probe.reason(RECEIVE_SMS))
        shadowOf(context).denyPermissions(RECEIVE_SMS)
        assertEquals(PlatformCapabilityReason.CAPTURE_PERMISSION_NOT_GRANTED, probe.reason(RECEIVE_SMS))
    }
}
