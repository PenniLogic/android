package com.pennilogic.android.platform.capture

import com.pennilogic.android.observability.StartupEvent
import com.pennilogic.android.platform.PlatformBaseline
import com.pennilogic.android.platform.scheduling.StandbyBucket
import com.pennilogic.android.platform.scheduling.StopReason
import com.pennilogic.android.platform.state.CaptureHealthCondition
import com.pennilogic.android.platform.state.ClientState
import com.pennilogic.android.platform.state.PermissionDeniedCause
import com.pennilogic.android.platform.state.PlatformCapabilityReason

/**
 * Health of the automatic-capture pipeline, as published in
 * `docs/platform/capture-health-identifiers.json`. It is not a taxonomy state: [Healthy] renders as
 * content, [Paused] renders through `capture_paused_by_platform` (`degraded`) and [Blocked] through
 * `capture_blocked_by_setting` (`permission_denied`, cause `device`). No value carries transaction,
 * message or device content.
 */
sealed interface CaptureHealth {
    val id: String
    val condition: CaptureHealthCondition?
    val reason: PlatformCapabilityReason?

    /** The taxonomy state a surface renders, or null for content. */
    val clientState: ClientState?
        get() = condition?.bindsTo

    data object Healthy : CaptureHealth {
        override val id: String = "healthy"
        override val condition: CaptureHealthCondition? = null
        override val reason: PlatformCapabilityReason? = null
    }

    /** Tracking paused for a platform reason; stays until capture health is restored, never on a mere start. */
    data class Paused(
        override val reason: PlatformCapabilityReason,
        val sinceMillis: Long,
    ) : CaptureHealth {
        init {
            require(reason.condition == CaptureHealthCondition.CAPTURE_PAUSED_BY_PLATFORM) {
                "${reason.id} is not a pause reason"
            }
        }

        override val id: String = "paused"
        override val condition: CaptureHealthCondition = CaptureHealthCondition.CAPTURE_PAUSED_BY_PLATFORM
    }

    /** Capture blocked by a permission or restricted setting; [permission] is the platform's name for it. */
    data class Blocked(
        override val reason: PlatformCapabilityReason,
        val permission: String?,
    ) : CaptureHealth {
        init {
            require(reason.condition == CaptureHealthCondition.CAPTURE_BLOCKED_BY_SETTING) {
                "${reason.id} is not a blocking reason"
            }
        }

        override val id: String = "blocked"
        override val condition: CaptureHealthCondition = CaptureHealthCondition.CAPTURE_BLOCKED_BY_SETTING
        val cause: PermissionDeniedCause = PermissionDeniedCause.DEVICE
    }
}

/**
 * The structured `capture_health` log event: API level, stop reason, standby bucket and capture-health
 * state, exactly as the ticket's observability line asks, and nothing that identifies the device,
 * a transaction, a message or an account. Single-line JSON with a fixed key order.
 */
data class CaptureHealthEvent(
    val apiLevel: Int,
    /** Null for a drain event, which knows the stop reason and bucket but not the capture pipeline. */
    val captureHealth: CaptureHealth?,
    val stopReason: StopReason? = null,
    val standbyBucket: StandbyBucket? = null,
    /** Raw bucket value when the platform reported a bucket this baseline does not name. */
    val rawStandbyBucket: Int? = null,
    val targetApi: Int = PlatformBaseline.TARGET_API,
) {
    fun toJson(): String =
        buildString {
            append('{')
            append("\"event\":").append(StartupEvent.quote(EVENT_NAME)).append(',')
            append("\"api_level\":").append(apiLevel).append(',')
            append("\"target_api\":").append(targetApi).append(',')
            append("\"stop_reason\":").append(stopReason?.let { StartupEvent.quote(it.id) } ?: "null").append(',')
            append("\"standby_bucket\":").append(standbyBucket?.let { StartupEvent.quote(it.id) } ?: "null").append(',')
            append("\"standby_bucket_raw\":").append(rawStandbyBucket?.toString() ?: "null").append(',')
            append("\"capture_health\":").append(captureHealth?.let { StartupEvent.quote(it.id) } ?: "null").append(',')
            append(
                "\"condition\":",
            ).append(captureHealth?.condition?.let { StartupEvent.quote(it.id) } ?: "null").append(',')
            append("\"reason\":").append(captureHealth?.reason?.let { StartupEvent.quote(it.id) } ?: "null")
            append('}')
        }

    companion object {
        const val EVENT_NAME: String = "capture_health"
    }
}
