package com.pennilogic.android.config

import com.pennilogic.android.config.ConfigurationProblem.Kind
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ConfigurationLoaderTest {
    private val validUrl = "https://api.dev.pennilogic.invalid"

    @Test
    fun `loads a complete configuration and applies the build label default`() {
        val result =
            ConfigurationLoader.load(
                source(
                    ConfigurationKeys.ENVIRONMENT to "development",
                    ConfigurationKeys.API_BASE_URL to validUrl,
                ),
            )

        val configuration = assertLoaded(result)
        assertEquals(Environment.DEVELOPMENT, configuration.environment)
        assertEquals(validUrl, configuration.apiBaseUrl)
        assertEquals(ConfigurationLoader.DEFAULT_BUILD_LABEL, configuration.buildLabel)
    }

    @Test
    fun `missing required key fails and names the key`() {
        val result = ConfigurationLoader.load(source(ConfigurationKeys.ENVIRONMENT to "development"))

        assertProblems(result, ConfigurationProblem(ConfigurationKeys.API_BASE_URL, Kind.MISSING))
    }

    @Test
    fun `blank value counts as missing`() {
        val result =
            ConfigurationLoader.load(
                source(
                    ConfigurationKeys.ENVIRONMENT to "   ",
                    ConfigurationKeys.API_BASE_URL to validUrl,
                ),
            )

        assertProblems(result, ConfigurationProblem(ConfigurationKeys.ENVIRONMENT, Kind.MISSING))
    }

    @Test
    fun `reports every required key problem in one pass`() {
        val result = ConfigurationLoader.load(source())

        assertProblems(
            result,
            ConfigurationProblem(ConfigurationKeys.ENVIRONMENT, Kind.MISSING),
            ConfigurationProblem(ConfigurationKeys.API_BASE_URL, Kind.MISSING),
        )
    }

    @Test
    fun `unknown environment is invalid`() {
        val result =
            ConfigurationLoader.load(
                source(
                    ConfigurationKeys.ENVIRONMENT to "prod",
                    ConfigurationKeys.API_BASE_URL to validUrl,
                ),
            )

        assertProblems(result, ConfigurationProblem(ConfigurationKeys.ENVIRONMENT, Kind.INVALID))
    }

    @Test
    fun `every documented environment value parses`() {
        for (environment in Environment.entries) {
            assertEquals(environment, Environment.parse(environment.configurationValue))
        }
    }

    @Test
    fun `values are trimmed before parsing`() {
        val result =
            ConfigurationLoader.load(
                source(
                    ConfigurationKeys.ENVIRONMENT to " staging ",
                    ConfigurationKeys.API_BASE_URL to " $validUrl ",
                ),
            )

        assertEquals(Environment.STAGING, assertLoaded(result).environment)
    }

    @Test
    fun `cleartext api base url is rejected`() {
        val result =
            ConfigurationLoader.load(
                source(
                    ConfigurationKeys.ENVIRONMENT to "development",
                    ConfigurationKeys.API_BASE_URL to "http://api.dev.pennilogic.invalid",
                ),
            )

        assertProblems(result, ConfigurationProblem(ConfigurationKeys.API_BASE_URL, Kind.INVALID))
    }

    @Test
    fun `api base url must be absolute https with a host and nothing sensitive`() {
        val rejected =
            listOf(
                "https://",
                "https:///path",
                "/relative/path",
                "not a url",
                "ftp://files.pennilogic.invalid",
                "https://user:secret@api.pennilogic.invalid",
                "https://api.pennilogic.invalid/?token=1",
                "https://api.pennilogic.invalid/#fragment",
            )
        for (candidate in rejected) {
            assertEquals("expected rejection of $candidate", null, ConfigurationLoader.parseHttpsUrl(candidate))
        }

        val accepted = listOf(validUrl, "$validUrl/", "$validUrl/v1", "HTTPS://api.pennilogic.invalid:8443/v1")
        for (candidate in accepted) {
            assertEquals(candidate, ConfigurationLoader.parseHttpsUrl(candidate))
        }
    }

    @Test
    fun `optional build label is used when valid`() {
        val result =
            ConfigurationLoader.load(
                source(
                    ConfigurationKeys.ENVIRONMENT to "production",
                    ConfigurationKeys.API_BASE_URL to validUrl,
                    ConfigurationKeys.BUILD_LABEL to "ci-42-abc123",
                ),
            )

        assertEquals("ci-42-abc123", assertLoaded(result).buildLabel)
    }

    @Test
    fun `build label longer than the limit or outside printable ascii is invalid`() {
        val tooLong = "x".repeat(ConfigurationLoader.MAX_BUILD_LABEL_LENGTH + 1)
        val atLimit = "x".repeat(ConfigurationLoader.MAX_BUILD_LABEL_LENGTH)

        assertEquals(atLimit, ConfigurationLoader.parseBuildLabel(atLimit))
        assertEquals(null, ConfigurationLoader.parseBuildLabel(tooLong))
        assertEquals(null, ConfigurationLoader.parseBuildLabel("build\nlabel"))
        assertEquals(null, ConfigurationLoader.parseBuildLabel("bü1ld"))

        val result =
            ConfigurationLoader.load(
                source(
                    ConfigurationKeys.ENVIRONMENT to "production",
                    ConfigurationKeys.API_BASE_URL to validUrl,
                    ConfigurationKeys.BUILD_LABEL to tooLong,
                ),
            )
        assertProblems(result, ConfigurationProblem(ConfigurationKeys.BUILD_LABEL, Kind.INVALID))
    }

    @Test
    fun `problem descriptions carry the key and kind but never the value`() {
        val problem = ConfigurationProblem(ConfigurationKeys.API_BASE_URL, Kind.INVALID)

        assertEquals("PENNILOGIC_API_BASE_URL:invalid", problem.description)
        assertFalse(problem.description.contains("http"))
    }

    @Test
    fun `required key set matches the documented contract`() {
        assertEquals(setOf("PENNILOGIC_ENVIRONMENT", "PENNILOGIC_API_BASE_URL"), ConfigurationKeys.required)
        assertEquals(
            listOf("PENNILOGIC_ENVIRONMENT", "PENNILOGIC_API_BASE_URL", "PENNILOGIC_BUILD_LABEL"),
            ConfigurationKeys.all,
        )
    }

    private fun source(vararg values: Pair<String, String?>): ConfigurationSource {
        val map = mapOf(*values)
        return ConfigurationSource { key -> map[key] }
    }

    private fun assertLoaded(result: ConfigurationResult): AppConfiguration {
        assertTrue("expected Loaded but was $result", result is ConfigurationResult.Loaded)
        return (result as ConfigurationResult.Loaded).configuration
    }

    private fun assertProblems(
        result: ConfigurationResult,
        vararg expected: ConfigurationProblem,
    ) {
        assertTrue("expected Invalid but was $result", result is ConfigurationResult.Invalid)
        assertEquals(expected.toList(), (result as ConfigurationResult.Invalid).problems)
    }
}
