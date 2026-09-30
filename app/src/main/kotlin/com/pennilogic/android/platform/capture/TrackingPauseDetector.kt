package com.pennilogic.android.platform.capture

import com.pennilogic.android.platform.state.PlatformCapabilityReason

/**
 * Decides, from the platform's signals at process start, whether tracking is paused and why. The
 * order is deliberate: a force-stop or private-space stop is the strongest signal because the
 * platform has already torn the capture components down; a user background restriction and the
 * restricted standby bucket come next. Detection never clears a pause; only
 * [CaptureHealthMonitor.onHealthRestored] does.
 */
class TrackingPauseDetector(
    private val signals: PlatformSignals,
) {
    fun detect(): PlatformCapabilityReason? {
        val stoppedByPlatform =
            signals.wasForceStopped() == true ||
                signals.lastExitReason() == AndroidPlatformSignals.REASON_USER_REQUESTED ||
                signals.lastExitReason() == AndroidPlatformSignals.REASON_USER_STOPPED
        return when {
            stoppedByPlatform && signals.profileKind() == ProfileKind.OTHER_PROFILE -> {
                PlatformCapabilityReason.PRIVATE_SPACE_PAUSED
            }

            stoppedByPlatform -> {
                PlatformCapabilityReason.FORCE_STOPPED
            }

            signals.isBackgroundRestricted() -> {
                PlatformCapabilityReason.BACKGROUND_RESTRICTED
            }

            signals.standbyBucket().first.pausesCapture -> {
                PlatformCapabilityReason.STANDBY_BUCKET_RESTRICTED
            }

            else -> {
                null
            }
        }
    }
}
