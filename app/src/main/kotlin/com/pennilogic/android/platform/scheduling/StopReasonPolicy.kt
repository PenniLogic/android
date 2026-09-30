package com.pennilogic.android.platform.scheduling

import com.pennilogic.android.platform.state.PlatformCapabilityReason

/** What a drain does after the platform stopped it, and what the capture surface shows. */
enum class StopDisposition(
    val id: String,
    val description: String,
) {
    /** Nothing to do: the work was not stopped. */
    NONE("none", "The work completed or was not stopped."),

    /** Release claimed items; the scheduler re-runs the work on its own when quota returns. */
    DEFER_TO_SCHEDULER(
        "defer_to_scheduler",
        "Release in-flight items and let the scheduler re-run the work when quota or constraints allow.",
    ),

    /** Release claimed items and ask for a backoff retry; the stop was transient. */
    RETRY_WITH_BACKOFF("retry_with_backoff", "Release in-flight items and retry with backoff."),

    /** Release claimed items and stop scheduling until the user lifts the restriction. */
    PAUSE_UNTIL_USER_ACTION(
        "pause_until_user_action",
        "Release in-flight items; only the user can lift the restriction.",
    ),

    /** Release claimed items; the app cancelled its own work, nothing is rescheduled here. */
    CANCELLED("cancelled", "Release in-flight items; the app cancelled the work itself."),

    /** Release claimed items and record a defect: the job was abandoned without finishing. */
    RECORD_DEFECT("record_defect", "Release in-flight items and record the abandoned job as a defect."),
}

/**
 * Stop-reason and standby-bucket policy shared by every scheduled drain. Whatever the reason, claimed
 * items are released, never dropped, so a quota stop can neither lose nor duplicate queued work;
 * the policy only decides what happens next and which capture-health reason, if any, the pause maps to.
 */
object StopReasonPolicy {
    fun disposition(reason: StopReason): StopDisposition =
        when (reason.category) {
            StopReason.Category.APP -> {
                StopDisposition.CANCELLED
            }

            StopReason.Category.TRANSIENT -> {
                StopDisposition.RETRY_WITH_BACKOFF
            }

            StopReason.Category.CONSTRAINT -> {
                StopDisposition.DEFER_TO_SCHEDULER
            }

            StopReason.Category.QUOTA -> {
                StopDisposition.DEFER_TO_SCHEDULER
            }

            StopReason.Category.USER_RESTRICTION -> {
                StopDisposition.PAUSE_UNTIL_USER_ACTION
            }

            StopReason.Category.DEFECT -> {
                StopDisposition.RECORD_DEFECT
            }

            StopReason.Category.OTHER -> {
                if (reason == StopReason.NOT_STOPPED) StopDisposition.NONE else StopDisposition.RETRY_WITH_BACKOFF
            }
        }

    /**
     * The capture-health reason a stop implies, or null when it implies none. A quota stop pauses
     * sync, which is the pending marker of the happy path in the taxonomy, not a capture pause; only
     * a user restriction or the restricted bucket pauses capture.
     */
    fun captureHealthReason(
        reason: StopReason,
        bucket: StandbyBucket,
    ): PlatformCapabilityReason? =
        when {
            reason == StopReason.BACKGROUND_RESTRICTION -> {
                PlatformCapabilityReason.BACKGROUND_RESTRICTED
            }

            reason == StopReason.APP_STANDBY && bucket.pausesCapture -> {
                PlatformCapabilityReason.STANDBY_BUCKET_RESTRICTED
            }

            bucket.pausesCapture -> {
                PlatformCapabilityReason.STANDBY_BUCKET_RESTRICTED
            }

            else -> {
                null
            }
        }
}
