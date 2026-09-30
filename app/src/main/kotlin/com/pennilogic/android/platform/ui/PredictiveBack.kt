package com.pennilogic.android.platform.ui

import androidx.activity.compose.PredictiveBackHandler
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.rememberUpdatedState
import kotlin.coroutines.cancellation.CancellationException

/**
 * Progress of an in-flight predictive back gesture, forwarded to the screen so it can preview the
 * destination. [progress] runs from 0 to 1; [swipeEdge] is `BackEventCompat.EDGE_LEFT` or
 * `EDGE_RIGHT`; the touch position is in the window's coordinate space.
 */
@Immutable
data class BackGestureProgress(
    val progress: Float,
    val swipeEdge: Int,
    val touchX: Float,
    val touchY: Float,
)

/**
 * The only way a screen intercepts back navigation.
 *
 * Android 16 no longer calls `onBackPressed()` nor dispatches `KEYCODE_BACK` to apps targeting API
 * 36, and the manifest opts the whole application into predictive back. This wrapper over
 * `PredictiveBackHandler` registers with the activity's `OnBackPressedDispatcher`, which is bridged to
 * the platform `OnBackInvokedDispatcher` on API 33+: while a gesture is in flight [onProgress]
 * receives its progress, a cancelled gesture clears it with `null`, and a committed gesture clears it
 * and then calls [onBack]. When [enabled] is false nothing is registered, so the system back-to-home
 * animation runs untouched.
 */
@Composable
fun PenniLogicBackHandler(
    enabled: Boolean = true,
    onProgress: (BackGestureProgress?) -> Unit = {},
    onBack: () -> Unit,
) {
    val currentOnProgress by rememberUpdatedState(onProgress)
    val currentOnBack by rememberUpdatedState(onBack)
    PredictiveBackHandler(enabled = enabled) { progress ->
        try {
            progress.collect { event ->
                currentOnProgress(BackGestureProgress(event.progress, event.swipeEdge, event.touchX, event.touchY))
            }
        } catch (cancelled: CancellationException) {
            currentOnProgress(null)
            throw cancelled
        }
        currentOnProgress(null)
        currentOnBack()
    }
}
