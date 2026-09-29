package com.pennilogic.android.ui

import com.pennilogic.android.config.ConfigurationResult

/** Presentation state of the scaffold screen, derived from build metadata and configuration. */
data class ScaffoldScreenState(
    val buildType: String,
    val versionName: String,
    val environment: String?,
    val buildLabel: String?,
    /** `KEY:kind` descriptions of configuration problems; empty when configuration loaded. */
    val problems: List<String>,
) {
    val isConfigured: Boolean
        get() = problems.isEmpty()

    companion object {
        fun from(
            configuration: ConfigurationResult,
            buildType: String,
            versionName: String,
        ): ScaffoldScreenState =
            when (configuration) {
                is ConfigurationResult.Loaded -> {
                    ScaffoldScreenState(
                        buildType = buildType,
                        versionName = versionName,
                        environment = configuration.configuration.environment.configurationValue,
                        buildLabel = configuration.configuration.buildLabel,
                        problems = emptyList(),
                    )
                }

                is ConfigurationResult.Invalid -> {
                    ScaffoldScreenState(
                        buildType = buildType,
                        versionName = versionName,
                        environment = null,
                        buildLabel = null,
                        problems = configuration.problems.map { it.description },
                    )
                }
            }
    }
}
