package com.pennilogic.android.platform.ui

import com.pennilogic.android.platform.PlatformBaseline
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class WindowWidthClassTest {
    @Test
    fun `breakpoints follow material 3 and the android 16 large-screen threshold`() {
        assertEquals(WindowWidthClass.COMPACT, WindowWidthClass.fromWidthDp(0))
        assertEquals(WindowWidthClass.COMPACT, WindowWidthClass.fromWidthDp(-1))
        assertEquals(WindowWidthClass.COMPACT, WindowWidthClass.fromWidthDp(599))
        assertEquals(WindowWidthClass.MEDIUM, WindowWidthClass.fromWidthDp(600))
        assertEquals(WindowWidthClass.MEDIUM, WindowWidthClass.fromWidthDp(839))
        assertEquals(WindowWidthClass.EXPANDED, WindowWidthClass.fromWidthDp(840))
        assertEquals(WindowWidthClass.EXPANDED, WindowWidthClass.fromWidthDp(2560))
        assertEquals(PlatformBaseline.LARGE_SCREEN_MIN_WIDTH_DP, WindowWidthClass.MEDIUM.minWidthDp)
    }

    @Test
    fun `large-screen classes are the ones the platform may resize freely`() {
        assertFalse(WindowWidthClass.COMPACT.isLargeScreen)
        assertTrue(WindowWidthClass.MEDIUM.isLargeScreen)
        assertTrue(WindowWidthClass.EXPANDED.isLargeScreen)
    }

    @Test
    fun `content fills compact and medium windows and is capped on expanded ones`() {
        assertEquals(360 - 32, WindowWidthClass.contentWidthDp(360))
        assertEquals(700 - 48, WindowWidthClass.contentWidthDp(700))
        assertEquals(839 - 48, WindowWidthClass.contentWidthDp(839))
        assertEquals(840 - 48, WindowWidthClass.contentWidthDp(840))
        assertEquals(840, WindowWidthClass.contentWidthDp(1000))
        assertEquals(840, WindowWidthClass.contentWidthDp(2560))
    }

    @Test
    fun `content width is never negative and never wider than the window`() {
        for (width in listOf(0, 1, 15, 16, 31, 32, 33, 599, 600, 601, 839, 840, 841, 1200, 4000)) {
            val content = WindowWidthClass.contentWidthDp(width)
            assertTrue("width $width gave $content", content >= 0)
            assertTrue("width $width gave $content", content <= width)
        }
    }
}
