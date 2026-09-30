package com.pennilogic.android.platform.scheduling

import android.content.Context
import android.util.Log
import androidx.test.core.app.ApplicationProvider
import androidx.work.ListenableWorker
import androidx.work.WorkerParameters
import androidx.work.testing.TestListenableWorkerBuilder
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.shadows.ShadowLog

class WorkerResultMapperTest {
    @Test
    fun `drained work succeeds unless items wait for a retry`() {
        assertEquals(ListenableWorker.Result.success(), WorkerResultMapper.map(DrainOutcome.Drained(3, 0, 0)))
        assertEquals(ListenableWorker.Result.success(), WorkerResultMapper.map(DrainOutcome.Drained(0, 2, 0)))
        assertEquals(ListenableWorker.Result.retry(), WorkerResultMapper.map(DrainOutcome.Drained(3, 0, 1)))
    }

    @Test
    fun `stopped work follows the disposition and never fails the chain`() {
        fun stopped(reason: StopReason) = DrainOutcome.Stopped(reason, StopReasonPolicy.disposition(reason), 1, 0, 2)
        assertEquals(ListenableWorker.Result.retry(), WorkerResultMapper.map(stopped(StopReason.QUOTA)))
        assertEquals(ListenableWorker.Result.retry(), WorkerResultMapper.map(stopped(StopReason.APP_STANDBY)))
        assertEquals(ListenableWorker.Result.retry(), WorkerResultMapper.map(stopped(StopReason.TIMEOUT)))
        assertEquals(ListenableWorker.Result.retry(), WorkerResultMapper.map(stopped(StopReason.TIMEOUT_ABANDONED)))
        assertEquals(
            ListenableWorker.Result.success(),
            WorkerResultMapper.map(stopped(StopReason.BACKGROUND_RESTRICTION)),
        )
        assertEquals(ListenableWorker.Result.success(), WorkerResultMapper.map(stopped(StopReason.CANCELLED_BY_APP)))
        assertEquals(ListenableWorker.Result.success(), WorkerResultMapper.map(stopped(StopReason.NOT_STOPPED)))
        StopReason.entries.forEach { reason ->
            assertTrue(
                "$reason must never end the chain with failure",
                WorkerResultMapper.map(stopped(reason)) != ListenableWorker.Result.failure(),
            )
        }
    }

    @Test
    fun `platform stop reason is read only while stopped`() {
        assertNull(WorkerResultMapper.stopReason(isStopped = false, platformStopReason = 10))
        assertEquals(StopReason.QUOTA, WorkerResultMapper.stopReason(isStopped = true, platformStopReason = 10))
        assertEquals(StopReason.UNKNOWN, WorkerResultMapper.stopReason(isStopped = true, platformStopReason = -512))
    }
}

/**
 * A concrete drain worker over an in-memory queue, as a feature ticket would write it. The companion
 * holds the scenario because `TestListenableWorkerBuilder` constructs the worker reflectively.
 */
class TestQueueDrainWorker(
    context: Context,
    parameters: WorkerParameters,
) : QueueDrainWorker(context, parameters) {
    override fun createDrainer(owner: String): QueueDrainer<*> {
        owners += owner
        return QueueDrainer(queue, { item ->
            started.complete(Unit)
            gate?.await()
            sent += item.key
            SendResult.Sent
        }, owner, { 10_000L }, leaseMillis = 60_000)
    }

    override fun platformStop(): StopReason? {
        val stopAfter = simulatedStopAfterSends ?: return super.platformStop()
        return if (sent.size >= stopAfter) simulatedStop else null
    }

    companion object {
        var queue = InMemoryDurableQueue<String>()
        val sent = mutableListOf<String>()
        val owners = mutableListOf<String>()
        var started = CompletableDeferred<Unit>()
        var gate: CompletableDeferred<Unit>? = null
        var simulatedStopAfterSends: Int? = null
        var simulatedStop: StopReason? = null

        fun reset() {
            queue = InMemoryDurableQueue()
            sent.clear()
            owners.clear()
            started = CompletableDeferred()
            gate = null
            simulatedStopAfterSends = null
            simulatedStop = null
        }
    }
}

@RunWith(RobolectricTestRunner::class)
class QueueDrainWorkerTest {
    private val context: Context = ApplicationProvider.getApplicationContext()

    @Before
    fun reset() {
        TestQueueDrainWorker.reset()
        ShadowLog.clear()
    }

