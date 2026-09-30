# Scheduling stop reasons, durable queue drain and capture health

Android platform baseline, android#57 (`T-AND-07`), pull request 2. Source of truth:
`app/src/main/kotlin/com/pennilogic/android/platform/scheduling/` and `.../platform/capture/`.
These are reusable primitives: no sync feature, no capture feature and no product UI exist yet.
Feature tickets consume them and carry no private Android-version workaround of their own.

## 1. Stop reasons and standby buckets

Android 16 enforces the job runtime quota also for jobs started while the app is in the top state
and for jobs running alongside a foreground service, enforces a (generous) quota in the active
standby bucket, and adds `STOP_REASON_TIMEOUT_ABANDONED` for jobs that time out without the app
holding their `JobParameters`. A quota stop is therefore an ordinary outcome that every scheduled
drain must survive.

`StopReason` is the one vocabulary for `JobParameters.getStopReason()` (API 31+) and
`WorkInfo.getStopReason()` / `ListenableWorker.getStopReason()` (WorkManager 2.9+), whose integer
values are identical; unknown values map to `unknown` rather than crashing. Each reason has a
category and `StopReasonPolicy` turns the category into a `StopDisposition`:

| Category | Reasons | Disposition | Meaning |
| --- | --- | --- | --- |
| quota | `quota`, `app_standby` | `defer_to_scheduler` | Release claimed items; the scheduler re-runs the work when quota returns. |
| constraint | `constraint_*` | `defer_to_scheduler` | Same; the scheduler re-runs when the constraint holds again. |
| transient | `preempt`, `timeout`, `device_state`, `system_processing`, `estimated_app_launch_time_changed` | `retry_with_backoff` | Release and retry with backoff. |
| user restriction | `background_restriction`, `user` | `pause_until_user_action` | Release; only the user can lift it; the capture surface shows the pause. |
| app | `cancelled_by_app` | `cancelled` | Release; nothing is rescheduled here. |
| defect | `timeout_abandoned` (API 36) | `record_defect` | Release and record the abandoned job as a defect. |
| other | `undefined`, `unknown` | `retry_with_backoff` | Release and retry. |

Whatever the reason, claimed items are released, never dropped. `StandbyBucket` mirrors
`UsageStatsManager` (`active`, `working_set`, `frequent`, `rare`, `restricted`; hidden buckets map to
`unknown` with the raw value kept for the event). Only the `restricted` bucket pauses capture
(`standby_bucket_restricted`); a quota stop pauses *sync*, which is the pending marker of the
taxonomy's happy path, never capture.

## 2. Durable queue and drain

`DurableQueue<T>` is the contract for locally queued writes awaiting sync. `InMemoryDurableQueue`
is the reference implementation with exactly these semantics; the storage ticket provides the
Room-backed one and must pass `DurableQueueContractTest`:

- an item exists at most once per idempotency key (`enqueue` refuses a second copy, also while in
  flight or dead-lettered);
- `claim` leases items to one owner for a bounded time, so two drains never send the same item
  concurrently; `ack`, `release` and `deadLetter` are accepted only from the lease holder;
- `expireLeases` returns leases that outlived their owner (crash, quota stop mid-send) to pending;
- every operation is atomic.

`QueueDrainer` drains in batches: it polls the platform stop signal before every claim and before
every send, releases every claimed-but-unsent item when the signal arrives, releases them under
`NonCancellable` when the coroutine is cancelled (WorkManager stopping the worker), acknowledges an
item only after the sender confirmed it, treats a sender that throws (network, serialization) as a
retry rather than a lost item or a failed run, dead-letters a rejected item once, backs off a retried
item (30 s doubling to one hour) and dead-letters it at the attempt limit. The drainer never reads the
payload; amounts and currencies stay inside it.

**What "no loss, no duplication" means and how it is proven.** `QueueDrainerTest` runs a fake server
that applies each idempotency key once and counts every receipt. Across a quota stop after four
sends, a process crash before and after every `claim`, `ack` and `expireLeases` call and before and
after every send, coroutine cancellation mid-send and a lease takeover by a second owner, every
queued item ends up applied exactly once and the queue ends empty. The one honest residual: a crash
between a successful send and its acknowledgement re-sends that single item under the same key,
which the server deduplicates — at-least-once delivery with idempotent application, which is what
"without losing or duplicating queued transactions" requires from a client.

## 3. WorkManager adapter

`QueueDrainWorker` is the base `CoroutineWorker` for scheduled drains. It builds the drainer with an
owner unique to the run, hands the platform stop reason (`ListenableWorker.getStopReason()` on API
31+, `unknown` below) to the drain, logs the `capture_health` event with the stop reason and standby
bucket, and maps the outcome with `WorkerResultMapper`: drained → success (retry when items wait for a
server-requested retry); stopped → retry for scheduler-deferred, transient and defect reasons,
success (chain ends, items wait, surface shows the pause) for user restrictions and app cancellation;
never `failure`, so a chain is never abandoned by a platform stop. WorkManager 2.12.0 adds four
normal (install-time) permissions and its services and receivers to the merged manifest; they are
listed in `SCAFFOLD.md` and allow-listed by `TargetConformanceTest`.

