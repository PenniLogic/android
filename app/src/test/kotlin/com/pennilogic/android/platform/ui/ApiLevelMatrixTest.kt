package com.pennilogic.android.platform.ui

import android.os.Build
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithTag
import com.pennilogic.android.MainActivity
import com.pennilogic.android.platform.PlatformBaseline
import com.pennilogic.android.ui.ScaffoldScreenTags
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The T-QA-12 compatibility matrix on the JVM: the entry activity, its root surface and the
 * dispatcher-based back path work on the Android 12, 13, 15 and 16 runtimes (API 31, 33, 35, 36).
 * Robolectric runs each test once per declared SDK, in both variants, in CI.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [31, 33, 35, 36])
class ApiLevelMatrixTest {
    @get:Rule
    val compose = createAndroidComposeRule<MainActivity>()

    @Test
    fun `scaffold renders inside the root surface on every qa api level`() {
        assertTrue(
            "runtime ${Build.VERSION.SDK_INT} is a QA level",
            Build.VERSION.SDK_INT in PlatformBaseline.QA_API_LEVELS,
        )
        compose.onNodeWithTag(RootSurfaceTags.SURFACE).assertIsDisplayed()
        compose.onNodeWithTag(RootSurfaceTags.INSET_AREA).assertIsDisplayed()
        compose.onNodeWithTag(RootSurfaceTags.CONTENT).assertIsDisplayed()
        compose.onNodeWithTag(ScaffoldScreenTags.HEADING).assertIsDisplayed()
    }

    @Test
    fun `unhandled back finishes the activity on every qa api level`() {
        compose.runOnIdle { compose.activity.onBackPressedDispatcher.onBackPressed() }
        compose.runOnIdle { assertTrue(compose.activity.isFinishing) }
    }
}
