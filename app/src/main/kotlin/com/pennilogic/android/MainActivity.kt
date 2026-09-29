package com.pennilogic.android

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.pennilogic.android.ui.ScaffoldScreen
import com.pennilogic.android.ui.ScaffoldScreenState
import com.pennilogic.android.ui.theme.PenniLogicTheme

/** Entry activity: renders the build and configuration state of the scaffold. */
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val state =
            ScaffoldScreenState.from(
                configuration = (application as PenniLogicApplication).configuration,
                buildType = BuildConfig.BUILD_TYPE,
                versionName = BuildConfig.VERSION_NAME,
            )
        setContent {
            PenniLogicTheme {
                ScaffoldScreen(state)
            }
        }
    }
}
