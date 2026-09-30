package com.pennilogic.android.platform.ui

import androidx.compose.runtime.Composable
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalWindowInfo
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.pennilogic.android.platform.PlatformBaseline

/**
 * Width class of the current window, following the Material 3 breakpoints. On Android 16 a window
 * can be any width at any time on 600dp+ screens, because the platform ignores orientation,
 * resizability and aspect-ratio restrictions; every root surface therefore lays out by this class
 * and never by device type or orientation.
 */
enum class WindowWidthClass(
    /** Inclusive lower bound of the class, in dp. */
    val minWidthDp: Int,
    /** Widest readable single-pane content, or null when the content fills the window. */
    val contentMaxWidthDp: Int?,
    /** Horizontal margin between the window edge (after insets) and the content. */
    val horizontalMarginDp: Int,
) {
    COMPACT(minWidthDp = 0, contentMaxWidthDp = null, horizontalMarginDp = 16),
    MEDIUM(minWidthDp = PlatformBaseline.LARGE_SCREEN_MIN_WIDTH_DP, contentMaxWidthDp = null, horizontalMarginDp = 24),
    EXPANDED(minWidthDp = 840, contentMaxWidthDp = 840, horizontalMarginDp = 24),
    ;

    /** True from the width at which Android 16 ignores orientation and aspect-ratio restrictions. */
    val isLargeScreen: Boolean
        get() = minWidthDp >= PlatformBaseline.LARGE_SCREEN_MIN_WIDTH_DP

    companion object {
        /** Classifies a window width; negative widths (never laid out yet) are compact. */
        fun fromWidthDp(widthDp: Int): WindowWidthClass = entries.lastOrNull { widthDp >= it.minWidthDp } ?: COMPACT

        /**
         * Width the content actually gets inside a window of [windowWidthDp] after the class's margins
         * and the content cap are applied. Never negative.
         */
        fun contentWidthDp(windowWidthDp: Int): Int {
            val widthClass = fromWidthDp(windowWidthDp)
            val available = (windowWidthDp - 2 * widthClass.horizontalMarginDp).coerceAtLeast(0)
            return widthClass.contentMaxWidthDp?.let { minOf(available, it) } ?: available
        }
    }
}

/** The width class of the window hosting this composition, in dp, from the container size. */
@Composable
@ReadOnlyComposable
fun currentWindowWidthClass(): WindowWidthClass {
    val widthPx = LocalWindowInfo.current.containerSize.width
    val widthDp = with(LocalDensity.current) { widthPx.toDp() }
    return WindowWidthClass.fromWidthDp(widthDp.value.toInt())
}

/** Dp view of [WindowWidthClass.contentMaxWidthDp]; [Dp.Unspecified] when the content fills. */
val WindowWidthClass.contentMaxWidth: Dp
    get() = contentMaxWidthDp?.dp ?: Dp.Unspecified
