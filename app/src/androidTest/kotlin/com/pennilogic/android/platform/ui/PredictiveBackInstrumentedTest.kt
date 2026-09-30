package com.pennilogic.android.platform.ui

import androidx.activity.ComponentActivity
import androidx.compose.foundation.layout.Box
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.lifecycle.Lifecycle
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.UiDevice
import com.pennilogic.android.MainActivity
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * The platform back path on Android 16, where `onBackPressed()` is never called and `KEYCODE_BACK`
 * is not dispatched to an app targeting API 36: the system back finishes the unhandled scaffold
 * activity, and a screen that registers [PenniLogicBackHandler] intercepts it through the platform
 * `OnBackInvokedDispatcher` bridge. Local evidence only (API 36 emulator).
 */
@RunWith(AndroidJUnit4::class)
class PredictiveBackInstrumentedTest {
    @get:Rule
    val compose = createAndroidComposeRule<ComponentActivity>()

    private val device: UiDevice = UiDevice.getInstance(InstrumentationRegistry.getInstrumentation())

    @Test
    fun systemBackFinishesTheUnhandledScaffoldActivity() {
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            assertEquals(Lifecycle.State.RESUMED, scenario.state)
            device.pressBack()
            device.waitForIdle()
            val deadline = System.currentTimeMillis() + 5_000
            while (scenario.state != Lifecycle.State.DESTROYED && System.currentTimeMillis() < deadline) {
                Thread.sleep(50)
            }
            assertEquals(
                "system back must finish the activity without a legacy callback",
                Lifecycle.State.DESTROYED,
                scenario.state,
            )
        }
    }

    @Test
    fun handlerInterceptsSystemBackThroughThePlatformDispatcher() {
        var backs = 0
        compose.setContent {
            PenniLogicBackHandler(onBack = { backs++ })
            Box {}
        }
        compose.waitForIdle()
        device.pressBack()
        device.waitForIdle()
        compose.waitUntil(5_000) { backs == 1 }
        compose.runOnIdle {
            assertEquals(1, backs)
            assertFalse("an intercepted back must not finish the activity", compose.activity.isFinishing)
        }
    }

    @Test
    fun disabledHandlerLetsSystemBackFinishTheActivity() {
        compose.setContent {
            PenniLogicBackHandler(enabled = false, onBack = { error("must not be called") })
            Box {}
        }
        compose.waitForIdle()
        // Captured before back, because the rule refuses to hand out a destroyed activity.
        val activity = compose.activity
        device.pressBack()
        device.waitForIdle()
        val deadline = System.currentTimeMillis() + 5_000
        while (!(activity.isFinishing || activity.isDestroyed) && System.currentTimeMillis() < deadline) {
            Thread.sleep(50)
        }
        assertTrue(activity.isFinishing || activity.isDestroyed)
    }
}
