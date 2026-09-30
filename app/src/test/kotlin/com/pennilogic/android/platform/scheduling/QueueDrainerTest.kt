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
 * stop mid-batch, any number of stops before a single send, a process crash before and after every
 * queue operation the drain actually performs (claim, ack, release, retryLater, deadLetter,
 * expireLeases) and every send, coroutine cancellation, a lease takeover by a second owner, rejected
 * and repeatedly retried items. In every scenario each queued item is applied exactly once or parked
 * as a dead letter with its payload, and no live item remains: nothing lost, nothing duplicated.
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

        /** Keys answered with one `Retry` on their first send, then accepted. */
        val retryOnce = mutableSetOf<String>()

        fun send(item: QueuedItem<String>): SendResult {
            sends++
            if (sends == crashBeforeSend) throw ProcessCrash()
            if (item.key in rejectKeys) return SendResult.Rejected("schema")
            if (retryOnce.remove(item.key)) return SendResult.Retry()
            receipts[item.key] = (receipts[item.key] ?: 0) + 1
            applied.add(item.key)
            if (sends == crashAfterSend) throw ProcessCrash()
            return SendResult.Sent
        }
    }

    private data class CrashPlan(
        val operation: String,
        val nth: Int,
        val after: Boolean,
    )

    /** Counts every queue operation and dies before or after the nth call of the planned one. */
    private class InstrumentedQueue<T>(
        private val delegate: DurableQueue<T>,
        private val plan: CrashPlan?,
    ) : DurableQueue<T> {
        val calls = LinkedHashMap<String, Int>()

        private fun before(name: String) {
            val count = calls.merge(name, 1, Int::plus)
            if (plan != null && plan.operation == name && plan.nth == count && !plan.after) throw ProcessCrash()
        }

        private fun afterwards(name: String) {
            if (plan != null && plan.operation == name && plan.nth == calls[name] && plan.after) throw ProcessCrash()
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
        ): Boolean {
            before("release")
            return delegate.release(key, owner).also { afterwards("release") }
        }

        override suspend fun retryLater(
            key: String,
            owner: String,
            notBeforeMillis: Long,
        ): Boolean {
            before("retryLater")
            return delegate.retryLater(key, owner, notBeforeMillis).also { afterwards("retryLater") }
        }

        override suspend fun deadLetter(
            key: String,
            owner: String,
            reason: String,
            nowMillis: Long,
        ): Boolean {
            before("deadLetter")
            return delegate.deadLetter(key, owner, reason, nowMillis).also { afterwards("deadLetter") }
        }

        override suspend fun expireLeases(nowMillis: Long): Int {
            before("expireLeases")
            return delegate.expireLeases(nowMillis).also { afterwards("expireLeases") }
        }

        override suspend fun snapshot(): QueueSnapshot = delegate.snapshot()

        override suspend fun deadLetters(): List<DeadLetter<T>> = delegate.deadLetters()
    }

    /** A first drain shaped so that a given set of queue operations is reached, and what it must leave behind. */
    private class Scenario(
        val name: String,
        val expectedDeadLettered: Set<String>,
        val configure: (FakeServer) -> Unit,
        val stopSignal: (FakeServer) -> (() -> StopReason?),
    )

    private val scenarios =
        listOf(
            Scenario("every send succeeds", emptySet(), configure = {}, stopSignal = { { null } }),
            Scenario(
                "one transient retry and one rejection",
                expectedDeadLettered = setOf("txn-5"),
                configure = {
                    it.retryOnce += "txn-3"
                    it.rejectKeys = setOf("txn-5")
                },
                stopSignal = { { null } },
            ),
            Scenario(
                "quota stop after the first send",
                emptySet(),
                configure = {},
                stopSignal = { server -> { if (server.sends >= 1) StopReason.QUOTA else null } },
            ),
        )

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
    fun `ten quota stops before any send then one transient retry keep the head item queued once`() =
        runTest {
            val queue = seeded()
            val server = FakeServer()
            var now = 10_000L
            repeat(10) { round ->
                var polls = 0
                // Poll 1 is before the claim, poll 2 before the first send: the whole batch goes back unsent.
                val outcome =
                    drainer(queue, server, "run-$round", { now }, maxAttempts = 2).drain {
                        if (++polls == 2) StopReason.QUOTA else null
                    }
                val stopped = outcome as DrainOutcome.Stopped
                assertEquals(0, stopped.sent)
                assertEquals(3, stopped.released)
                assertEquals(keys.toSet(), queue.snapshot().pending)
                now += 1
            }
            assertEquals(0, server.sends)
            val head = queue.claim("peek", 1, now, LEASE).single()
            assertEquals("txn-1", head.key)
            assertEquals("ten stops spent no attempt", 0, head.attempts)
            assertTrue(queue.release("txn-1", "peek"))

            server.retryOnce += "txn-1"
            val retried = drainer(queue, server, "run-retry", { now }, maxAttempts = 2).drain { null }
            assertEquals(DrainOutcome.Drained(sent = 6, deadLettered = 0, retryLater = 1), retried)
            assertEquals("still queued, not parked", setOf("txn-1"), queue.snapshot().pending)
            assertEquals(emptySet<String>(), queue.snapshot().deadLettered)

            now += QueueDrainer.exponentialBackoffMillis(1)
            assertEquals(
                DrainOutcome.Drained(1, 0, 0),
                drainer(queue, server, "run-final", { now }, maxAttempts = 2).drain { null },
            )
            assertExactlyOnce(server, queue)
        }

    @Test
    fun `a crash before or after every queue operation the drain performs loses nothing`() =
        runTest {
            for (scenario in scenarios) {
                // Record which operations the first drain of this scenario performs, and how often.
                val recording = InstrumentedQueue(seeded(), plan = null)
                val recordingServer = FakeServer().also(scenario.configure)
                drainer(recording, recordingServer, "rec", { 10_000L }).drain(scenario.stopSignal(recordingServer))
                val plans =
                    recording.calls.flatMap { (operation, count) ->
                        (1..count).flatMap { nth ->
                            listOf(CrashPlan(operation, nth, false), CrashPlan(operation, nth, true))
                        }
                    }
                assertTrue("${scenario.name}: plans", plans.isNotEmpty())

                for (plan in plans) {
                    val queue = seeded()
                    val server = FakeServer().also(scenario.configure)
                    var now = 10_000L
                    val crashed =
                        runCatching {
                            drainer(InstrumentedQueue(queue, plan), server, "run-1", { now })
                                .drain(scenario.stopSignal(server))
                        }.exceptionOrNull() is ProcessCrash
                    assertTrue("${scenario.name}: $plan must crash where planned", crashed)
                    now = recover(queue, server, now)
                    assertExactlyOnce(server, queue, "${scenario.name}: $plan", scenario.expectedDeadLettered)
                }
            }
            val covered =
                scenarios
                    .map { scenario ->
                        val recording = InstrumentedQueue(seeded(), plan = null)
                        val server = FakeServer().also(scenario.configure)
                        drainer(recording, server, "rec", { 10_000L }).drain(scenario.stopSignal(server))
                        recording.calls.keys
                    }.flatten()
                    .toSet()
            assertEquals(
                "every mutating queue operation is crash-covered by some scenario",
                setOf("claim", "ack", "release", "retryLater", "deadLetter", "expireLeases"),
                covered,
            )
        }

    @Test
    fun `a crash before or after every send loses nothing and re-sends only the item in flight`() =
        runTest {
            for (send in 1..keys.size) {
                for (afterSend in listOf(false, true)) {
                    val queue = seeded()
                    val server = FakeServer().apply { if (afterSend) crashAfterSend = send else crashBeforeSend = send }
                    var now = 10_000L
                    val crashed =
                        runCatching {
                            drainer(queue, server, "run-1", { now }).drain { null }
                        }.exceptionOrNull() is ProcessCrash
                    assertTrue("send crash $send/$afterSend must happen", crashed)
                    server.crashAfterSend = null
                    server.crashBeforeSend = null
                    now = recover(queue, server, now)
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
            val attempts = queue.claim("peek", 7, 10_001, LEASE).sumOf { it.attempts }
            assertEquals("a cancellation spends no attempt", 0, attempts)
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
    fun `rejected items are parked once and retried items back off then park at the limit with their payload`() =
        runTest {
            val queue = seeded()
            val server =
                FakeServer().apply {
                    rejectKeys = setOf("txn-2")
                    retryOnce += "txn-5"
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
            server.retryOnce += "txn-5"
            val second = drainer(queue, server, "run-2", { now }, maxAttempts = 2).drain { null }
            assertEquals(
                "the second failed send exhausts the two attempts",
                DrainOutcome.Drained(sent = 0, deadLettered = 1, retryLater = 0),
                second,
            )
            assertEquals(setOf("txn-2", "txn-5"), queue.snapshot().deadLettered)
            assertEquals(QueueDrainer.MAX_ATTEMPTS_REASON, queue.deadLetterReason("txn-5"))
            assertTrue(queue.snapshot().isEmpty)
            assertEquals(keys.toSet() - setOf("txn-2", "txn-5"), server.applied)
            val parked = queue.deadLetters().associateBy { it.item.key }
            assertEquals("payload:txn-2", parked.getValue("txn-2").item.payload)
            assertEquals("payload:txn-5", parked.getValue("txn-5").item.payload)
            assertEquals("both failed sends are on record", 1, parked.getValue("txn-5").item.attempts)
            assertEquals(now, parked.getValue("txn-5").deadLetteredAtMillis)
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

    /**
     * Restart after a crash or stop: leases outlive the dead owner and expire, backoffs elapse, and
     * fresh drains run until nothing is pending or in flight. Returns the advanced clock.
     */
    private suspend fun recover(
        queue: DurableQueue<String>,
        server: FakeServer,
        start: Long,
    ): Long {
        var now = start
        repeat(4) { round ->
            now += LEASE + 1
            drainer(queue, server, "recovery-$round", { now }).drain { null }
            if (queue.snapshot().isEmpty) return now
        }
        return now
    }

    private suspend fun assertExactlyOnce(
        server: FakeServer,
        queue: DurableQueue<String>,
        context: String = "",
        deadLettered: Set<String> = emptySet(),
    ) {
        assertEquals("every deliverable item applied ($context)", keys.toSet() - deadLettered, server.applied)
        server.receipts.forEach { (key, count) -> assertTrue("$key received $count times ($context)", count in 1..2) }
        assertTrue("no live item left ($context): ${queue.snapshot()}", queue.snapshot().isEmpty)
        assertEquals("dead letters ($context)", deadLettered, queue.snapshot().deadLettered)
        queue.deadLetters().forEach { parked ->
            assertEquals("payload retained ($context)", "payload:${parked.item.key}", parked.item.payload)
        }
    }

    private companion object {
        const val LEASE = 60_000L
    }
}
