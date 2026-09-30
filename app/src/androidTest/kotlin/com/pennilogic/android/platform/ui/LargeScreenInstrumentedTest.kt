package com.pennilogic.android.platform.ui

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.getBoundsInRoot
import androidx.compose.ui.test.junit4.v2.createEmptyComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.unit.width
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.UiDevice
import com.pennilogic.android.MainActivity
import com.pennilogic.android.ui.ScaffoldScreenTags
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Large-screen behaviour on Android 16, where the platform ignores orientation, resizability and
 * aspect-ratio restrictions on 600dp+ displays: the display is resized with `wm size` / `wm density`
 * (shell commands the test is allowed to run), the scaffold is launched into it and must lay out by
 * width class. The display is reset afterwards. Local evidence only (API 36 emulator).
 */
@RunWith(AndroidJUnit4::class)
class LargeScreenInstrumentedTest {
    @get:Rule
    val compose = createEmptyComposeRule()

    private val device: UiDevice = UiDevice.getInstance(InstrumentationRegistry.getInstrumentation())

    @After
    fun resetDisplay() {
        device.executeShellCommand("wm size reset")
        device.executeShellCommand("wm density reset")
        settle()
    }

    @Test
    fun expandedLandscapeWindowCapsAndCentresTheContent() {
        // 2560x1600 px at 320 dpi = 1280x800 dp: an expanded, landscape window.
        resize(widthPx = 2560, heightPx = 1600, dpi = 320)
        ActivityScenario.launch(MainActivity::class.java).use {
            compose.waitForIdle()
            val root = compose.onRoot().getBoundsInRoot()
            assertTrue("window is a large screen: $root", root.width.value >= 1200f)
            val content = compose.onNodeWithTag(RootSurfaceTags.CONTENT).getBoundsInRoot()
            assertEquals(840f, content.width.value, 1f)
            assertEquals("content is centred", (root.width.value - content.width.value) / 2f, content.left.value, 2f)
            compose.onNodeWithTag(ScaffoldScreenTags.HEADING).assertIsDisplayed()
        }
    }

    @Test
    fun mediumPortraitWindowFillsTheWidthAndStaysUsable() {
        // 1200x2000 px at 320 dpi = 600x1000 dp: the smallest large-screen width, in portrait.
        resize(widthPx = 1200, heightPx = 2000, dpi = 320)
        ActivityScenario.launch(MainActivity::class.java).use {
            compose.waitForIdle()
            val root = compose.onRoot().getBoundsInRoot()
            assertTrue("window is at least 600dp wide: $root", root.width.value >= 600f)
            val insetArea = compose.onNodeWithTag(RootSurfaceTags.INSET_AREA).getBoundsInRoot()
            val content = compose.onNodeWithTag(RootSurfaceTags.CONTENT).getBoundsInRoot()
            val expected = WindowWidthClass.contentWidthDp(insetArea.width.value.toInt())
            assertEquals(expected.toFloat(), content.width.value, 1f)
            compose.onNodeWithTag(ScaffoldScreenTags.HEADING).assertIsDisplayed()
        }
    }

    private fun resize(
        widthPx: Int,
        heightPx: Int,
        dpi: Int,
    ) {
        device.executeShellCommand("wm size ${widthPx}x$heightPx")
        device.executeShellCommand("wm density $dpi")
        settle()
    }

    /** Display reconfiguration restarts System UI surfaces; give the headless emulator time to finish. */
    private fun settle() {
        device.waitForIdle()
        Thread.sleep(2_500)
        device.waitForIdle()
    }
}