## 4. Capture health

`CaptureHealth` is `healthy`, `paused(reason)` or `blocked(reason, permission)`, bound to the
taxonomy through the conditions published in
[`capture-health-identifiers.json`](capture-health-identifiers.json): paused →
`capture_paused_by_platform` → `degraded`; blocked → `capture_blocked_by_setting` →
`permission_denied` (cause `device`). `blocked` renders ahead of `paused`, as the taxonomy precedence
requires.

`TrackingPauseDetector` reads `PlatformSignals` at process start:

| Signal | API | Reason |
| --- | --- | --- |
| `ApplicationStartInfo.wasForceStopped()` on the first start after a stop | 35+ | `force_stopped`, or `private_space_paused` when the process runs in a non-managed profile |
| `ApplicationExitInfo.getReason()` = `REASON_USER_REQUESTED` (force-stop) or `REASON_USER_STOPPED` (profile stopped) | 30–34 (also read on 35+) | same as above |
| `ActivityManager.isBackgroundRestricted()` | 28+ | `background_restricted` |
| `UsageStatsManager.getAppStandbyBucket()` = restricted | 28+ | `standby_bucket_restricted` |
| nothing detectable | 26–29 | none; capture health is re-probed instead |

The public SDK cannot name the private space: `UserManager.isProfile() && !isManagedProfile()` is
true for the private space and for a clone profile alike, and both are classified as
`private_space_paused`. This residual is recorded here and in the identifiers document. The
classification records a concealment choice of the user (running the app inside a private space):
it stays on the device, is counted in aggregate only, is never joined to an account identifier and
is not exported by this baseline.

`CaptureHealthMonitor` is the state machine behind "after force-stop recovery the app shows tracking
paused until capture health is restored": `onProcessStart` records a detected pause and **never
clears one**; `onCaptureComponentsRegistered` records that the capture components exist again in
this process; `onHealthProbeSucceeded` clears the pause only after that registration (a healthy probe
from a process whose receivers are not registered proves nothing); a new start voids the previous
process's registration; `onBlockedBySetting` / `onSettingGranted` track the setting side. The record
persists through `CaptureHealthStore` (`PreferencesCaptureHealthStore`: identifiers and timestamps
only, so a plain file is appropriate; the in-memory store serves tests).

## 5. Observability

One structured `Log.i` event with tag `PenniLogic`:
`{"event":"capture_health","api_level":36,"target_api":36,"stop_reason":"quota"|null,"standby_bucket":"rare"|null,"standby_bucket_raw":40|null,"capture_health":"paused"|"healthy"|"blocked"|null,"condition":…,"reason":…}`.
The drain worker logs it with the stop reason and bucket and `capture_health: null`; the monitor logs
it with the state. It carries no device identifier, transaction content, amount, message content or
account identifier (`CaptureHealthTest` asserts the exact key set). The taxonomy signal
`client_state.<identifier>` is recorded separately by the surface with only the published attributes.
Sink and retention: the event goes to the local `Log.i` sink only — no telemetry export exists in
this baseline, and any future export names its purpose, aggregation and retention in
`capture-health-identifiers.json` first; the app persists nothing for this event, so its retention
is the device's logcat ring buffer.

## 6. Evidence

| Test | Proves | Where |
| --- | --- | --- |
| `StopReasonTest` | platform values, categories, dispositions, capture-health mapping | JVM, CI, both variants |
| `DurableQueueContractTest` | queue semantics above | JVM, CI |
| `QueueDrainerTest` | quota stop, exhaustive crash points, cancellation, lease takeover, rejection and retry limits: every item applied once, queue empty | JVM, CI |
| `WorkerResultMapperTest`, `QueueDrainWorkerTest` | result mapping, a real `CoroutineWorker` run through `work-testing` on Robolectric SDK 36 with the event logged and no content in it | JVM/Robolectric, CI |
| `TrackingPauseDetectorTest` | detection per API level and profile kind | JVM, CI |
| `CaptureHealthTest`, `CaptureHealthMonitorTest`, `PreferencesCaptureHealthStoreTest`, `AndroidPlatformSignalsTest` | state machine, precedence, event shape, store round trip, platform signals never throw | JVM/Robolectric, CI |

Not claimed: a real force-stop on a device (the detector's platform inputs are exercised through
fakes and the Android reader through a Robolectric smoke test; a force-stop journey is a QA matrix
item once a capture component exists), a Room-backed queue, any sync or capture feature.

## 7. Rollback

Revert the pull request. Nothing consumes the primitives yet; the merged manifest returns to the
PR 1 shape (no WorkManager permissions or components).
