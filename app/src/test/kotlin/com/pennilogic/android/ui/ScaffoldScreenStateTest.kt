package com.pennilogic.android.ui

import com.pennilogic.android.config.AppConfiguration
import com.pennilogic.android.config.ConfigurationProblem
import com.pennilogic.android.config.ConfigurationProblem.Kind
import com.pennilogic.android.config.ConfigurationResult
import com.pennilogic.android.config.Environment
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ScaffoldScreenStateTest {
    @Test
    fun `loaded configuration shows environment and build label`() {
        val state =
            ScaffoldScreenState.from(
                configuration =
                    ConfigurationResult.Loaded(
                        AppConfiguration(Environment.STAGING, "https://api.staging.pennilogic.invalid", "ci-7"),
                    ),
                buildType = "debug",
                versionName = "0.1.0-debug",
            )

        assertEquals(
            ScaffoldScreenState(
                buildType = "debug",
                versionName = "0.1.0-debug",
                environment = "staging",
                buildLabel = "ci-7",
                problems = emptyList(),
            ),
            state,
        )
        assertTrue(state.isConfigured)
    }

    @Test
    fun `invalid configuration hides values and lists problems`() {
        val state =
            ScaffoldScreenState.from(
                configuration =
                    ConfigurationResult.Invalid(
                        listOf(ConfigurationProblem("PENNILOGIC_API_BASE_URL", Kind.INVALID)),
                    ),
                buildType = "release",
                versionName = "0.1.0",
            )

        assertEquals(null, state.environment)
        assertEquals(null, state.buildLabel)
        assertEquals(listOf("PENNILOGIC_API_BASE_URL:invalid"), state.problems)
        assertFalse(state.isConfigured)
    }

    @Test
    fun `screen state never exposes the api base url`() {
        val apiBaseUrl = "https://distinctive.pennilogic.invalid"
        val state =
            ScaffoldScreenState.from(
                configuration =
                    ConfigurationResult.Loaded(AppConfiguration(Environment.PRODUCTION, apiBaseUrl, "local")),
                buildType = "release",
                versionName = "0.1.0",
            )

        assertFalse(state.toString().contains(apiBaseUrl))
    }
}
