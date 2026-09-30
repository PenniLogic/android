package com.pennilogic.android.platform.ui

import androidx.compose.ui.test.assertTopPositionInRootIsEqualTo
import androidx.compose.ui.test.getBoundsInRoot
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.height
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.pennilogic.android.MainActivity
import com.pennilogic.android.ui.ScaffoldScreenTags
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Real insets on a real Android 16 window: the root surface draws behind the system bars and its
 * inset area starts exactly at the status bar and ends exactly at the navigation bar. Local
 * evidence only (`./gradlew connectedDebugAndroidTest` on the API 36 emulator); CI has no emulator.
 */
@RunWith(AndroidJUnit4::class)
class EdgeToEdgeInstrumentedTest {
    @get:Rule
    val compose = createAndroidComposeRule<MainActivity>()

    @Test
    fun rootSurfaceDrawsBehindTheBarsAndInsetsItsContent() {
        var systemBarsTopPx = 0
        var systemBarsBottomPx = 0
        var windowHeightPx = 0
        var density = 1f
        compose.runOnUiThread {
            val decorView = compose.activity.window.decorView
            val insets = checkNotNull(ViewCompat.getRootWindowInsets(decorView)) { "no root window insets" }
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout())
            systemBarsTopPx = bars.top
            systemBarsBottomPx = bars.bottom
            windowHeightPx = decorView.height
            density = compose.activity.resources.displayMetrics.density
        }
        assertTrue("the emulator must show a status bar for this test to mean anything", systemBarsTopPx > 0)

        val surface = compose.onNodeWithTag(RootSurfaceTags.SURFACE)
        surface.assertTopPositionInRootIsEqualTo(0.dp)
        val surfaceBounds = surface.getBoundsInRoot()
        assertEquals(
            "the surface spans the whole window, behind both bars",
            windowHeightPx / density,
            surfaceBounds.height.value,
            1f,
        )

        val insetArea = compose.onNodeWithTag(RootSurfaceTags.INSET_AREA).getBoundsInRoot()
        assertEquals("inset area starts at the status bar", systemBarsTopPx / density, insetArea.top.value, 1f)
        assertEquals(
            "inset area ends at the navigation bar",
            (windowHeightPx - systemBarsBottomPx) / density,
            insetArea.bottom.value,
            1f,
        )
        val heading = compose.onNodeWithTag(ScaffoldScreenTags.HEADING).getBoundsInRoot()
        assertTrue("heading is below the status bar: $heading", heading.top >= insetArea.top)
    }
}
