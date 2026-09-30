package com.pennilogic.android.platform.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp

/** Semantics test tags of the [RootSurface] layers, shared by JVM and instrumented tests. */
object RootSurfaceTags {
    /** The full-window surface that draws behind the system bars. */
    const val SURFACE: String = "pennilogic.root_surface"

    /** The window minus the applied insets; nothing interactive is placed outside it. */
    const val INSET_AREA: String = "pennilogic.root_inset_area"

    /** The content area after the width-class margins and content cap. */
    const val CONTENT: String = "pennilogic.root_content"
}

/**
 * The mandatory root of every screen.
 *
 * Android 16 draws every app edge-to-edge with no opt-out, so insets are applied exactly once, here,
 * and screens never consume window insets themselves: [insets] (safe drawing by default: system
 * bars, display cutout and keyboard) become padding of the inset area, and the content is laid out
 * by the [WindowWidthClass] of the window rather than by orientation or device type, so 600dp+
 * windows stay readable when the platform ignores orientation and aspect-ratio restrictions.
 *
 * `RootSurfaceUsageTest` fails the build when a `setContent` root does not go through this
 * composable; `RootSurfaceLayoutTest` and the instrumented tests prove the inset and width behaviour.
 */
@Composable
fun RootSurface(
    modifier: Modifier = Modifier,
    insets: WindowInsets = WindowInsets.safeDrawing,
    widthClass: WindowWidthClass = currentWindowWidthClass(),
    content: @Composable BoxScope.() -> Unit,
) {
    Surface(
        modifier = modifier.fillMaxSize().testTag(RootSurfaceTags.SURFACE),
        color = MaterialTheme.colorScheme.background,
    ) {
        Box(
            modifier =
                Modifier
                    .fillMaxSize()
                    .windowInsetsPadding(insets)
                    .testTag(RootSurfaceTags.INSET_AREA),
        ) {
            Box(
                modifier =
                    Modifier
                        .align(Alignment.TopCenter)
                        .fillMaxHeight()
                        .padding(horizontal = widthClass.horizontalMarginDp.dp)
                        .widthIn(max = widthClass.contentMaxWidth)
                        .fillMaxWidth()
                        .testTag(RootSurfaceTags.CONTENT),
                content = content,
            )
        }
    }
}
