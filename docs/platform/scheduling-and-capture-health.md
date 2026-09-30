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
  concurrently; `ack`, `release`, `retryLater` and `deadLetter` are accepted only from the lease holder;
- `release` gives back an item that was **not sent** (platform stop, cancellation): pending again at
  once, no attempt counted, no backoff; `retryLater` records a **completed send attempt that failed**
  and is the only operation that counts toward the attempt limit, so any number of platform stops can
  never bring an item closer to dead-lettering;
- `expireLeases` returns leases that outlived their owner (crash, quota stop mid-send) to pending and
  counts a lease expiry, kept apart from attempts because the item may never have reached the server
  (a poison-item cap on that counter is a feature decision, not the queue's);
- `deadLetter` **parks** an item — the server rejected it for good or `maxAttempts` completed sends
  failed — with its payload, key, attempt counts, timestamps and reason, exposed through
  `deadLetters()`; nothing is deleted, `enqueue` keeps refusing the key, and an operator or QA can
  inspect, re-queue or export it;
- every dead-letter and rejection reason is an identifier (`[a-z][a-z0-9_]{0,63}`), never free server
  text, so no content can enter local diagnostics through that path;
- every operation is atomic.

`QueueDrainer` drains in batches: it polls the platform stop signal before every claim and before
every send, gives every claimed-but-unsent item back (`release`, no attempt counted) when the signal
arrives — under `NonCancellable`, as it does when the coroutine is cancelled (WorkManager stopping
the worker) —, acknowledges an item only after the sender confirmed it, treats a sender that throws
(network, serialization) as a retry rather than a lost item or a failed run, parks a rejected item
once, backs off a retried item (`retryLater`, 30 s doubling to one hour) and parks it with reason
`max_attempts` when `maxAttempts` completed sends have failed. The drainer never reads the payload;
amounts and currencies stay inside it.

**What "no loss, no duplication" means and how it is proven.** `QueueDrainerTest` runs a fake server
that applies each idempotency key once and counts every receipt. Across a quota stop after four
sends; ten quota stops before any send followed by one transient retry (the head item stays queued
with zero attempts spent, one copy); a process crash before and after **every queue operation the
drain actually performs** — the crash plans are derived from the recorded call counts of three
scenarios (all sends succeed; one transient retry and one rejection; a quota stop after the first
send), so `claim`, `ack`, `release`, `retryLater`, `deadLetter` and `expireLeases` are all covered and
every plan is asserted to have crashed where intended — and before and after every send; coroutine
cancellation mid-send; and a lease takeover by a second owner, every queued item ends up applied
exactly once or parked as a dead letter with its payload, and no live item remains. Two honest
residuals: a crash between a successful send and its acknowledgement re-sends that single item under
the same key, which the server deduplicates — at-least-once delivery with idempotent application,
which is what "without losing or duplicating queued transactions" requires from a client; and a send
that outlives its lease (60 s) lets a second drainer take the lease and send the same key
concurrently, which the same idempotency handles.

## 3. WorkManager adapter

`QueueDrainWorker` is the base `CoroutineWorker` for scheduled drains. It builds the drainer with an
owner unique to the run, hands the platform stop reason (`ListenableWorker.getStopReason()` on API
31+, `unknown` below) to the drain, logs the `capture_health` event with the stop reason and standby
bucket, and maps the outcome with `WorkerResultMapper`: drained → success (retry when items wait for a
server-requested retry); stopped → retry for scheduler-deferred, transient and defect reasons,
success (chain ends, items wait, surface shows the pause) for user restrictions and app cancellation;
never `failure`, so a chain is never abandoned by a platform stop. `QueueDrainWorkerTest` runs the
real worker through `work-testing` on the happy path, on the polled stop path (a quota stop after
the first send: the unsent items are pending again with no attempt spent, the event carries
`stop_reason: quota`, the result is `retry`) and on the cancellation path WorkManager actually uses
for a `CoroutineWorker` (`onStopped()` plus cancellation of the future `startWork()` returned: the
`NonCancellable` give-back runs and every claimed item is pending again). WorkManager 2.12.0 adds
four normal (install-time) permissions and its services and receivers to the merged manifest; they
are listed in `SCAFFOLD.md` and allow-listed by `TargetConformanceTest`.

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
| `ApplicationStartInfo.wasForceStopped()` on the first start after a stop | 35+ | `force_stopped`, or `private_space_paused` when the process runs in a non-managed profile; the platform's answer decides on its own (`false` is not a force-stop, whatever the last exit reason says) |
| `ApplicationExitInfo.getReason()` = `REASON_USER_STOPPED` (the profile the app ran in was stopped: private space locked, work profile off) | 30+ | `private_space_paused` in a non-managed profile, else `force_stopped` |
| `ApplicationExitInfo.getReason()` = `REASON_USER_REQUESTED` | 30+ | **not read as a stop.** Android documents the value for a force-stop *or* a swipe from Recents (before API 34 also an app update), exposes no public sub-reason and `getDescription()` is free-form; a swipe leaves receivers and jobs intact, so reading it would show "tracking paused" after every swipe |
| `ActivityManager.isBackgroundRestricted()` | 28+ | `background_restricted` |
| `UsageStatsManager.getAppStandbyBucket()` = restricted | 28+ | `standby_bucket_restricted` |
| nothing detectable | 26–29, and a force-stop on 30–34 | none; capture health is re-probed at start instead |

**Limitation, recorded for T-QA-12:** on API 30–34 a force-stop is not detectable with public APIs
(see the `REASON_USER_REQUESTED` row), so the pause is shown only when the platform can say so (API
35+) or when the profile was stopped. `AndroidPlatformSignalsTest` runs the reader on the SDK 31, 33,
35 and 36 runtimes and proves the guards (`wasForceStopped()` is null below 35, the profile reads as
personal below 33, every read returns rather than throws); `TrackingPauseDetectorTest` drives the
detector by the shape of those answers, including "start info says no, exit reason says
`REASON_USER_REQUESTED`" → no pause.

The public SDK cannot name the private space: `UserManager.isProfile() && !isManagedProfile()` is
true for the private space and for a clone profile alike, and both are classified as
`private_space_paused`. This residual is recorded here and in the identifiers document. The
classification records a concealment choice of the user (running the app inside a private space):
it stays on the device as a per-device state record and a logcat-only event, would only ever be
counted in aggregate, is never joined to an account identifier, is not exported by this baseline and
is erased with the record (below).

`CaptureHealthMonitor` is the state machine behind "after force-stop recovery the app shows tracking
paused until capture health is restored": `onProcessStart` records a detected pause and **never
clears one**; `onCaptureComponentsRegistered` records that the capture components exist again in
this process; `onHealthProbeSucceeded` clears the pause only after that registration (a healthy probe
from a process whose receivers are not registered proves nothing), whatever the pause reason — a
lifted background restriction or an improved standby bucket clears through the same path, never
through a start alone; `onPlatformPause` records a pause a drain worker observed at runtime (its stop
reason and standby bucket mapped by `StopReasonPolicy.captureHealthReason`); a new start voids the
previous process's registration; `onBlockedBySetting` / `onSettingGranted` track the setting side,
fed by `CaptureSettingsProbe` (notification access, restricted-setting lock) and
`CapturePermissionProbe` (`ContextCompat.checkSelfPermission` for the runtime permission the capture
feature names — a read, never a request; this baseline declares no capture permission, so it reads as
not granted); `clear` erases the record.

**Persisted artefact and its lifecycle (erasure inventory).** `PreferencesCaptureHealthStore` writes
the private SharedPreferences file `pennilogic.capture_health` with exactly the keys `pause_reason`,
`paused_since`, `components_registered`, `block_reason`, `block_permission` — reason identifiers, one
timestamp, one boolean and a platform permission name; nothing else. The file is excluded from cloud
backup and device transfer by the application's extraction rules. It is erased by
`CaptureHealthStore.clear()` (`CaptureHealthMonitor.clear()`), which the sign-out and account-erasure
flow of the account ticket calls, so a `private_space_paused` indicator and its timestamp never
outlive the account on the device; uninstall removes it as well. The in-memory store serves tests.

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
| `DurableQueueContractTest` | queue semantics above, incl. unsent give-back counting no attempt however often, dead letters retained with payload, identifier-only reasons | JVM, CI |
| `QueueDrainerTest` | quota stop mid-batch; ten stops then one transient retry (one copy, zero attempts spent); crash before and after every queue operation the drain performs, plans derived from recorded call counts and asserted to crash; crash around every send; cancellation; lease takeover; rejection and retry limits with payload retained | JVM, CI |
| `WorkerResultMapperTest`, `QueueDrainWorkerTest` | result mapping; the real `CoroutineWorker` through `work-testing` on Robolectric SDK 36: happy path, polled quota stop (items back, no attempt spent, `stop_reason: quota`, `retry`), cancellation of the running work (`NonCancellable` give-back) | JVM/Robolectric, CI |
| `TrackingPauseDetectorTest` | detection by the shape of the platform answers: start-info decides, `REASON_USER_STOPPED` honoured, `REASON_USER_REQUESTED` inconclusive, profile kind, restriction precedence | JVM, CI |
| `AndroidPlatformSignalsTest` | the reader's API-level guards on the SDK 31, 33, 35 and 36 runtimes; every read returns | Robolectric, CI |
| `CaptureHealthTest`, `CaptureHealthMonitorTest`, `PreferencesCaptureHealthStoreTest` | state machine, precedence, runtime pause recording, erasure, event shape, store round trip and `clear()` | JVM/Robolectric, CI |
| `CapturePermissionProbeTest`, `AndroidPermissionStateReaderTest` | `capture_permission_not_granted` from a granted/denied permission, platform-name rule, feed into the monitor; the platform reader against Robolectric's permission state | JVM/Robolectric, CI |

Not claimed: a real force-stop on a device (the detector's platform inputs are exercised through
fakes and the Android reader through Robolectric on four SDK levels; a force-stop journey is a QA
matrix item once a capture component exists), force-stop detection on API 30–34 (see §4), a
Room-backed queue, any sync or capture feature, any permission request.

## 7. Rollback

Revert the pull request. Nothing consumes the primitives yet; the merged manifest returns to the
PR 1 shape (no WorkManager permissions or components).
