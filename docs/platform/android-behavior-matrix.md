# Android behaviour matrix: API 31, 33, 35 and 36

Android platform baseline, android#57 (`T-AND-07`), recorded for the QA compatibility matrix
(`T-QA-12`). Source of truth: [`android-behavior-matrix.json`](android-behavior-matrix.json). The
block below is rendered from that file and `BehaviorMatrixContractTest` fails when it differs; the
same test checks that every behaviour states all four API levels, that every cited code path and
artifact exists in this repository, that every capture-health binding is a condition published in
[`capture-health-identifiers.json`](capture-health-identifiers.json), and that every scope bullet
of the ticket has a row.

## How to read it

- **API levels.** 31 (Android 12) is the last level before restricted settings; 33 (Android 13)
  introduces them and the `POST_NOTIFICATIONS` runtime permission; 35 (Android 15) enforces
  edge-to-edge, adds the private space, `ApplicationStartInfo.wasForceStopped()` and pending-intent
  cancellation on stop; 36 (Android 16) is the Play-required target with mandatory edge-to-edge,
  default-on predictive back, ignored large-screen restrictions and job-quota changes.
- **Status.** `implemented` means code in this repository handles the behaviour and the evidence
  column names the tests that prove it; `documented` means the baseline records the behaviour and a
  feature ticket will act on it. The scheduling, capture-health and Play Integrity rows were
  `planned_pr2` until the second pull request of android#57 (`android-57-compat-primitives`) landed
  their primitives.
- **Governed devices.** Only API 31 and API 36 emulators exist on the developer machine
  (`Pixel_5_API31`, `Pixel_8_API36`); CI has no emulator. JVM and Robolectric tests run in CI for
  both variants; instrumented tests run locally on the API 36 emulator and their output is attached
  to the pull request. Nothing here claims a physical-device acceptance.
- **Capture-health condition.** The taxonomy condition a behaviour feeds when it degrades or blocks
  automatic capture; `—` when it has no user-visible state.

