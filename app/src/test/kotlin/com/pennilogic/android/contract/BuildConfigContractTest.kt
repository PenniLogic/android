package com.pennilogic.android.contract

import com.pennilogic.android.BuildConfig
import com.pennilogic.android.config.BuildConfigSource
import com.pennilogic.android.config.ConfigurationKeys
import com.pennilogic.android.config.ConfigurationLoader
import com.pennilogic.android.config.ConfigurationProblem
import com.pennilogic.android.config.ConfigurationResult
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeFalse
import org.junit.Assume.assumeTrue
import org.junit.Test

/**
 * Checks the values Gradle baked into this variant's BuildConfig against the key contract. The
 * debug variant must load with its documented defaults; the release variant must carry no hidden
 * defaults, so without environment variables it reports the required keys as missing.
 */
class BuildConfigContractTest {
    @Test
    fun `debug build configuration satisfies the contract`() {
        assumeTrue("debug variant only", BuildConfig.DEBUG)

        val result = ConfigurationLoader.load(BuildConfigSource)

        // Passes with the documented defaults and with any valid environment override; a misconfigured
        // build environment (for example a cleartext API URL) fails the unit test gate here.
        assertTrue("debug BuildConfig must satisfy the contract: $result", result is ConfigurationResult.Loaded)
    }

    @Test
    fun `release build has no hidden defaults for required keys`() {
        assumeFalse("release variant only", BuildConfig.DEBUG)

        when (val result = ConfigurationLoader.load(BuildConfigSource)) {
            // The build environment supplied every required key.
            is ConfigurationResult.Loaded -> {
                assertTrue(result.configuration.apiBaseUrl.startsWith("https://"))
            }

            is ConfigurationResult.Invalid -> {
                // Descriptions are KEY:kind only, so naming them here cannot leak a configuration value.
                val described = result.problems.joinToString { it.description }
                val kinds = result.problems.map { it.kind }.toSet()
                assertEquals(
                    "release BuildConfig may only be missing required keys, but reported: $described",
                    setOf(ConfigurationProblem.Kind.MISSING),
                    kinds,
                )
                assertTrue(
                    "release BuildConfig reported a problem outside the required keys: $described",
                    result.problems.all { it.key in ConfigurationKeys.required },
                )
            }
        }
    }

    @Test
    fun `application id and version name follow the variant matrix`() {
        if (BuildConfig.DEBUG) {
            assertEquals("com.pennilogic.android.debug", BuildConfig.APPLICATION_ID)
            assertTrue(BuildConfig.VERSION_NAME.endsWith("-debug"))
        } else {
            assertEquals("com.pennilogic.android", BuildConfig.APPLICATION_ID)
            assertEquals("0.1.0", BuildConfig.VERSION_NAME)
        }
        assertEquals(1, BuildConfig.VERSION_CODE)
    }

    @Test
    fun `build label always has a printable default`() {
        assertEquals(BuildConfig.BUILD_LABEL, ConfigurationLoader.parseBuildLabel(BuildConfig.BUILD_LABEL))
    }
}
