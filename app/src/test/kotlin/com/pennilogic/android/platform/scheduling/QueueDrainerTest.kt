package com.pennilogic.android.platform.scheduling

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Proves the drain invariants against a fake server that applies each idempotency key once: a quota
 * stop mid-batch, a process crash before or after every queue operation and every send, coroutine
 * cancellation, a lease takeover by a second owner, rejected and repeatedly retried items. In every
 * scenario each queued item is applied exactly once and the queue ends empty: nothing lost, nothing
 * duplicated.
 */
class QueueDrainerTest {
    /** Simulated process death: an Error, because a dying process throws nothing the drainer could catch. */
    private class ProcessCrash : Error("simulated process death")

    /** Applies each key once; counts every receipt so at-least-once delivery is visible. */
    private class FakeServer {
        val applied = LinkedHashSet<String>()
        val receipts = LinkedHashMap<String, Int>()
        var sends = 0
        var crashBeforeSend: Int? = null
        var crashAfterSend: Int? = null
        var rejectKeys: Set<String> = emptySet()
        var retryKeys: Set<String> = emptySet()

        fun send(item: QueuedItem<String>): SendResult {
            sends++
            if (sends == crashBeforeSend) throw ProcessCrash()
            if (item.key in rejectKeys) return SendResult.Rejected("schema")
            if (item.key in retryKeys) return SendResult.Retry()
            receipts[item.key] = (receipts[item.key] ?: 0) + 1
            applied.add(item.key)
            if (sends == crashAfterSend) throw ProcessCrash()
            return SendResult.Sent
        }
    }

    /** Dies before or after the nth call of one operation; every other call passes through. */
    private class CrashingQueue<T>(
        private val delegate: DurableQueue<T>,
        private val operation: String,
        private val nth: Int,
        private val after: Boolean,
    ) : DurableQueue<T> {
        private val counts = HashMap<String, Int>()

        private fun before(name: String) {
            if (name != operation || after) return
            if (counts.merge(name, 1, Int::plus) == nth) throw ProcessCrash()
        }

        private fun afterwards(name: String) {
            if (name != operation || !after) return
            if (counts.merge(name, 1, Int::plus) == nth) throw ProcessCrash()
        }

        override suspend fun enqueue(
            key: String,
            payload: T,
            nowMillis: Long,
        ): Boolean = delegate.enqueue(key, payload, nowMillis)

        override suspend fun claim(
            owner: String,
            limit: Int,
            nowMillis: Long,
            leaseMillis: Long,
        ): List<QueuedItem<T>> {
            before("claim")
            return delegate.claim(owner, limit, nowMillis, leaseMillis).also { afterwards("claim") }
        }

        override suspend fun ack(
            key: String,
            owner: String,
        ): Boolean {
            before("ack")
            return delegate.ack(key, owner).also { afterwards("ack") }
        }

        override suspend fun release(
            key: String,
            owner: String,
            retryNotBeforeMillis: Long?,
        ): Boolean {
            before("release")
            return delegate.release(key, owner, retryNotBeforeMillis).also { afterwards("release") }
        }

        override suspend fun deadLetter(
            key: String,
            owner: String,
            reason: String,
        ): Boolean {
            before("deadLetter")
            return delegate.deadLetter(key, owner, reason).also { afterwards("deadLetter") }
        }

        override suspend fun expireLeases(nowMillis: Long): Int {
            before("expireLeases")
            return delegate.expireLeases(nowMillis).also { afterwards("expireLeases") }
        }

        override suspend fun snapshot(): QueueSnapshot = delegate.snapshot()
    }

    private val keys = (1..7).map { "txn-$it" }

    private suspend fun seeded(): InMemoryDurableQueue<String> =
        InMemoryDurableQueue<String>().also { queue ->
            keys.forEach { assertTrue(queue.enqueue(it, "payload:$it", 1_000)) }
        }

    private fun drainer(
        queue: DurableQueue<String>,
        server: FakeServer,
        owner: String,
        clock: () -> Long,
        batchSize: Int = 3,
        maxAttempts: Int = 3,
    ) = QueueDrainer(queue, {
        server.send(it)
    }, owner, clock, batchSize = batchSize, leaseMillis = LEASE, maxAttempts = maxAttempts)

