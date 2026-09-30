package com.pennilogic.android.platform.capture

import android.content.Context
import android.content.SharedPreferences
import androidx.core.content.edit
import com.pennilogic.android.platform.state.CaptureHealthCondition
import com.pennilogic.android.platform.state.PlatformCapabilityReason

/**
 * Persists the capture-health record across process starts. Only identifiers and timestamps are
 * stored: a pause reason, when it began, whether the capture components were registered since, and a
 * blocking reason with its platform permission name. Never transaction, message or device content.
 */
interface CaptureHealthStore {
    fun read(): CaptureHealthRecord

    fun write(record: CaptureHealthRecord)
}

/** The stored record; every field is an identifier or a timestamp. */
data class CaptureHealthRecord(
    val pauseReason: PlatformCapabilityReason? = null,
    val pausedSinceMillis: Long? = null,
    /** True once the capture components were registered in the current pause; a probe may then clear it. */
    val componentsRegistered: Boolean = false,
    val blockReason: PlatformCapabilityReason? = null,
    val blockPermission: String? = null,
)

class InMemoryCaptureHealthStore(
    initial: CaptureHealthRecord = CaptureHealthRecord(),
) : CaptureHealthStore {
    private var record = initial

    override fun read(): CaptureHealthRecord = record

    override fun write(record: CaptureHealthRecord) {
        this.record = record
    }
}

/** SharedPreferences-backed store; a plain file is appropriate because the record holds no content worth protecting. */
class PreferencesCaptureHealthStore(
    context: Context,
) : CaptureHealthStore {
    private val preferences: SharedPreferences = context.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    override fun read(): CaptureHealthRecord =
        CaptureHealthRecord(
            pauseReason = preferences.getString(KEY_PAUSE_REASON, null)?.let(PlatformCapabilityReason::fromId),
            pausedSinceMillis = preferences.getLong(KEY_PAUSED_SINCE, -1).takeIf { it >= 0 },
            componentsRegistered = preferences.getBoolean(KEY_COMPONENTS_REGISTERED, false),
            blockReason = preferences.getString(KEY_BLOCK_REASON, null)?.let(PlatformCapabilityReason::fromId),
            blockPermission = preferences.getString(KEY_BLOCK_PERMISSION, null),
        )

    override fun write(record: CaptureHealthRecord) {
        preferences.edit {
            putString(KEY_PAUSE_REASON, record.pauseReason?.id)
            putLong(KEY_PAUSED_SINCE, record.pausedSinceMillis ?: -1)
            putBoolean(KEY_COMPONENTS_REGISTERED, record.componentsRegistered)
            putString(KEY_BLOCK_REASON, record.blockReason?.id)
            putString(KEY_BLOCK_PERMISSION, record.blockPermission)
        }
    }

    private companion object {
        const val FILE = "pennilogic.capture_health"
        const val KEY_PAUSE_REASON = "pause_reason"
        const val KEY_PAUSED_SINCE = "paused_since"
        const val KEY_COMPONENTS_REGISTERED = "components_registered"
        const val KEY_BLOCK_REASON = "block_reason"
        const val KEY_BLOCK_PERMISSION = "block_permission"
    }
}

/**
 * The capture-health state machine.
 *
 * - [onProcessStart] records a pause the detector reports. A start on its own never clears a pause:
 *   after a force-stop the surface shows tracking paused until [onHealthRestored] is reached.
 * - [onCaptureComponentsRegistered] records that the capture components exist again in this process;
 *   [onHealthProbeSucceeded] then clears the pause. A probe before registration is ignored, because a
 *   healthy-looking probe from a process whose receivers are not registered proves nothing.
 * - [onBlockedBySetting] / [onSettingGranted] track the permission / restricted-setting side, which
 *   the taxonomy renders ahead of a pause (`permission_denied` precedes `degraded`).
 *
 * Every transition returns the resulting [CaptureHealth] and the monitor can render the observability
 * event for it. Transitions are serialized on the monitor, so lifecycle callbacks and workers may call
 * it from any thread. The monitor keeps no transaction content.
 */
class CaptureHealthMonitor(
    private val store: CaptureHealthStore,
    private val detector: TrackingPauseDetector,
    private val signals: PlatformSignals,
    private val clock: () -> Long,
) {
    @Synchronized
    fun current(): CaptureHealth = store.read().toHealth()

    @Synchronized
    fun onProcessStart(): CaptureHealth {
        val record = store.read()
        val detected = detector.detect()
        val updated =
            when {
                detected != null && record.pauseReason == null -> {
                    record.copy(pauseReason = detected, pausedSinceMillis = clock(), componentsRegistered = false)
                }

                // A new platform reason while already paused replaces the reason; the pause continues and
                // any registration from the previous process is void.
                detected != null -> {
                    record.copy(pauseReason = detected, componentsRegistered = false)
                }

                else -> {
                    record.copy(componentsRegistered = false)
                }
            }
        store.write(updated)
        return updated.toHealth()
    }

    @Synchronized
    fun onCaptureComponentsRegistered(): CaptureHealth {
        val record = store.read()
        store.write(record.copy(componentsRegistered = true))
        return current()
    }

    /** Clears a pause only when the components are registered; otherwise the pause stays. */
    @Synchronized
    fun onHealthProbeSucceeded(): CaptureHealth {
        val record = store.read()
        if (record.pauseReason != null && record.componentsRegistered) {
            store.write(record.copy(pauseReason = null, pausedSinceMillis = null))
        }
        return current()
    }

    /** Registration followed by a successful probe, in one step. */
    @Synchronized
    fun onHealthRestored(): CaptureHealth {
        onCaptureComponentsRegistered()
        return onHealthProbeSucceeded()
    }

    @Synchronized
    fun onBlockedBySetting(
        reason: PlatformCapabilityReason,
        permission: String?,
    ): CaptureHealth {
        require(reason.condition == CaptureHealthCondition.CAPTURE_BLOCKED_BY_SETTING) {
            "${reason.id} is not a blocking reason"
        }
        store.write(store.read().copy(blockReason = reason, blockPermission = permission))
        return current()
    }

    @Synchronized
    fun onSettingGranted(): CaptureHealth {
        store.write(store.read().copy(blockReason = null, blockPermission = null))
        return current()
    }

    /** The observability event for the current state, with the platform's standby bucket attached. */
    @Synchronized
    fun event(): CaptureHealthEvent {
        val (bucket, raw) = signals.standbyBucket()
        return CaptureHealthEvent(
            apiLevel = signals.apiLevel,
            captureHealth = current(),
            standbyBucket = bucket,
            rawStandbyBucket = raw,
        )
    }

    private fun CaptureHealthRecord.toHealth(): CaptureHealth =
        when {
            blockReason != null -> CaptureHealth.Blocked(blockReason, blockPermission)
            pauseReason != null -> CaptureHealth.Paused(pauseReason, pausedSinceMillis ?: 0L)
            else -> CaptureHealth.Healthy
        }
}
