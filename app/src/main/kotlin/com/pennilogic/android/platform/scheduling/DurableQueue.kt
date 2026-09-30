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
    /** Completed send attempts before this one. */
    val attempts: Int,
)

/** Outcome of one send attempt, as the sender reports it. */
sealed interface SendResult {
    /** The server accepted (or idempotently re-accepted) the item. */
    data object Sent : SendResult

    /** Try again later; [notBeforeMillis] overrides the drainer's backoff when the server said when. */
    data class Retry(
        val notBeforeMillis: Long? = null,
    ) : SendResult

    /** The server rejected the item for good; it is dead-lettered with the reason, never retried. */
    data class Rejected(
        val reason: String,
    ) : SendResult
}

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
 * concurrently; [ack] and [release] are accepted only from the owner that holds the lease; a lease
 * that outlives its owner (crash, quota stop mid-send) is returned to pending by [expireLeases], so a
 * crash at any point loses nothing and the same key is re-sent, which the server deduplicates.
 * Every operation is atomic: it either fully applies or leaves the queue unchanged.
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

    /** Returns an item to pending, optionally not before [retryNotBeforeMillis]; false when [owner] has no lease. */
    suspend fun release(
        key: String,
        owner: String,
        retryNotBeforeMillis: Long?,
    ): Boolean

    /** Moves an item the server rejected for good out of the live queue; false when [owner] has no lease. */
    suspend fun deadLetter(
        key: String,
        owner: String,
        reason: String,
    ): Boolean

    /** Returns every item whose lease ended before [nowMillis] to pending; the count returned. */
    suspend fun expireLeases(nowMillis: Long): Int

    suspend fun snapshot(): QueueSnapshot
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
        var owner: String? = null,
        var leaseUntilMillis: Long = 0,
        var retryNotBeforeMillis: Long = 0,
    )

    private val mutex = Mutex()
    private val live = LinkedHashMap<String, Entry<T>>()
    private val dead = LinkedHashMap<String, String>()

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
                    QueuedItem(entry.key, entry.payload, entry.enqueuedAtMillis, entry.attempts)
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
        retryNotBeforeMillis: Long?,
    ): Boolean =
        mutex.withLock {
            val entry = live[key] ?: return false
            if (entry.owner != owner) return false
            entry.owner = null
            entry.leaseUntilMillis = 0
            entry.attempts += 1
            entry.retryNotBeforeMillis = retryNotBeforeMillis ?: 0
            true
        }

    override suspend fun deadLetter(
        key: String,
        owner: String,
        reason: String,
    ): Boolean =
        mutex.withLock {
            val entry = live[key] ?: return false
            if (entry.owner != owner) return false
            live.remove(key)
            dead[key] = reason
            true
        }

    override suspend fun expireLeases(nowMillis: Long): Int =
        mutex.withLock {
            var expired = 0
            for (entry in live.values) {
                if (entry.owner != null && entry.leaseUntilMillis <= nowMillis) {
                    entry.owner = null
                    entry.leaseUntilMillis = 0
                    entry.attempts += 1
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

    /** The recorded rejection reason of a dead-lettered key, for diagnostics. */
    suspend fun deadLetterReason(key: String): String? = mutex.withLock { dead[key] }
}
