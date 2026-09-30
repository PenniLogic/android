package com.pennilogic.android.platform.ui

import androidx.activity.ComponentActivity
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.assertLeftPositionInRootIsEqualTo
import androidx.compose.ui.test.assertTopPositionInRootIsEqualTo
import androidx.compose.ui.test.assertWidthIsEqualTo
import androidx.compose.ui.test.getBoundsInRoot
import androidx.compose.ui.test.junit4.AndroidComposeTestRule
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.width
import androidx.test.ext.junit.rules.ActivityScenarioRule
import com.pennilogic.android.BuildConfig
import org.junit.Assert.assertEquals
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TestRule
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Proves the [RootSurface] primitive with injected insets and window sizes on the Android 16
 * runtime. Synthetic compositions need the Compose test manifest, which only the debug variant
 * merges, so the release variant skips this class; `MainActivityLayoutTest` covers both variants.
 */
@RunWith(RobolectricTestRunner::class)
class RootSurfaceTest {
    private val compose: AndroidComposeTestRule<ActivityScenarioRule<ComponentActivity>, ComponentActivity>? =
        if (BuildConfig.DEBUG) createAndroidComposeRule<ComponentActivity>() else null

    @get:Rule
    val rule: TestRule = compose ?: TestRule { base, _ -> base }

    @Before
    fun requireTestManifest() {
        assumeTrue("synthetic compositions need the debug test manifest", compose != null)
    }

    @Test
    fun `injected insets become padding of the inset area and nothing else`() {
        val compose = checkNotNull(compose)
        compose.setContent {
            RootSurface(
                insets = WindowInsets(left = 10.dp, top = 48.dp, right = 10.dp, bottom = 24.dp),
                widthClass = WindowWidthClass.COMPACT,
            ) {
                Box(Modifier.fillMaxSize().testTag("child"))
            }
        }
        val root = compose.onRoot().getBoundsInRoot()
        compose.onNodeWithTag(RootSurfaceTags.SURFACE).assertWidthIsEqualTo(root.width)
        compose.onNodeWithTag(RootSurfaceTags.SURFACE).assertTopPositionInRootIsEqualTo(0.dp)
        val insetArea = compose.onNodeWithTag(RootSurfaceTags.INSET_AREA)
        insetArea.assertTopPositionInRootIsEqualTo(48.dp)
        insetArea.assertLeftPositionInRootIsEqualTo(10.dp)
        insetArea.assertWidthIsEqualTo(root.width - 20.dp)
        val insetBounds = insetArea.getBoundsInRoot()
        assertEquals("bottom inset not applied: $insetBounds vs $root", root.bottom - 24.dp, insetBounds.bottom)
        val content = compose.onNodeWithTag(RootSurfaceTags.CONTENT)
        val margin = WindowWidthClass.COMPACT.horizontalMarginDp.dp
        content.assertLeftPositionInRootIsEqualTo(10.dp + margin)
        content.assertWidthIsEqualTo(root.width - 20.dp - margin - margin)
        compose.onNodeWithTag("child").assertTopPositionInRootIsEqualTo(48.dp)
    }

    @Test
    @Config(qualifiers = "w1000dp-h700dp-land")
    fun `expanded windows cap the content at 840dp and centre it`() {
        val compose = checkNotNull(compose)
        compose.setContent { RootSurface(insets = WindowInsets(0.dp)) { Box(Modifier.fillMaxSize()) } }
        val root = compose.onRoot().getBoundsInRoot()
        assertEquals("unexpected root width", 1000.dp, root.width)
        compose.onNodeWithTag(RootSurfaceTags.CONTENT).assertWidthIsEqualTo(840.dp)
        compose.onNodeWithTag(RootSurfaceTags.CONTENT).assertLeftPositionInRootIsEqualTo(80.dp)
    }

    @Test
    @Config(qualifiers = "w700dp-h500dp-land")
    fun `medium windows fill the width inside 24dp margins`() {
        val compose = checkNotNull(compose)
        compose.setContent { RootSurface(insets = WindowInsets(0.dp)) { Box(Modifier.fillMaxSize()) } }
        compose.onNodeWithTag(RootSurfaceTags.CONTENT).assertWidthIsEqualTo(700.dp - 48.dp)
        compose.onNodeWithTag(RootSurfaceTags.CONTENT).assertLeftPositionInRootIsEqualTo(24.dp)
    }

    @Test
    @Config(qualifiers = "w360dp-h780dp")
    fun `compact windows fill the width inside 16dp margins`() {
        val compose = checkNotNull(compose)
        compose.setContent { RootSurface(insets = WindowInsets(0.dp)) { Box(Modifier.fillMaxSize()) } }
        compose.onNodeWithTag(RootSurfaceTags.CONTENT).assertWidthIsEqualTo(360.dp - 32.dp)
        compose.onNodeWithTag(RootSurfaceTags.CONTENT).assertLeftPositionInRootIsEqualTo(16.dp)
    }
}
