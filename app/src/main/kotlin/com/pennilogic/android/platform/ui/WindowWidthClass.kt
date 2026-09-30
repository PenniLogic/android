package com.pennilogic.android.platform.ui

import androidx.compose.runtime.Composable
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalWindowInfo
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.pennilogic.android.platform.PlatformBaseline

/**
 * Width class of the usable width, following the Material 3 breakpoints at 600dp and 840dp. On
 * Android 16 a window can be any width at any time on 600dp+ screens, because the platform ignores
 * orientation, resizability and aspect-ratio restrictions; every root surface therefore lays out by
 * this class and never by device type or orientation. [RootSurface] derives it from the width
 * **after insets** (the inset area), so a cutout or hinge at a class boundary cannot make margins and
 * content width disagree.
 *
 * The enum deliberately stops at [EXPANDED]: Material 3's newer `large` (≥1200dp) and `extra-large`
 * (≥1600dp) classes matter for multi-pane layouts, which no PenniLogic surface has yet; the design
 * system ticket (`T-DSY-01`) extends the enum when a pane layout needs them.
 *
 * [contentMaxWidthDp] is a **layout cap for a single pane**, not a readable measure: at Material's
 * `bodyLarge` a full 840dp line holds roughly 110 characters, and medium windows are not capped at
 * all. Line length for prose (the 45–75 character measure) is a typography decision that belongs to
 * the design system (`T-DSY-01`) and its text components, which this baseline does not pre-empt.
 */
enum class WindowWidthClass(
    /** Inclusive lower bound of the class, in dp. */
    val minWidthDp: Int,
    /** Widest single pane, or null when the content fills the usable width. */
    val contentMaxWidthDp: Int?,
    /** Horizontal margin between the inset area's edge and the content. */
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
        /** Classifies a usable width; negative widths (never laid out yet) are compact. */
        fun fromWidthDp(widthDp: Int): WindowWidthClass = entries.lastOrNull { widthDp >= it.minWidthDp } ?: COMPACT

        /**
         * Width the content actually gets inside a usable width of [usableWidthDp] after the class's
         * margins and the pane cap are applied. Never negative.
         */
        fun contentWidthDp(usableWidthDp: Int): Int {
            val widthClass = fromWidthDp(usableWidthDp)
            val available = (usableWidthDp - 2 * widthClass.horizontalMarginDp).coerceAtLeast(0)
            return widthClass.contentMaxWidthDp?.let { minOf(available, it) } ?: available
        }
    }
}

/**
 * The width class of the **whole window** hosting this composition (insets included), from the
 * container size. Use it for decisions about the window itself; [RootSurface] does not use it for
 * layout, because it classifies the inset area instead.
 */
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
