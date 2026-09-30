package com.pennilogic.android.platform.scheduling

import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * One queued item. [key] is the idempotency key the server deduplicates on; the payload is opaque
 * to the queue, which never interprets amounts, currencies or content.
 */
data class QueuedItem<T>(
    val key: String,
    val payload: T,
    val enqueuedAtMillis: Long,
    /**
     * Completed send attempts that failed before this one ([DurableQueue.retryLater]). A platform
     * stop, a cancellation or a lease expiry gives an item back without a send having failed, so none
     * of them counts here; only a sender's retry outcome does.
     */
    val attempts: Int,
    /**
     * Leases that outlived their owner ([DurableQueue.expireLeases]): a crash or a stop mid-send. Kept
     * apart from [attempts] because the item may never have reached the server; a poison-item cap on
     * this counter is the feature ticket's policy, not the queue's.
     */
    val leaseExpiries: Int = 0,
)

/** Outcome of one send attempt, as the sender reports it. */
sealed interface SendResult {
    /** The server accepted (or idempotently re-accepted) the item. */
    data object Sent : SendResult

    /** Try again later; [notBeforeMillis] overrides the drainer's backoff when the server said when. */
    data class Retry(
        val notBeforeMillis: Long? = null,
    ) : SendResult

    /**
     * The server rejected the item for good; it is dead-lettered with the reason, never retried.
     * [reason] is an identifier ([DurableQueue.REASON]), never free server text, so no content can
     * enter local diagnostics through it.
     */
    data class Rejected(
        val reason: String,
    ) : SendResult {
        init {
            require(DurableQueue.REASON.matches(reason)) { "rejection reason must be an identifier" }
        }
    }
}

/**
 * A dead-lettered item: parked, not deleted. The item keeps its payload, key, attempt counts and
 * timestamps so an operator or QA can inspect, re-queue or export it; [DurableQueue.enqueue] still
 * refuses its key.
 */
data class DeadLetter<T>(
    val item: QueuedItem<T>,
    val reason: String,
    val deadLetteredAtMillis: Long,
)

/** Keys by state; the queue's own view, used by tests and diagnostics. */
data class QueueSnapshot(
    val pending: Set<String>,
    val inFlight: Set<String>,
    val deadLettered: Set<String>,
) {
    val isEmpty: Boolean
        get() = pending.isEmpty() && inFlight.isEmpty()
}

/**
 * Durable, crash-safe queue contract for locally queued writes awaiting sync.
 *
 * Semantics every implementation (the in-memory one here, a Room-backed one in the storage ticket)
 * must keep, and `DurableQueueContractTest` asserts: an item exists at most once per [QueuedItem.key];
 * [claim] leases items to one owner for a bounded time so two drains never send the same item
 * concurrently; [ack], [release], [retryLater] and [deadLetter] are accepted only from the owner that
 * holds the lease; [release] gives back an item that was not sent and counts nothing, [retryLater]
 * records a failed send attempt, so platform stops can never exhaust the attempt limit; a lease that
 * outlives its owner (crash, quota stop mid-send) is returned to pending by [expireLeases], which
 * counts a lease expiry and not an attempt, so a crash at any point loses nothing and the same key is
 * re-sent, which the server deduplicates; a dead-lettered item is retained with its payload, never
 * deleted. Every operation is atomic: it either fully applies or leaves the queue unchanged.
 */
interface DurableQueue<T> {
    /** Adds an item; false when the key is already queued, in flight or dead-lettered (never a second copy). */
    suspend fun enqueue(
        key: String,
        payload: T,
        nowMillis: Long,
    ): Boolean

    /** Leases up to [limit] pending items whose retry time has come to [owner] until now + [leaseMillis]. */
    suspend fun claim(
        owner: String,
        limit: Int,
        nowMillis: Long,
        leaseMillis: Long,
    ): List<QueuedItem<T>>

    /** Removes a sent item; false when [owner] does not hold its lease (a late ack after takeover). */
    suspend fun ack(
        key: String,
        owner: String,
    ): Boolean

    /**
     * Gives back an item that was **not sent** (platform stop, cancellation): pending again at once, no
     * attempt counted, no backoff. False when [owner] has no lease.
     */
    suspend fun release(
        key: String,
        owner: String,
    ): Boolean

    /**
     * Records a completed send attempt that failed and must be retried: the attempt counts, and the
     * item is pending again not before [notBeforeMillis]. False when [owner] has no lease.
     */
    suspend fun retryLater(
        key: String,
        owner: String,
        notBeforeMillis: Long,
    ): Boolean

    /**
     * Parks an item the server rejected for good, or that exhausted its attempts, with its payload,
     * [reason] (an identifier matching [REASON]) and [nowMillis]; the item leaves the live queue but is
     * never deleted. False when [owner] has no lease.
     */
    suspend fun deadLetter(
        key: String,
        owner: String,
        reason: String,
        nowMillis: Long,
    ): Boolean

