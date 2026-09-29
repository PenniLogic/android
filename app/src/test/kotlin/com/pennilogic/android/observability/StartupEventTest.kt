package com.pennilogic.android.observability

import com.pennilogic.android.config.AppConfiguration
import com.pennilogic.android.config.ConfigurationProblem
import com.pennilogic.android.config.ConfigurationProblem.Kind
import com.pennilogic.android.config.ConfigurationResult
import com.pennilogic.android.config.Environment
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class StartupEventTest {
    @Test
    fun `loaded configuration produces a single line json event`() {
        val event =
            StartupEvent.forProcessStart(
                buildType = "debug",
                versionName = "0.1.0-debug",
                versionCode = 1,
                configuration =
                    ConfigurationResult.Loaded(
                        AppConfiguration(Environment.DEVELOPMENT, "https://api.dev.pennilogic.invalid", "local"),
                    ),
            )

        assertEquals(
            """{"event":"app_start","build_type":"debug","version_name":"0.1.0-debug","version_code":1,""" +
                """"configuration":"loaded","problems":[]}""",
            event.toJson(),
        )
        assertFalse(event.toJson().contains('\n'))
    }

    @Test
    fun `invalid configuration lists problem descriptions`() {
        val event =
            StartupEvent.forProcessStart(
                buildType = "release",
                versionName = "0.1.0",
                versionCode = 1,
                configuration =
                    ConfigurationResult.Invalid(
                        listOf(
                            ConfigurationProblem("PENNILOGIC_ENVIRONMENT", Kind.MISSING),
                            ConfigurationProblem("PENNILOGIC_API_BASE_URL", Kind.INVALID),
                        ),
                    ),
            )

        assertEquals(
            """{"event":"app_start","build_type":"release","version_name":"0.1.0","version_code":1,""" +
                """"configuration":"invalid","problems":["PENNILOGIC_ENVIRONMENT:missing","PENNILOGIC_API_BASE_URL:invalid"]}""",
            event.toJson(),
        )
    }

    @Test
    fun `event never contains configuration values`() {
        val apiBaseUrl = "https://very-distinctive-host.pennilogic.invalid"
        val buildLabel = "distinctive-build-label"
        val event =
            StartupEvent.forProcessStart(
                buildType = "debug",
                versionName = "0.1.0-debug",
                versionCode = 1,
                configuration =
                    ConfigurationResult.Loaded(AppConfiguration(Environment.PRODUCTION, apiBaseUrl, buildLabel)),
            )

        val json = event.toJson()
        assertFalse(json.contains(apiBaseUrl))
        assertFalse(json.contains(buildLabel))
        assertFalse(json.contains(Environment.PRODUCTION.configurationValue))
    }

    @Test
    fun `strings are escaped as json`() {
        assertEquals("\"plain\"", StartupEvent.quote("plain"))
        assertEquals("\"quote\\\"back\\\\slash\"", StartupEvent.quote("quote\"back\\slash"))
        assertEquals("\"line\\nfeed\\ttab\\rreturn\"", StartupEvent.quote("line\nfeed\ttab\rreturn"))
        assertEquals("\"\\u0001\"", StartupEvent.quote("\u0001"))
        assertEquals("\"ünïcödé\"", StartupEvent.quote("ünïcödé"))
    }

    @Test
    fun `hostile build metadata cannot break the json structure`() {
        val event =
            StartupEvent(
                buildType = "debug\",\"injected\":\"value",
                versionName = "0.1.0",
                versionCode = 1,
                configurationStatus = StartupEvent.STATUS_LOADED,
                configurationProblems = emptyList(),
            )

        assertEquals(
            """{"event":"app_start","build_type":"debug\",\"injected\":\"value","version_name":"0.1.0",""" +
                """"version_code":1,"configuration":"loaded","problems":[]}""",
            event.toJson(),
        )
    }
}
