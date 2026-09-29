package com.pennilogic.android.config

import com.pennilogic.android.BuildConfig

/** Exposes the values Gradle baked into `BuildConfig` from the build environment. */
object BuildConfigSource : ConfigurationSource {
    private val values: Map<String, String> =
        mapOf(
            ConfigurationKeys.ENVIRONMENT to BuildConfig.ENVIRONMENT,
            ConfigurationKeys.API_BASE_URL to BuildConfig.API_BASE_URL,
            ConfigurationKeys.BUILD_LABEL to BuildConfig.BUILD_LABEL,
        )

    override fun value(key: String): String? = values[key]
}
