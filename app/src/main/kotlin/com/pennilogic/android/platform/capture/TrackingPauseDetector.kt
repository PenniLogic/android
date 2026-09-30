package com.pennilogic.android.platform.capture

import com.pennilogic.android.platform.state.PlatformCapabilityReason

/**
 * Decides, from the platform's signals at process start, whether tracking is paused and why. The
 * order is deliberate: a force-stop or private-space stop is the strongest signal because the
 * platform has already torn the capture components down; a user background restriction and the
 * restricted standby bucket come next. Detection never clears a pause; only
 * [CaptureHealthMonitor.onHealthRestored] does.
 *
 * What counts as a stop, and what deliberately does not:
 * - `ApplicationStartInfo.wasForceStopped()` (API 35+) is the platform's own answer and decides the
 *   force-stop case alone: `true` is a force-stop, `false` is not, whatever the last exit reason says.
 * - `ApplicationExitInfo.REASON_USER_STOPPED` (API 30+) means the user profile the app ran in was
 *   stopped — the private space was locked or a work profile turned off — which tears the components
 *   down the same way; it is honoured on every level that reports it.
 * - `ApplicationExitInfo.REASON_USER_REQUESTED` is **not** used: Android documents it for a force-stop
 *   *or* a swipe from Recents (and, before API 34, an app update), the public SDK exposes no
 *   sub-reason, and a swipe leaves receivers and jobs intact. Reading it as a force-stop would show
 *   "tracking paused" after every swipe. So on API 30–34 a force-stop is not detectable with public
 *   APIs; like below API 30, capture health is re-probed at start instead. The behaviour matrix and
 *   the identifiers document record this limitation.
 */
class TrackingPauseDetector(
    private val signals: PlatformSignals,
) {
    fun detect(): PlatformCapabilityReason? {
        val forceStopped = signals.wasForceStopped() == true
        val profileStopped = signals.lastExitReason() == AndroidPlatformSignals.REASON_USER_STOPPED
        val stoppedByPlatform = forceStopped || profileStopped
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