    private fun seed(vararg keys: String) =
        runBlocking { keys.forEach { TestQueueDrainWorker.queue.enqueue(it, "payload", 1) } }

    private fun capturedEvent(): String =
        ShadowLog
            .getLogsForTag("PenniLogic")
            .map { it.msg }
            .single { it.contains("\"event\":\"capture_health\"") }

    @Test
    fun `the worker drains the queue, succeeds and logs the capture health event without content`() {
        seed("txn-1", "txn-2")
        val worker = TestListenableWorkerBuilder<TestQueueDrainWorker>(context).build()

        val result = runBlocking { worker.doWork() }

        assertEquals(ListenableWorker.Result.success(), result)
        assertEquals(listOf("txn-1", "txn-2"), TestQueueDrainWorker.sent)
        assertTrue(runBlocking { TestQueueDrainWorker.queue.snapshot().isEmpty })
        assertTrue("owner is attributable to the run", TestQueueDrainWorker.owners.single().startsWith("work:"))
        val event = capturedEvent()
        assertTrue(event, event.contains("\"api_level\":36"))
        assertTrue(event, event.contains("\"stop_reason\":null"))
        assertTrue(event, event.contains("\"capture_health\":null"))
        assertTrue("no payload or key in the event: $event", !event.contains("payload") && !event.contains("txn-"))
        assertEquals(Log.INFO, ShadowLog.getLogsForTag("PenniLogic").single { it.msg == event }.type)
    }

    @Test
    fun `a platform stop polled mid-run releases the unsent items, logs the reason and asks for a retry`() {
        seed("txn-1", "txn-2", "txn-3")
        TestQueueDrainWorker.simulatedStopAfterSends = 1
        TestQueueDrainWorker.simulatedStop = StopReason.QUOTA
        val worker = TestListenableWorkerBuilder<TestQueueDrainWorker>(context).build()

        val result = runBlocking { worker.doWork() }

        assertEquals("the scheduler re-runs the chain when quota returns", ListenableWorker.Result.retry(), result)
        assertEquals(listOf("txn-1"), TestQueueDrainWorker.sent)
        val snapshot = runBlocking { TestQueueDrainWorker.queue.snapshot() }
        assertEquals(setOf("txn-2", "txn-3"), snapshot.pending)
        assertEquals(emptySet<String>(), snapshot.inFlight)
        assertEquals(emptySet<String>(), snapshot.deadLettered)
        val unsent = runBlocking { TestQueueDrainWorker.queue.claim("peek", 10, 20_000, 60_000) }
        assertEquals("a quota stop spends no attempt", 0, unsent.sumOf { it.attempts })
        val event = capturedEvent()
        assertTrue(event, event.contains("\"stop_reason\":\"quota\""))
        assertTrue(event, !event.contains("txn-"))
    }

    @Test
    fun `a stop that cancels the coroutine mid-send gives every claimed item back`() {
        seed("txn-1", "txn-2", "txn-3")
        val gate = CompletableDeferred<Unit>()
        TestQueueDrainWorker.gate = gate
        val worker = TestListenableWorkerBuilder<TestQueueDrainWorker>(context).build()

        // WorkManager stops a CoroutineWorker by calling stop(reason) -> onStopped() and cancelling the
        // future startWork() returned, which cancels the coroutine running doWork().
        val future = worker.startWork()
        runBlocking { withTimeout(10_000) { TestQueueDrainWorker.started.await() } }
        assertEquals(
            3,
            runBlocking {
                TestQueueDrainWorker.queue
                    .snapshot()
                    .inFlight.size
            },
        )
        worker.onStopped()
        future.cancel(true)

        assertTrue(future.isCancelled)
        val deadline = System.currentTimeMillis() + 10_000
        var snapshot = runBlocking { TestQueueDrainWorker.queue.snapshot() }
        while (snapshot.inFlight.isNotEmpty() && System.currentTimeMillis() < deadline) {
            Thread.sleep(20)
            snapshot = runBlocking { TestQueueDrainWorker.queue.snapshot() }
        }
        assertEquals("the NonCancellable release ran", emptySet<String>(), snapshot.inFlight)
        assertEquals(setOf("txn-1", "txn-2", "txn-3"), snapshot.pending)
        assertEquals("nothing was sent", emptyList<String>(), TestQueueDrainWorker.sent)
        gate.complete(Unit)
    }
}