    /** Returns every item whose lease ended before [nowMillis] to pending; the count returned. */
    suspend fun expireLeases(nowMillis: Long): Int

    suspend fun snapshot(): QueueSnapshot

    /** Every dead-lettered item, oldest first, with payload and reason. */
    suspend fun deadLetters(): List<DeadLetter<T>>

    companion object {
        /** Shape of every dead-letter and rejection reason: an identifier, never free text. */
        val REASON: Regex = Regex("[a-z][a-z0-9_]{0,63}")
    }
}

/**
 * Reference implementation with the exact contract semantics, used by the drain tests and by
 * feature tickets until the storage ticket provides the Room-backed queue. Not durable across
 * processes on its own; durability is the storage ticket's job, the semantics are proven here.
 */
class InMemoryDurableQueue<T> : DurableQueue<T> {
    private class Entry<T>(
        val key: String,
        val payload: T,
        val enqueuedAtMillis: Long,
        var attempts: Int = 0,
        var leaseExpiries: Int = 0,
        var owner: String? = null,
        var leaseUntilMillis: Long = 0,
        var retryNotBeforeMillis: Long = 0,
    ) {
        fun item(): QueuedItem<T> = QueuedItem(key, payload, enqueuedAtMillis, attempts, leaseExpiries)
    }

    private val mutex = Mutex()
    private val live = LinkedHashMap<String, Entry<T>>()
    private val dead = LinkedHashMap<String, DeadLetter<T>>()

    override suspend fun enqueue(
        key: String,
        payload: T,
        nowMillis: Long,
    ): Boolean =
        mutex.withLock {
            if (key in live || key in dead) return false
            live[key] = Entry(key, payload, nowMillis)
            true
        }

    override suspend fun claim(
        owner: String,
        limit: Int,
        nowMillis: Long,
        leaseMillis: Long,
    ): List<QueuedItem<T>> =
        mutex.withLock {
            require(limit > 0 && leaseMillis > 0)
            live.values
                .asSequence()
                .filter { it.owner == null && it.retryNotBeforeMillis <= nowMillis }
                .take(limit)
                .map { entry ->
                    entry.owner = owner
                    entry.leaseUntilMillis = nowMillis + leaseMillis
                    entry.item()
                }.toList()
        }

    override suspend fun ack(
        key: String,
        owner: String,
    ): Boolean =
        mutex.withLock {
            val entry = live[key] ?: return false
            if (entry.owner != owner) return false
            live.remove(key)
            true
        }

    override suspend fun release(
        key: String,
        owner: String,
    ): Boolean =
        mutex.withLock {
            val entry = live[key] ?: return false
            if (entry.owner != owner) return false
            entry.owner = null
            entry.leaseUntilMillis = 0
            entry.retryNotBeforeMillis = 0
            true
        }

    override suspend fun retryLater(
        key: String,
        owner: String,
        notBeforeMillis: Long,
    ): Boolean =
        mutex.withLock {
            val entry = live[key] ?: return false
            if (entry.owner != owner) return false
            entry.owner = null
            entry.leaseUntilMillis = 0
            entry.attempts += 1
            entry.retryNotBeforeMillis = notBeforeMillis
            true
        }

    override suspend fun deadLetter(
        key: String,
        owner: String,
        reason: String,
        nowMillis: Long,
    ): Boolean =
        mutex.withLock {
            require(DurableQueue.REASON.matches(reason)) { "dead-letter reason must be an identifier" }
            val entry = live[key] ?: return false
            if (entry.owner != owner) return false
            live.remove(key)
            entry.owner = null
            entry.leaseUntilMillis = 0
            dead[key] = DeadLetter(entry.item(), reason, nowMillis)
            true
        }

    override suspend fun expireLeases(nowMillis: Long): Int =
        mutex.withLock {
            var expired = 0
            for (entry in live.values) {
                if (entry.owner != null && entry.leaseUntilMillis <= nowMillis) {
                    entry.owner = null
                    entry.leaseUntilMillis = 0
                    entry.leaseExpiries += 1
                    expired++
                }
            }
            expired
        }

    override suspend fun snapshot(): QueueSnapshot =
        mutex.withLock {
            QueueSnapshot(
                pending =
                    live.values
                        .filter { it.owner == null }
                        .map { it.key }
                        .toSet(),
                inFlight =
                    live.values
                        .filter { it.owner != null }
                        .map { it.key }
                        .toSet(),
                deadLettered = dead.keys.toSet(),
            )
        }

    override suspend fun deadLetters(): List<DeadLetter<T>> = mutex.withLock { dead.values.toList() }

    /** The recorded reason of a dead-lettered key, for diagnostics. */
    suspend fun deadLetterReason(key: String): String? = mutex.withLock { dead[key]?.reason }
}