    @Test
    fun `quota stop mid-batch releases the unsent items and a later drain finishes them exactly once`() =
        runTest {
            val queue = seeded()
            val server = FakeServer()
            var now = 10_000L
            val outcome =
                drainer(queue, server, "run-1", { now }).drain {
                    if (server.sends >= 4) StopReason.QUOTA else null
                }
            val stopped = outcome as DrainOutcome.Stopped
            assertEquals(StopReason.QUOTA, stopped.reason)
            assertEquals(StopDisposition.DEFER_TO_SCHEDULER, stopped.disposition)
            assertEquals(4, stopped.sent)
            assertEquals("the rest of the claimed batch went back", 2, stopped.released)
            val snapshot = queue.snapshot()
            assertEquals(emptySet<String>(), snapshot.inFlight)
            assertEquals(keys.drop(4).toSet(), snapshot.pending)

            now += 1
            val second = drainer(queue, server, "run-2", { now }).drain { null }
            assertEquals(DrainOutcome.Drained(sent = 3, deadLettered = 0, retryLater = 0), second)
            assertExactlyOnce(server, queue)
        }

    @Test
    fun `a stop before the first claim touches nothing`() =
        runTest {
            val queue = seeded()
            val server = FakeServer()
            val outcome = drainer(queue, server, "run", { 10_000 }).drain { StopReason.APP_STANDBY }
            assertEquals(
                DrainOutcome.Stopped(StopReason.APP_STANDBY, StopDisposition.DEFER_TO_SCHEDULER, 0, 0, 0),
                outcome,
            )
            assertEquals(keys.toSet(), queue.snapshot().pending)
            assertEquals(0, server.sends)
        }

    @Test
    fun `a crash before or after any queue operation or send loses nothing and applies each item once`() =
        runTest {
            val plans =
                buildList {
                    for (operation in listOf("claim", "ack", "expireLeases")) {
                        for (nth in 1..keys.size) {
                            add(Triple(operation, nth, false))
                            add(Triple(operation, nth, true))
                        }
                    }
                }
            for ((operation, nth, after) in plans) {
                val queue = seeded()
                val server = FakeServer()
                var now = 10_000L
                val crashed =
                    runCatching {
                        drainer(CrashingQueue(queue, operation, nth, after), server, "run-1", { now }).drain { null }
                    }.exceptionOrNull() is ProcessCrash
                // Restart: leases outlive the dead owner and expire before the next drain.
                now += LEASE + 1
                drainer(queue, server, "run-2", { now }).drain { null }
                assertExactlyOnce(server, queue, "plan=$operation#$nth after=$after crashed=$crashed")
            }
            for (send in 1..keys.size) {
                for (afterSend in listOf(false, true)) {
                    val queue = seeded()
                    val server = FakeServer().apply { if (afterSend) crashAfterSend = send else crashBeforeSend = send }
                    var now = 10_000L
                    val crashed =
                        runCatching {
                            drainer(
                                queue,
                                server,
                                "run-1",
                                { now },
                            ).drain { null }
                        }.exceptionOrNull() is ProcessCrash
                    assertTrue("send crash $send/$afterSend must happen", crashed)
                    server.crashAfterSend = null
                    server.crashBeforeSend = null
                    now += LEASE + 1
                    drainer(queue, server, "run-2", { now }).drain { null }
                    assertExactlyOnce(server, queue, "sendCrash=$send after=$afterSend")
                    val resent = server.receipts.count { it.value > 1 }
                    assertEquals("only the item in flight at the crash is re-sent", if (afterSend) 1 else 0, resent)
                }
            }
        }

    @Test
    fun `cancellation mid-send gives every claimed item back`() =
        runTest {
            val queue = seeded()
            val gate = CompletableDeferred<Unit>()
            val started = CompletableDeferred<Unit>()
            val drainer =
                QueueDrainer<String>(
                    queue,
                    sender = {
                        started.complete(Unit)
                        gate.await()
                        SendResult.Sent
                    },
                    owner = "run",
                    clock = { 10_000 },
                    batchSize = 3,
                    leaseMillis = LEASE,
                )
            val job = launch { drainer.drain { null } }
            started.await()
            assertEquals(3, queue.snapshot().inFlight.size)
            job.cancel()
            job.join()
            val snapshot = queue.snapshot()
            assertEquals(emptySet<String>(), snapshot.inFlight)
            assertEquals(keys.toSet(), snapshot.pending)
        }

