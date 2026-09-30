package com.pennilogic.android.platform.scheduling

import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.withContext
import kotlin.coroutines.cancellation.CancellationException

/** Result of one drain run. */
sealed interface DrainOutcome {
    val sent: Int
    val deadLettered: Int

    /** Every eligible item was sent or parked as a dead letter (payload retained); nothing is pending for now. */
    data class Drained(
        override val sent: Int,
        override val deadLettered: Int,
        /** Items released for a later retry because the server asked for one. */
        val retryLater: Int,
    ) : DrainOutcome

    /** The platform stopped the drain; every claimed item was released, none was dropped. */
    data class Stopped(
        val reason: StopReason,
        val disposition: StopDisposition,
        override val sent: Int,
        override val deadLettered: Int,
        val released: Int,
    ) : DrainOutcome
}

/**
 * Drains a [DurableQueue] in batches, surviving quota stops and crashes without losing or duplicating
 * items.
 *
 * The invariants, proven by `QueueDrainerTest`: an item is sent only while this drainer holds its
 * lease; a sent item is acknowledged only after the sender confirmed it; a sender that throws is a
 * retry with backoff, never a lost item or a failed run; a stop signal gives every claimed-but-unsent
 * item back through [DurableQueue.release] before returning, which counts no attempt, so any number
 * of platform stops can never bring an item closer to the attempt limit — only a completed send the
 * server asked to retry does ([DurableQueue.retryLater]); coroutine cancellation (the scheduler
 * stopping the worker) gives the claimed items back the same way, under `NonCancellable`; a crash at
 * any point leaves items leased, and the next drain returns them to pending through
 * [DurableQueue.expireLeases] and re-sends them under the same idempotency key, which the server
 * deduplicates; an item the server rejects for good, or that fails [maxAttempts] sends, is parked as a
 * dead letter with its payload, never deleted. The drainer never reads the payload.
 */
class QueueDrainer<T>(
    private val queue: DurableQueue<T>,
    private val sender: suspend (QueuedItem<T>) -> SendResult,
    private val owner: String,
    private val clock: () -> Long,
    private val batchSize: Int = DEFAULT_BATCH_SIZE,
    private val leaseMillis: Long = DEFAULT_LEASE_MILLIS,
    private val maxAttempts: Int = DEFAULT_MAX_ATTEMPTS,
    private val backoffMillis: (attempts: Int) -> Long = ::exponentialBackoffMillis,
) {
    init {
        require(batchSize > 0) { "batchSize must be positive" }
        require(leaseMillis > 0) { "leaseMillis must be positive" }
        require(maxAttempts > 0) { "maxAttempts must be positive" }
    }

    /**
     * Drains until the queue has nothing eligible or [stopReason] reports a platform stop. The stop
     * signal is polled before every claim and before every send, so at most the item being sent when
     * the signal arrives is finished before the drain returns.
     */
    suspend fun drain(stopReason: () -> StopReason?): DrainOutcome {
        queue.expireLeases(clock())
        var sent = 0
        var deadLettered = 0
        var retryLater = 0
        while (true) {
            stopReason()?.let { return stopped(it, sent, deadLettered, released = 0) }
            val batch = queue.claim(owner, batchSize, clock(), leaseMillis)
            if (batch.isEmpty()) return DrainOutcome.Drained(sent, deadLettered, retryLater)
            for ((index, item) in batch.withIndex()) {
                val stop = stopReason()
                if (stop != null) {
                    // The give-back must complete even if the scheduler cancels the coroutine meanwhile.
                    val released = withContext(NonCancellable) { releaseAll(batch.drop(index)) }
                    return stopped(stop, sent, deadLettered, released)
                }
                val result =
                    try {
                        sender(item)
                    } catch (cancelled: CancellationException) {
                        // The scheduler stopped the worker mid-send: give every claimed item back. The
                        // releases must not be skipped by the very cancellation that triggered them.
                        withContext(NonCancellable) { releaseAll(batch.drop(index)) }
                        throw cancelled
                    } catch (failure: Exception) {
                        // A sender that throws (network, serialization) is a retry, never a lost item
                        // and never a failed chain; whether the item reached the server is unknown, so
                        // the same key is re-sent and the server deduplicates.
                        SendResult.Retry()
                    }
                when (result) {
                    SendResult.Sent -> {
                        queue.ack(item.key, owner)
                        sent++
                    }

                    is SendResult.Retry -> {
                        // This send completed and failed: it counts. Stops and expiries never reach here.
                        if (item.attempts + 1 >= maxAttempts) {
                            queue.deadLetter(item.key, owner, MAX_ATTEMPTS_REASON, clock())
                            deadLettered++
                        } else {
                            queue.retryLater(
                                item.key,
                                owner,
                                result.notBeforeMillis ?: clock() + backoffMillis(item.attempts + 1),
                            )
                            retryLater++
                        }
                    }

                    is SendResult.Rejected -> {
                        queue.deadLetter(item.key, owner, result.reason, clock())
                        deadLettered++
                    }
                }
            }
        }
    }

    /** Gives unsent items back: no attempt is counted, so a stop never spends the attempt budget. */
    private suspend fun releaseAll(items: List<QueuedItem<T>>): Int {
        var released = 0
        for (item in items) {
            if (queue.release(item.key, owner)) released++
        }
        return released
    }

    private fun stopped(
        reason: StopReason,
        sent: Int,
        deadLettered: Int,
        released: Int,
    ) = DrainOutcome.Stopped(reason, StopReasonPolicy.disposition(reason), sent, deadLettered, released)

    companion object {
        const val DEFAULT_BATCH_SIZE: Int = 20
        const val DEFAULT_LEASE_MILLIS: Long = 60_000
        const val DEFAULT_MAX_ATTEMPTS: Int = 8

        /** Dead-letter reason when [maxAttempts] completed sends failed; an identifier like every reason. */
        const val MAX_ATTEMPTS_REASON: String = "max_attempts"

        /** 30 s, 60 s, 120 s, ... capped at one hour; deterministic so tests can assert it. */
        fun exponentialBackoffMillis(attempts: Int): Long {
            val base = 30_000L
            val shift = (attempts - 1).coerceIn(0, 7)
            return minOf(base shl shift, 3_600_000L)
        }
    }
}
