package com.pennilogic.android.platform.ui

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertLeftPositionInRootIsEqualTo
import androidx.compose.ui.test.assertWidthIsEqualTo
import androidx.compose.ui.test.getBoundsInRoot
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.width
import com.pennilogic.android.MainActivity
import com.pennilogic.android.ui.ScaffoldScreenTags
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The real entry activity on the Android 16 runtime, in both variants: its composition root is the
 * [RootSurface], the scaffold screen stays usable at compact, 600dp+ and 840dp+ widths in either
 * orientation, and an unhandled back finishes the activity without any legacy callback.
 */
@RunWith(RobolectricTestRunner::class)
class MainActivityLayoutTest {
    @get:Rule
    val compose = createAndroidComposeRule<MainActivity>()

    @Test
    fun `phone width renders the scaffold inside the root surface`() {
        assertLayoutFollowsWidthClass()
    }

    @Test
    @Config(qualifiers = "sw700dp-w700dp-h1000dp-port")
    fun `600dp portrait window stays usable and fills the width`() {
        assertLayoutFollowsWidthClass()
        assertEquals(WindowWidthClass.MEDIUM, WindowWidthClass.fromWidthDp(rootWidthDp()))
    }

    @Test
    @Config(qualifiers = "sw700dp-w1000dp-h700dp-land")
    fun `1000dp landscape window caps and centres the content`() {
        assertLayoutFollowsWidthClass()
        assertEquals(WindowWidthClass.EXPANDED, WindowWidthClass.fromWidthDp(rootWidthDp()))
        compose.onNodeWithTag(RootSurfaceTags.CONTENT).assertWidthIsEqualTo(840.dp)
        compose.onNodeWithTag(RootSurfaceTags.CONTENT).assertLeftPositionInRootIsEqualTo(80.dp)
    }

    @Test
    @Config(qualifiers = "sw800dp-w1280dp-h800dp-land")
    fun `tablet landscape window keeps the heading readable and centred`() {
        assertLayoutFollowsWidthClass()
        compose.onNodeWithTag(RootSurfaceTags.CONTENT).assertLeftPositionInRootIsEqualTo(220.dp)
    }

    @Test
    fun `unhandled back finishes the activity through the dispatcher`() {
        compose.runOnIdle {
            compose.activity.onBackPressedDispatcher.onBackPressed()
        }
        compose.runOnIdle {
            assertTrue(compose.activity.isFinishing)
        }
    }

    private fun assertLayoutFollowsWidthClass() {
        val root = compose.onRoot().getBoundsInRoot()
        compose.onNodeWithTag(RootSurfaceTags.SURFACE).assertWidthIsEqualTo(root.width)
        val insetArea = compose.onNodeWithTag(RootSurfaceTags.INSET_AREA).getBoundsInRoot()
        val content = compose.onNodeWithTag(RootSurfaceTags.CONTENT).getBoundsInRoot()
        assertTrue(
            "inset area within root: $insetArea in $root",
            insetArea.left >= root.left && insetArea.right <= root.right,
        )
        assertTrue(
            "content within inset area: $content in $insetArea",
            content.left >= insetArea.left && content.right <= insetArea.right,
        )
        val expectedContentWidth = WindowWidthClass.contentWidthDp(insetArea.width.value.toInt())
        assertEquals(expectedContentWidth.dp, content.width)
        compose.onNodeWithTag(ScaffoldScreenTags.HEADING).assertIsDisplayed()
        val heading = compose.onNodeWithTag(ScaffoldScreenTags.HEADING).getBoundsInRoot()
        assertTrue(
            "heading inside content: $heading in $content",
            heading.left >= content.left && heading.right <= content.right,
        )
    }

    private fun rootWidthDp(): Int =
        compose
            .onRoot()
            .getBoundsInRoot()
            .width.value
            .toInt()
}