<!-- matrix:begin (generated from android-behavior-matrix.json; do not edit by hand) -->
| Behaviour | Area | Status | Capture-health condition |
| --- | --- | --- | --- |
| [`edge_to_edge`](#edge-to-edge) | ui | `implemented` | — |
| [`predictive_back`](#predictive-back) | ui | `implemented` | — |
| [`large_screen_restrictions_ignored`](#large-screen-restrictions-ignored) | ui | `implemented` | — |
| [`restricted_settings`](#restricted-settings) | settings | `implemented` | `capture_blocked_by_setting` |
| [`job_quota_and_stop_reasons`](#job-quota-and-stop-reasons) | scheduling | `implemented` | — |
| [`standby_buckets`](#standby-buckets) | scheduling | `implemented` | `capture_paused_by_platform` |
| [`force_stop_detection`](#force-stop-detection) | capture_health | `implemented` | `capture_paused_by_platform` |
| [`private_space`](#private-space) | capture_health | `implemented` | `capture_paused_by_platform` |
| [`notification_runtime_permission`](#notification-runtime-permission) | settings | `documented` | — |
| [`developer_verification`](#developer-verification) | distribution | `implemented` | — |
| [`play_integrity_standard_requests`](#play-integrity-standard-requests) | security | `implemented` | — |
| [`pending_intents_cancelled_on_stop`](#pending-intents-cancelled-on-stop) | capture_health | `documented` | `capture_paused_by_platform` |

### `edge_to_edge`

Apps draw behind the system bars; the app applies insets itself.

| API | Platform behaviour |
| --- | --- |
| 31 | Opt-in: the app calls enableEdgeToEdge() (or WindowCompat.setDecorFitsSystemWindows(false)); otherwise the system fits content below the bars. |
| 33 | Same as 31; three-button navigation bar may be opaque unless the app requests transparency. |
| 35 | Enforced for apps targeting 35: system bars are transparent and content extends behind them; R.attr.windowOptOutEdgeToEdgeEnforcement can opt out. |
| 36 | Enforced with no opt-out for apps targeting 36: windowOptOutEdgeToEdgeEnforcement is deprecated and disabled on Android 16 devices. |

**App handling.** RootSurface applies WindowInsets.safeDrawing exactly once at every composition root; MainActivity still calls enableEdgeToEdge() so API 26-34 behave like 35+; no value resource sets windowOptOutEdgeToEdgeEnforcement; no other main source imports the window-insets API (guarded by import and by call in SourceRules).

**Evidence.**

- TargetConformanceTest: no resource opts out of edge-to-edge enforcement (JVM, CI, both variants)
- PlatformBaselineSourceTest + SourceRulesSelfTest: the window-insets API is imported and used only by RootSurface; every composition root calls RootSurface (JVM, CI; rules proven on planted snippets)
- RootSurfaceTest: injected insets become padding of the inset area (Robolectric SDK 36, CI, debug)
- ApiLevelMatrixTest: the root surface renders on the SDK 31, 33, 35 and 36 runtimes (Robolectric, CI, both variants)
- EdgeToEdgeInstrumentedTest (androidTest, local API 36 emulator only)

Source: https://developer.android.com/about/versions/16/behavior-changes-16

### `predictive_back`

System back animations and the OnBackInvokedCallback model replace onBackPressed()/KEYCODE_BACK.

| API | Platform behaviour |
| --- | --- |
| 31 | Legacy model: onBackPressed() and KEYCODE_BACK are dispatched; OnBackPressedDispatcher works through them. |
| 33 | OnBackInvokedDispatcher exists; predictive animations only when the app opts in with enableOnBackInvokedCallback=true and the developer option is on. |
| 35 | System animations (back-to-home, cross-task, cross-activity) run for apps that opted in; no developer option needed. |
| 36 | For apps targeting 36 the animations are on by default, onBackPressed() is not called and KEYCODE_BACK is not dispatched; 3-button navigation long-press previews the animation; opt-out only via enableOnBackInvokedCallback=false. |

**App handling.** The application opts in with android:enableOnBackInvokedCallback="true"; no activity may set it to false; no onBackPressed()/onKeyDown override exists; screens intercept back only through PenniLogicBackHandler (PredictiveBackHandler over the OnBackPressedDispatcher, which bridges to the platform dispatcher on 33+), whose enabled argument is required and must be true only while there is something in-app to go back to, so no root screen can trap the system back.

**Evidence.**

- TargetConformanceTest: predictive back is enabled for the application and disabled for no activity (JVM, CI, both variants)
- PlatformBaselineSourceTest + SourceRulesSelfTest: no legacy back callback survives; back is registered only through the predictive-back primitive, guarded by import and by call including the trailing-lambda form; every PenniLogicBackHandler call states enabled (JVM, CI)
- PenniLogicBackHandlerTest: progress forwarded, cleared on cancel, onBack on commit; disabled handler falls through (Robolectric SDK 36, CI, debug)
- MainActivityLayoutTest: unhandled back finishes the activity through the dispatcher (Robolectric SDK 36, CI, both variants)
- ApiLevelMatrixTest: unhandled back finishes the activity on the SDK 31, 33, 35 and 36 runtimes (Robolectric, CI, both variants)
- PredictiveBackInstrumentedTest (androidTest, local API 36 emulator only)

Source: https://developer.android.com/about/versions/16/behavior-changes-16

### `large_screen_restrictions_ignored`

On 600dp+ displays the platform ignores orientation, resizability and aspect-ratio restrictions.

| API | Platform behaviour |
| --- | --- |
| 31 | screenOrientation, resizeableActivity, minAspectRatio/maxAspectRatio and setRequestedOrientation() are honoured; large screens may letterbox the app. |
| 33 | Same as 31; device manufacturers may apply per-app compatibility overrides on tablets and foldables. |
| 35 | Same as 33; user-configurable aspect-ratio overrides exist on some large-screen devices. |
| 36 | For apps targeting 36 on displays with smallest width >= 600dp all of those restrictions are ignored: the app is resizable, fills the window and follows the user's orientation. A temporary manifest opt-out property exists on Android 16 and is removed in Android 17. |

**App handling.** No activity declares screenOrientation, resizeableActivity=false or an aspect ratio; no android.window.PROPERTY_COMPAT_* property is declared; no code calls setRequestedOrientation(); RootSurface lays out by the WindowWidthClass of the usable width after insets (compact <600dp, medium 600-839dp, expanded >=840dp with an 840dp single-pane layout cap) so the scaffold stays usable at any width and orientation. The cap is a pane cap, not a readable measure: prose line length is the design system's (T-DSY-01) typography decision.

**Evidence.**

- TargetConformanceTest: no activity restricts orientation, resizability or aspect ratio; no compatibility property opts out (JVM, CI, both variants)
- PlatformBaselineSourceTest: no code requests an orientation (JVM, CI)
- WindowWidthClassTest (JVM, CI); RootSurfaceTest and MainActivityLayoutTest at 360, 700, 1000 and 1280dp in both orientations (Robolectric SDK 36, CI)
- LargeScreenInstrumentedTest (androidTest, local API 36 emulator with `wm size` resized display)

Source: https://developer.android.com/develop/ui/compose/layouts/adaptive/app-orientation-aspect-ratio-resizability

### `restricted_settings`

Sideloaded builds cannot enable sensitive settings until the user unlocks them in App info.

| API | Platform behaviour |
| --- | --- |
| 31 | No restricted settings. |
| 33 | Notification listener and accessibility service locked at install time iff the installer declared a local/downloaded file package source (InstallPackageHelper.enableRestrictedSettings); unlock via App info > Allow restricted settings. |
| 35 | Enhanced Confirmation Mode: file-sourced installs always guarded, other installers guarded unless the device trusts non-allowlisted installers (AOSP default) or the installer is preinstalled/allowlisted; decided lazily at the first toggle. Set extended by the Android 15 CDD to device admin, display over other apps, usage access, the SMS runtime permission and the default SMS and phone roles. |
| 36 | Same set as 35. |

**App handling.** RestrictedSettingsMatrix states every source x setting x API cell for an AOSP-default device (API 33/34 lock keyed on the declared package source; API 35/36 lock keyed on installer trust, decided lazily); CaptureSettingsProbe classifies the install source from getInstallSourceInfo through the app's proxies and renders capture_blocked_by_setting with reason restricted_setting_locked (which changes only the recovery destination) or listener_access_not_granted; the app never reads or changes ACCESS_RESTRICTED_SETTINGS.

**Evidence.**

- RestrictedSettingsMatrixTest, InstallSourceClassifierTest, CaptureSettingsProbeTest, AndroidInstallSourceReaderTest (JVM/Robolectric, CI, both variants)
- RestrictedSettingsMatrixDocTest keeps docs/platform/restricted-settings-matrix.md equal to the code (JVM, CI)

Source: docs/platform/restricted-settings-matrix.md

### `job_quota_and_stop_reasons`

JobScheduler and WorkManager stop jobs for quota, standby and timeout reasons that the app must read and handle.

| API | Platform behaviour |
| --- | --- |
| 31 | JobParameters.getStopReason() exists (API 31); quota is enforced by standby bucket; STOP_REASON_QUOTA, STOP_REASON_APP_STANDBY, STOP_REASON_TIMEOUT. |
| 33 | Same as 31; WorkManager exposes the reason through WorkInfo.getStopReason() and ListenableWorker.getStopReason() from 2.9. |
| 35 | JobScheduler.getPendingJobReason(jobId) explains why a job is pending. |
| 36 | Runtime quota also applies to jobs started in the top state and to jobs running alongside a foreground service; active-bucket apps get a generous but enforced quota; abandoned jobs receive STOP_REASON_TIMEOUT_ABANDONED; getPendingJobReasons(jobId) and getPendingJobReasonsHistory(jobId) list every reason. |

**App handling.** StopReason models the JobParameters/WorkInfo constants including STOP_REASON_TIMEOUT_ABANDONED; StopReasonPolicy maps each category to a disposition that always releases claimed items; QueueDrainer drains a DurableQueue in leased batches, polls the stop signal before every claim and send, releases unsent items on a stop or cancellation and acknowledges only confirmed sends, so a quota stop neither loses nor duplicates queued work; QueueDrainWorker is the WorkManager base worker that reads ListenableWorker.getStopReason() (API 31+) and logs the capture_health event. Capture stays a listener, so a quota stop never pauses capture.

**Evidence.**

- StopReasonTest: platform values, categories, dispositions (JVM, CI, both variants)
- DurableQueueContractTest: at most one copy per key, leases, owner-checked ack/release, lease expiry (JVM, CI)
- QueueDrainerTest: quota stop mid-batch, crash before/after every claim/ack/expireLeases and every send, cancellation mid-send, lease takeover, rejection and retry limits: every item applied exactly once, queue empty (JVM, CI)
- WorkerResultMapperTest and QueueDrainWorkerTest: result mapping never fails a chain; real CoroutineWorker run through work-testing on Robolectric SDK 36 (CI)

Source: https://developer.android.com/about/versions/16/behavior-changes-all

### `standby_buckets`

The app standby bucket bounds how often background work runs.

| API | Platform behaviour |
| --- | --- |
| 31 | Buckets active, working_set, frequent, rare and restricted (restricted added in API 30); UsageStatsManager.getAppStandbyBucket(). |
| 33 | Same as 31. |
| 35 | Same as 31. |
| 36 | Same buckets; in addition Android 16 enforces a runtime quota even in the active bucket. |

**App handling.** StandbyBucket mirrors UsageStatsManager (hidden buckets map to unknown with the raw value kept); StopReasonPolicy.captureHealthReason maps the restricted bucket to standby_bucket_restricted (capture_paused_by_platform -> degraded) and a quota stop to no capture pause; TrackingPauseDetector reads the bucket at start; the bucket travels in the capture_health event.

**Evidence.**

- StopReasonTest: standby buckets mirror UsageStatsManager and only restricted pauses capture (JVM, CI)
- TrackingPauseDetectorTest: restricted bucket pauses, rare does not (JVM, CI)
- CaptureHealthMonitorTest: the event reflects the bucket (JVM, CI)

Source: https://developer.android.com/topic/performance/appstandby

### `force_stop_detection`

A force-stopped app must show tracking paused until capture health is restored.

| API | Platform behaviour |
| --- | --- |
| 31 | Force-stop puts the package in the stopped state (receivers and jobs off until the user opens the app); detectable afterwards only through ApplicationExitInfo.REASON_USER_REQUESTED (API 30+). |
| 33 | Same as 31. |
| 35 | ApplicationStartInfo.wasForceStopped() reports the first start after a force-stop; entering the stopped state also cancels all pending intents. |
| 36 | Same as 35; ApplicationStartInfo.getStartComponent() distinguishes what started the process. |

**App handling.** TrackingPauseDetector: ApplicationStartInfo.wasForceStopped() on API 35+, ApplicationExitInfo REASON_USER_REQUESTED/REASON_USER_STOPPED on API 30+, nothing below 30. CaptureHealthMonitor records capture_paused_by_platform / force_stopped and keeps it until the capture components are registered again and a health probe succeeds; a process start alone never clears it. Record persisted by PreferencesCaptureHealthStore (identifiers and timestamps only).

**Evidence.**

- TrackingPauseDetectorTest: API 35/36 start info, API 30-34 exit reason, below 30 nothing (JVM, CI)
- CaptureHealthMonitorTest: after a force-stop the app shows tracking paused until capture health is restored; a new start voids the previous registration (JVM, CI)
- PreferencesCaptureHealthStoreTest and AndroidPlatformSignalsTest (Robolectric SDK 36, CI)

Source: https://developer.android.com/about/versions/15/behavior-changes-all

### `private_space`

Apps inside a locked private space are stopped and hidden.

| API | Platform behaviour |
| --- | --- |
| 31 | No private space. |
| 33 | No private space. |
| 35 | Private space introduced: locking it stops every app inside, hides notifications and widgets; apps cannot run in the background while locked. |
| 36 | Same as 35. |

**App handling.** TrackingPauseDetector classifies a force-stop or REASON_USER_STOPPED while running in a non-managed profile (UserManager.isProfile && !isManagedProfile, API 33+) as private_space_paused (capture_paused_by_platform -> degraded); recovery is the same registration-plus-probe path. Residual: the public SDK cannot distinguish the private space from a clone profile, so both classify the same way (recorded in capture-health-identifiers.json).

**Evidence.**

- TrackingPauseDetectorTest: a stop inside a non-managed profile is the private space; a work profile stop is a plain force-stop (JVM, CI)
- CaptureHealthMonitorTest: a private-space stop is reported with its own reason and recovers the same way (JVM, CI)

Source: https://developer.android.com/about/versions/15/behavior-changes-all

### `notification_runtime_permission`

Posting notifications needs a runtime permission from API 33.

| API | Platform behaviour |
| --- | --- |
| 31 | No runtime permission; notifications are enabled unless the user turns them off. |
| 33 | POST_NOTIFICATIONS runtime permission; apps targeting 33+ must request it before posting. |
| 35 | Same as 33. |
| 36 | Same as 33. |

**App handling.** The scaffold requests no permission (TargetConformanceTest); the matrix records post_notifications as never locked by restricted settings and not applicable below 33. Requesting it is a feature ticket.

**Evidence.**

- TargetConformanceTest: merged manifest requests no permission of its own; RestrictedSettingsMatrixTest (JVM, CI)

Source: https://developer.android.com/develop/ui/views/notifications/notification-permission

### `developer_verification`

Certified devices block installs of apps from unverified developers.

| API | Platform behaviour |
| --- | --- |
| 31 | Applies to certified devices running Android 7+, so all QA API levels alike; enforcement is regional (Brazil, Indonesia, Singapore, Thailand from 30 September 2026; global 2027) and by participating store, not by API level. |
| 33 | Same as 31. |
| 35 | Same as 31. |
| 36 | Same as 31. |

**App handling.** Sideloaded QA installs use adb (exempt) or the tester's one-time advanced flow; release builds are registered through Play Console; the debug applicationId is a separate package name and is never registered. docs/platform/sideloaded-qa-prerequisites.md and scripts/qa_sideload_prerequisites.py make the prerequisites executable.

**Evidence.**

- scripts/tests/test_qa_sideload_prerequisites.py (Python unit tests, CI)

Source: https://developer.android.com/developer-verification

### `play_integrity_standard_requests`

Standard Play Integrity requests bind a token to the protected request.

| API | Platform behaviour |
| --- | --- |
| 31 | Standard requests need Google Play Store and Play services on API 21+; behaviour does not vary by platform API level. |
| 33 | Same as 31. |
| 35 | Same as 31. |
| 36 | Same as 31. |

**App handling.** ProtectedRequest + IntegrityRequestBinding compute the base64url SHA-256 request hash (43 chars, under the 500-byte limit) over method, path, body digest, account scope, nonce and issue time; StandardIntegrityClient asks the provider for a token bound to that hash and distrusts a mismatched answer; BoundIntegrityToken attaches once, refuses a different request, a second use or a stale token and burns itself; IntegrityVerdictPolicy is the executable fail-closed reference table (wrong package, wrong request, expired, future-dated, replayed, unrecognised, basic-only, unlicensed all deny) that the api repository implements server-side; ClassicRequestRegistry reserves nothing and requires risk + budget for any future reservation; PlayStandardIntegrityTokenProvider is the Play adapter behind the interface (not exercised in tests).

**Evidence.**

- IntegrityRequestBindingTest, BoundIntegrityTokenTest, StandardIntegrityClientTest: binding, single use, wrong request, expiry, unavailable provider (JVM, CI)
- IntegrityVerdictPolicyTest: every denial row plus the Play replay-protection shape (JVM, CI)
- ClassicRequestRegistryTest: empty registry, reservation shape, budget ceiling (JVM, CI)

Source: https://developer.android.com/google/play/integrity/standard

### `pending_intents_cancelled_on_stop`

Entering the stopped state cancels pending intents.

| API | Platform behaviour |
| --- | --- |
| 31 | Pending intents survive the stopped state; alarms and jobs are simply not delivered while stopped. |
| 33 | Same as 31. |
| 35 | All pending intents are cancelled when the app enters the stopped state; they must be re-registered on the next start. |
| 36 | Same as 35. |

**App handling.** Any capture or sync alarm a feature ticket registers must be re-registered from process start; PR 2's CaptureHealthMonitor treats a start after force-stop as paused until that re-registration and a health probe succeed.

**Evidence.**

- Documented; CaptureHealthMonitorTest proves that a start after a force-stop stays paused until re-registration and a successful probe (JVM, CI)

Source: https://developer.android.com/about/versions/15/behavior-changes-all
<!-- matrix:end -->

## Rollback

The target SDK cannot roll back below Play policy. A regression in one behaviour disables the
affected feature behind the taxonomy's `degraded` or `permission_denied` state rather than lowering
the target, and the conformance tests keep the target, the predictive-back opt-in and the absence of
orientation restrictions from regressing silently.
