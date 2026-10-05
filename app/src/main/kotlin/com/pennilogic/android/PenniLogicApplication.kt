package com.pennilogic.android

import android.app.Application
import com.pennilogic.android.config.BuildConfigSource
import com.pennilogic.android.config.ConfigurationLoader
import com.pennilogic.android.config.ConfigurationResult
import com.pennilogic.android.observability.PrivacyEventLogger
import com.pennilogic.android.observability.StartupEvent

/** Process entry point: loads the build configuration once and logs the structured start event. */
class PenniLogicApplication : Application() {
    /** Outcome of configuration loading, read by the entry activity to render its state. */
    lateinit var configuration: ConfigurationResult
        private set

    override fun onCreate() {
        super.onCreate()
        configuration = ConfigurationLoader.load(BuildConfigSource)
        val event =
            StartupEvent.forProcessStart(
                buildType = BuildConfig.BUILD_TYPE,
                versionName = BuildConfig.VERSION_NAME,
                versionCode = BuildConfig.VERSION_CODE,
                configuration = configuration,
            )
        PrivacyEventLogger.log(resources, event.toJson())
    }
}
