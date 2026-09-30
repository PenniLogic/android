package com.pennilogic.android.platform.settings

import android.content.ComponentName
import androidx.test.core.app.ApplicationProvider
import com.pennilogic.android.platform.state.PlatformCapabilityReason
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf

class InstallSourceClassifierTest {
    @Test
    fun `play, stores, adb and file installs classify from what the platform reports`() {
        val cases =
            mapOf(
                InstallSourceSnapshot("com.android.vending", "com.android.vending", 2) to InstallSource.PLAY_STORE,
                InstallSourceSnapshot("com.android.vending", "com.android.vending", null) to InstallSource.PLAY_STORE,
                InstallSourceSnapshot(null, "com.android.shell", 0) to InstallSource.ADB,
                InstallSourceSnapshot(null, null, null) to InstallSource.ADB,
                InstallSourceSnapshot(
                    "com.google.android.packageinstaller",
                    "com.google.android.packageinstaller",
                    0,
                ) to
                    InstallSource.LEGACY_SIDELOAD,
                InstallSourceSnapshot("com.android.packageinstaller", "com.android.packageinstaller", null) to
                    InstallSource.LEGACY_SIDELOAD,
                // Vendor builds of the package installer are the same intent-based sideload path.
                InstallSourceSnapshot("com.miui.packageinstaller", "com.miui.packageinstaller", 0) to
                    InstallSource.LEGACY_SIDELOAD,
                InstallSourceSnapshot("com.samsung.android.packageinstaller", null, null) to
                    InstallSource.LEGACY_SIDELOAD,
                InstallSourceSnapshot("org.fdroid.fdroid", "org.fdroid.fdroid", 2) to InstallSource.SESSION_STORE,
                InstallSourceSnapshot("com.sec.android.app.samsungapps", "com.sec.android.app.samsungapps", 0) to
                    InstallSource.SESSION_STORE,
                InstallSourceSnapshot("com.google.android.apps.nbu.files", "com.google.android.apps.nbu.files", 3) to
                    InstallSource.SESSION_FILE,
                InstallSourceSnapshot("com.android.chrome", "com.android.chrome", 4) to InstallSource.SESSION_FILE,
                // A declared file source wins even when the installing package looks like a store.
                InstallSourceSnapshot("com.android.vending", "com.android.vending", 4) to InstallSource.SESSION_FILE,
                // An installer that disappeared leaves no installing package: treated as a sideload, never unlocked.
                InstallSourceSnapshot(null, "com.example.installer", 0) to InstallSource.LEGACY_SIDELOAD,
            )
        for ((snapshot, expected) in cases) {
            assertEquals("$snapshot", expected, InstallSourceClassifier.classify(snapshot))
        }
    }

    @Test
    fun `nothing reported classifies to nothing`() {
        assertNull(InstallSourceClassifier.classify(null))
    }
}

class CaptureSettingsProbeTest {
    private val listener =
        ComponentName("com.pennilogic.android", "com.pennilogic.android.capture.NotificationCaptureService")

    private fun probe(
        snapshot: InstallSourceSnapshot?,
        granted: Boolean,
        apiLevel: Int,
    ) = CaptureSettingsProbe({ snapshot }, { granted }, apiLevel)

    @Test
    fun `granted access needs no reason whatever the source`() {
        assertNull(
            probe(
                InstallSourceSnapshot("com.google.android.packageinstaller", null, 0),
                granted = true,
                apiLevel = 36,
            ).notificationListenerReason(listener),
        )
        assertNull(probe(null, granted = true, apiLevel = 36).notificationListenerReason(listener))
    }

    @Test
    fun `an unlocked source that is not granted points at the normal settings path`() {
        val play =
            probe(
                InstallSourceSnapshot("com.android.vending", "com.android.vending", 2),
                granted = false,
                apiLevel = 36,
            )
        assertEquals(PlatformCapabilityReason.LISTENER_ACCESS_NOT_GRANTED, play.notificationListenerReason(listener))
        val adb = probe(InstallSourceSnapshot(null, "com.android.shell", 0), granted = false, apiLevel = 36)
        assertEquals(PlatformCapabilityReason.LISTENER_ACCESS_NOT_GRANTED, adb.notificationListenerReason(listener))
    }

    @Test
    fun `a sideloaded build on api 33 or later reports the restricted-setting lock`() {
        val sideload =
            InstallSourceSnapshot("com.google.android.packageinstaller", "com.google.android.packageinstaller", 4)
        for (apiLevel in listOf(33, 34, 35, 36)) {
            assertEquals(
                "api $apiLevel",
                PlatformCapabilityReason.RESTRICTED_SETTING_LOCKED,
                probe(sideload, granted = false, apiLevel = apiLevel).notificationListenerReason(listener),
            )
        }
        assertEquals(
            PlatformCapabilityReason.LISTENER_ACCESS_NOT_GRANTED,
            probe(sideload, granted = false, apiLevel = 31).notificationListenerReason(listener),
        )
    }

    @Test
    fun `an unknown source never claims a lock`() {
        assertEquals(
            PlatformCapabilityReason.LISTENER_ACCESS_NOT_GRANTED,
            probe(null, granted = false, apiLevel = 36).notificationListenerReason(listener),
        )
    }

    @Test
    fun `every reason the probe can produce binds to capture_blocked_by_setting`() {
        val reasons =
            setOf(
                PlatformCapabilityReason.LISTENER_ACCESS_NOT_GRANTED,
                PlatformCapabilityReason.RESTRICTED_SETTING_LOCKED,
            )
        reasons.forEach { assertEquals(it.name, "capture_blocked_by_setting", it.condition.id) }
    }
}

@RunWith(RobolectricTestRunner::class)
class AndroidInstallSourceReaderTest {
    private val context = ApplicationProvider.getApplicationContext<android.content.Context>()

    @Test
    fun `reads the installer the platform recorded`() {
        shadowOf(context.packageManager).setInstallSourceInfo(context.packageName, "com.android.shell", null)
        val snapshot = AndroidInstallSourceReader(context).read()
        assertEquals(InstallSource.ADB, InstallSourceClassifier.classify(snapshot))

        shadowOf(
            context.packageManager,
        ).setInstallSourceInfo(context.packageName, "com.android.vending", "com.android.vending")
        assertEquals(
            InstallSource.PLAY_STORE,
            InstallSourceClassifier.classify(AndroidInstallSourceReader(context).read()),
        )
    }

    @Test
    fun `listener access is read from the platform and is not granted by default`() {
        val listener = ComponentName(context, "com.pennilogic.android.capture.NotificationCaptureService")
        assertFalse(AndroidListenerAccessReader(context).isGranted(listener))
    }
}
