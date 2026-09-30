package com.pennilogic.android.platform.ui

import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.assert
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertTextEquals
import androidx.compose.ui.test.isHeading
import androidx.compose.ui.test.junit4.ComposeTestRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import com.pennilogic.android.BuildConfig
import com.pennilogic.android.ui.ScaffoldScreenTags

/**
 * The accessibility contract of the scaffold screen, asserted in CI at every window size and on
 * every QA API level: the app name is the one heading, and each label/value pair is a single merged
 * node so a screen reader announces "Build, debug 0.1.0-debug" as one item.
 */
object ScaffoldAccessibility {
    fun assertContract(compose: ComposeTestRule) {
        compose.onNodeWithTag(ScaffoldScreenTags.HEADING).assertIsDisplayed()
        compose
            .onNodeWithTag(
                ScaffoldScreenTags.HEADING,
            ).assert(SemanticsMatcher.keyIsDefined(SemanticsProperties.Heading))
        compose.onAllNodes(isHeading()).assertCountEquals(1)
        compose
            .onNodeWithText("Build", substring = false)
            .assertTextEquals("Build", "${BuildConfig.BUILD_TYPE} ${BuildConfig.VERSION_NAME}")
    }
}
