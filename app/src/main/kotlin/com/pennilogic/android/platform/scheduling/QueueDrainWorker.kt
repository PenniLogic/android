package com.pennilogic.android.platform.scheduling

import android.content.Context
import android.os.Build
import android.util.Log
import androidx.work.CoroutineWorker
import androidx.work.ListenableWorker
import androidx.work.WorkerParameters
import com.pennilogic.android.observability.LOG_TAG
import com.pennilogic.android.platform.capture.AndroidPlatformSignals
import com.pennilogic.android.platform.capture.CaptureHealthEvent
import com.pennilogic.android.platform.capture.PlatformSignals

/** Maps a drain outcome onto the result WorkManager expects; pure, so the mapping is unit tested. */
object WorkerResultMapper {
    fun map(outcome: DrainOutcome): ListenableWorker.Result =
        when (outcome) {
            is DrainOutcome.Drained -> {
                if (outcome.retryLater >
                    0
                ) {
                    ListenableWorker.Result.retry()
                } else {
                    ListenableWorker.Result.success()
                }
            }

            is DrainOutcome.Stopped -> {
                when (outcome.disposition) {
                    StopDisposition.NONE -> ListenableWorker.Result.success()

                    StopDisposition.DEFER_TO_SCHEDULER -> ListenableWorker.Result.retry()

                    StopDisposition.RETRY_WITH_BACKOFF -> ListenableWorker.Result.retry()

                    StopDisposition.RECORD_DEFECT -> ListenableWorker.Result.retry()

                    // Only the user can lift the restriction; the released items wait in the queue and
                    // the capture surface shows the pause, so the chain ends here without failing.
                    StopDisposition.PAUSE_UNTIL_USER_ACTION -> ListenableWorker.Result.success()

                    StopDisposition.CANCELLED -> ListenableWorker.Result.success()
                }
            }
        }

    /**
     * The stop reason WorkManager reports, in the shared vocabulary. Real reasons exist from API 31;
     * below that WorkManager returns `STOP_REASON_UNKNOWN`, which maps to [StopReason.UNKNOWN].
     */
    fun stopReason(
        isStopped: Boolean,
        platformStopReason: Int,
    ): StopReason? = if (!isStopped) null else StopReason.fromPlatform(platformStopReason)
}

/**
 * Base worker for every scheduled drain of a [DurableQueue]. Feature tickets subclass it with their
 * queue and sender; this class owns the stop-reason handling: the drainer polls the platform stop
 * reason, releases claimed items on a stop and the worker logs the `capture_health` event with the
 * stop reason and standby bucket (no content, no device identifier). Android 16's quota changes make
 * quota stops routine, and the queue semantics make them harmless.
 */
abstract class QueueDrainWorker(
    context: Context,
    parameters: WorkerParameters,
) : CoroutineWorker(context, parameters) {
    /** Builds the drainer for this run; [owner] is unique per worker run so leases are attributable. */
    protected abstract fun createDrainer(owner: String): QueueDrainer<*>

    protected open val signals: PlatformSignals by lazy { AndroidPlatformSignals(applicationContext) }

    final override suspend fun doWork(): Result {
        val drainer = createDrainer(owner = "work:$id:$runAttemptCount")
        val outcome = drainer.drain { WorkerResultMapper.stopReason(isStopped, currentPlatformStopReason()) }
        val (bucket, raw) = signals.standbyBucket()
        val stopReason = (outcome as? DrainOutcome.Stopped)?.reason
        Log.i(
            LOG_TAG,
            CaptureHealthEvent(
                apiLevel = signals.apiLevel,
                captureHealth = null,
                stopReason = stopReason,
                standbyBucket = bucket,
                rawStandbyBucket = raw,
            ).toJson(),
        )
        return WorkerResultMapper.map(outcome)
    }

    private fun currentPlatformStopReason(): Int =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) stopReason else StopReason.UNKNOWN.platformValue
}