    @Test
    fun `a stalled owner loses its lease and the takeover applies the item once`() =
        runTest {
            val queue = InMemoryDurableQueue<String>()
            queue.enqueue("txn-1", "p", 1_000)
            val server = FakeServer()
            var now = 10_000L
            val stalled = queue.claim("A", 1, now, LEASE).single()
            now += LEASE + 1
            val outcome = drainer(queue, server, "B", { now }).drain { null }
            assertEquals(DrainOutcome.Drained(1, 0, 0), outcome)
            // A wakes up and finishes its work: the server deduplicates, the queue refuses the stale ack.
            server.send(stalled)
            assertFalse(queue.ack("txn-1", "A"))
            assertEquals(1, server.applied.size)
            assertEquals(2, server.receipts["txn-1"])
            assertTrue(queue.snapshot().isEmpty)
        }

    @Test
    fun `rejected items are dead-lettered once and retried items back off then dead-letter at the limit`() =
        runTest {
            val queue = seeded()
            val server =
                FakeServer().apply {
                    rejectKeys = setOf("txn-2")
                    retryKeys = setOf("txn-5")
                }
            var now = 10_000L
            val first = drainer(queue, server, "run-1", { now }, maxAttempts = 2).drain { null }
            assertEquals(DrainOutcome.Drained(sent = 5, deadLettered = 1, retryLater = 1), first)
            assertEquals(setOf("txn-2"), queue.snapshot().deadLettered)
            assertEquals("schema", queue.deadLetterReason("txn-2"))
            assertEquals(
                "txn-5 waits for its backoff",
                emptyList<String>(),
                queue.claim("peek", 10, now, LEASE).map { it.key },
            )
            now += QueueDrainer.exponentialBackoffMillis(1)
            val second = drainer(queue, server, "run-2", { now }, maxAttempts = 2).drain { null }
            assertEquals(
                "second retry exhausts the two attempts",
                DrainOutcome.Drained(sent = 0, deadLettered = 1, retryLater = 0),
                second,
            )
            assertEquals(setOf("txn-2", "txn-5"), queue.snapshot().deadLettered)
            assertEquals("max_attempts", queue.deadLetterReason("txn-5"))
            assertTrue(queue.snapshot().isEmpty)
            assertEquals(keys.toSet() - setOf("txn-2", "txn-5"), server.applied)
        }

    @Test
    fun `a sender that throws is a retry, not a lost item and not a failed run`() =
        runTest {
            val queue = seeded()
            val server = FakeServer()
            var now = 10_000L
            var throwOnce = true
            val drainer =
                QueueDrainer<String>(
                    queue,
                    sender = { item ->
                        if (item.key == "txn-3" && throwOnce) {
                            throwOnce = false
                            throw java.io.IOException("connection reset")
                        }
                        server.send(item)
                    },
                    owner = "run-1",
                    clock = { now },
                    batchSize = 3,
                    leaseMillis = LEASE,
                )
            assertEquals(DrainOutcome.Drained(sent = 6, deadLettered = 0, retryLater = 1), drainer.drain { null })
            assertEquals(setOf("txn-3"), queue.snapshot().pending)
            now += QueueDrainer.exponentialBackoffMillis(1)
            assertEquals(DrainOutcome.Drained(1, 0, 0), drainer.drain { null })
            assertExactlyOnce(server, queue)
        }

    @Test
    fun `backoff doubles from thirty seconds and caps at one hour`() {
        assertEquals(30_000, QueueDrainer.exponentialBackoffMillis(1))
        assertEquals(60_000, QueueDrainer.exponentialBackoffMillis(2))
        assertEquals(120_000, QueueDrainer.exponentialBackoffMillis(3))
        assertEquals(3_600_000, QueueDrainer.exponentialBackoffMillis(8))
        assertEquals(3_600_000, QueueDrainer.exponentialBackoffMillis(50))
        assertEquals(30_000, QueueDrainer.exponentialBackoffMillis(0))
    }

    private suspend fun assertExactlyOnce(
        server: FakeServer,
        queue: DurableQueue<String>,
        context: String = "",
    ) {
        assertEquals("every item applied ($context)", keys.toSet(), server.applied)
        server.receipts.forEach { (key, count) -> assertTrue("$key received $count times ($context)", count in 1..2) }
        assertTrue("queue empty ($context): ${queue.snapshot()}", queue.snapshot().isEmpty)
        assertEquals("nothing dead-lettered ($context)", emptySet<String>(), queue.snapshot().deadLettered)
    }

    private companion object {
        const val LEASE = 60_000L
    }
}
