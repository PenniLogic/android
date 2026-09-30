package com.pennilogic.android.platform.ui

import androidx.activity.BackEventCompat
import androidx.activity.ComponentActivity
import androidx.compose.foundation.layout.Box
import androidx.compose.ui.test.junit4.AndroidComposeTestRule
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.test.ext.junit.rules.ActivityScenarioRule
import com.pennilogic.android.BuildConfig
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TestRule
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

/**
 * Drives the [PenniLogicBackHandler] through the activity's `OnBackPressedDispatcher`, which is the
 * path the platform's predictive back dispatcher feeds on API 33+. Debug variant only, like every
 * synthetic composition test; `MainActivityLayoutTest` proves the unhandled case in both variants.
 */
@RunWith(RobolectricTestRunner::class)
class PenniLogicBackHandlerTest {
    private val compose: AndroidComposeTestRule<ActivityScenarioRule<ComponentActivity>, ComponentActivity>? =
        if (BuildConfig.DEBUG) createAndroidComposeRule<ComponentActivity>() else null

    @get:Rule
    val rule: TestRule = compose ?: TestRule { base, _ -> base }

    @Before
    fun requireTestManifest() {
        assumeTrue("synthetic compositions need the debug test manifest", compose != null)
    }

    @Test
    fun `an enabled handler receives the committed back and the activity stays alive`() {
        val compose = checkNotNull(compose)
        var backs = 0
        val progress = mutableListOf<BackGestureProgress?>()
        compose.setContent {
            PenniLogicBackHandler(enabled = true, onProgress = { progress += it }, onBack = { backs++ })
            Box {}
        }
        compose.runOnIdle {
            compose.activity.onBackPressedDispatcher.onBackPressed()
        }
        compose.runOnIdle {
            assertEquals(1, backs)
            assertEquals(listOf<BackGestureProgress?>(null), progress)
            assertFalse(compose.activity.isFinishing)
        }
    }

    @Test
    fun `gesture progress is forwarded, cleared on cancel and followed by onBack on commit`() {
        val compose = checkNotNull(compose)
        var backs = 0
        val progress = mutableListOf<BackGestureProgress?>()
        compose.setContent {
            PenniLogicBackHandler(enabled = true, onProgress = { progress += it }, onBack = { backs++ })
            Box {}
        }
        val dispatcher = compose.activity.onBackPressedDispatcher
        // Each dispatch gets its own idle point, as gesture events do on a device.
        compose.runOnIdle { dispatcher.dispatchOnBackStarted(BackEventCompat(1f, 2f, 0f, BackEventCompat.EDGE_LEFT)) }
        compose.runOnIdle {
            dispatcher.dispatchOnBackProgressed(
                BackEventCompat(30f, 2f, 0.4f, BackEventCompat.EDGE_LEFT),
            )
        }
        compose.runOnIdle { dispatcher.dispatchOnBackCancelled() }
        compose.runOnIdle {
            assertEquals(0, backs)
            assertEquals(0.4f, checkNotNull(progress.dropLast(1).last()).progress)
            assertEquals(BackEventCompat.EDGE_LEFT, checkNotNull(progress.dropLast(1).last()).swipeEdge)
            assertNull("a cancelled gesture clears the progress", progress.last())
        }
        progress.clear()
        compose.runOnIdle { dispatcher.dispatchOnBackStarted(BackEventCompat(1f, 2f, 0f, BackEventCompat.EDGE_RIGHT)) }
        compose.runOnIdle {
            dispatcher.dispatchOnBackProgressed(
                BackEventCompat(60f, 2f, 0.9f, BackEventCompat.EDGE_RIGHT),
            )
        }
        compose.runOnIdle { dispatcher.onBackPressed() }
        compose.runOnIdle {
            assertEquals(1, backs)
            assertEquals(0.9f, checkNotNull(progress.dropLast(1).last()).progress)
            assertNull("a committed gesture clears the progress before onBack", progress.last())
            assertFalse(compose.activity.isFinishing)
        }
    }

    @Test
    fun `a disabled handler registers nothing so back falls through to the platform`() {
        val compose = checkNotNull(compose)
        var backs = 0
        compose.setContent {
            PenniLogicBackHandler(enabled = false, onBack = { backs++ })
            Box {}
        }
        compose.runOnIdle {
            compose.activity.onBackPressedDispatcher.onBackPressed()
        }
        compose.runOnIdle {
            assertEquals(0, backs)
            assertTrue("unhandled back must finish the activity", compose.activity.isFinishing)
        }
    }
}
