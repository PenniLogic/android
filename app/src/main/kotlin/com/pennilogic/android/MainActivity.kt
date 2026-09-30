package com.pennilogic.android

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.pennilogic.android.platform.ui.RootSurface
import com.pennilogic.android.ui.ScaffoldScreen
import com.pennilogic.android.ui.ScaffoldScreenState
import com.pennilogic.android.ui.theme.PenniLogicTheme

/**
 * Entry activity: renders the build and configuration state of the scaffold.
 *
 * Back navigation is left to the platform: there is no `onBackPressed()` override and no
 * `KEYCODE_BACK` handling, so on API 36 the predictive back-to-home animation runs; a screen that
 * needs to intercept back uses `PenniLogicBackHandler`.
 */
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Edge-to-edge is mandatory on API 35+; calling this keeps API 26-34 consistent with it.
        enableEdgeToEdge()
        val state =
            ScaffoldScreenState.from(
                configuration = (application as PenniLogicApplication).configuration,
                buildType = BuildConfig.BUILD_TYPE,
                versionName = BuildConfig.VERSION_NAME,
            )
        setContent {
            PenniLogicTheme {
                RootSurface {
                    ScaffoldScreen(state)
                }
            }
        }
    }
}
