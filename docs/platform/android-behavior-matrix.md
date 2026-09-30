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
  feature ticket will act on it; `planned_pr2` means the reusable primitive lands in the second pull
  request of android#57 (`android-57-compat-primitives`), which flips the status.
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
| [`job_quota_and_stop_reasons`](#job-quota-and-stop-reasons) | scheduling | `planned_pr2` | — |
| [`standby_buckets`](#standby-buckets) | scheduling | `planned_pr2` | `capture_paused_by_platform` |
| [`force_stop_detection`](#force-stop-detection) | capture_health | `planned_pr2` | `capture_paused_by_platform` |
| [`private_space`](#private-space) | capture_health | `planned_pr2` | `capture_paused_by_platform` |
| [`notification_runtime_permission`](#notification-runtime-permission) | settings | `documented` | — |
| [`developer_verification`](#developer-verification) | distribution | `implemented` | — |
| [`play_integrity_standard_requests`](#play-integrity-standard-requests) | security | `planned_pr2` | — |
| [`pending_intents_cancelled_on_stop`](#pending-intents-cancelled-on-stop) | capture_health | `documented` | `capture_paused_by_platform` |

### `edge_to_edge`

Apps draw behind the system bars; the app applies insets itself.

| API | Platform behaviour |
| --- | --- |
| 31 | Opt-in: the app calls enableEdgeToEdge() (or WindowCompat.setDecorFitsSystemWindows(false)); otherwise the system fits content below the bars. |
| 33 | Same as 31; three-button navigation bar may be opaque unless the app requests transparency. |
| 35 | Enforced for apps targeting 35: system bars are transparent and content extends behind them; R.attr.windowOptOutEdgeToEdgeEnforcement can opt out. |
| 36 | Enforced with no opt-out for apps targeting 36: windowOptOutEdgeToEdgeEnforcement is deprecated and disabled on Android 16 devices. |

**App handling.** RootSurface applies WindowInsets.safeDrawing exactly once at every composition root; MainActivity still calls enableEdgeToEdge() so API 26-34 behave like 35+; no theme sets windowOptOutEdgeToEdgeEnforcement; screens never consume insets themselves.

**Evidence.**

- TargetConformanceTest: themes never opt out of edge-to-edge enforcement (JVM, CI, both variants)
- PlatformBaselineSourceTest: window insets are applied only by the root surface; every composition root goes through the root surface (JVM, CI)
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

**App handling.** The application opts in with android:enableOnBackInvokedCallback="true"; no activity may set it to false; no onBackPressed()/onKeyDown override exists; screens intercept back only through PenniLogicBackHandler (PredictiveBackHandler over the OnBackPressedDispatcher, which bridges to the platform dispatcher on 33+).

**Evidence.**

- TargetConformanceTest: predictive back is enabled for the application and disabled for no activity (JVM, CI, both variants)
- PlatformBaselineSourceTest: no legacy back callback survives; back is intercepted only through the predictive-back primitive (JVM, CI)
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

**App handling.** No activity declares screenOrientation, resizeableActivity=false or an aspect ratio; no android.window.PROPERTY_COMPAT_* property is declared; no code calls setRequestedOrientation(); RootSurface lays out by WindowWidthClass (compact <600dp, medium 600-839dp, expanded >=840dp with an 840dp readable-content cap) so the scaffold stays usable at any width and orientation.

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
| 33 | Notification listener and accessibility service locked for builds installed from a user-acquired file (intent-based installer or a session declaring a local/downloaded file source); unlock via App info > Allow restricted settings. |
| 35 | Lock extended by the Android 15 CDD to device admin, display over other apps, usage access, the SMS runtime permission and the default SMS and phone roles. |
| 36 | Same set as 35. |

**App handling.** RestrictedSettingsMatrix states every source x setting x API cell; CaptureSettingsProbe classifies the install source from getInstallSourceInfo and renders capture_blocked_by_setting with reason restricted_setting_locked or listener_access_not_granted; the app never reads or changes ACCESS_RESTRICTED_SETTINGS.

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

**App handling.** PR 2 adds the StopReason model over the JobParameters/WorkInfo constants, the standby-bucket model and a DurableQueue drain primitive that releases in-flight items on a quota stop so nothing is lost or duplicated; the capture pipeline stays a listener, so a quota stop never pauses capture.

**Evidence.**

- Planned: quota stop and queue durability tests (JVM, CI) in PR 2

Source: https://developer.android.com/about/versions/16/behavior-changes-all

### `standby_buckets`

The app standby bucket bounds how often background work runs.

| API | Platform behaviour |
| --- | --- |
| 31 | Buckets active, working_set, frequent, rare and restricted (restricted added in API 30); UsageStatsManager.getAppStandbyBucket(). |
| 33 | Same as 31. |
| 35 | Same as 31. |
| 36 | Same buckets; in addition Android 16 enforces a runtime quota even in the active bucket. |

**App handling.** PR 2 maps STANDBY_BUCKET_RESTRICTED to the capture-health reason standby_bucket_restricted (capture_paused_by_platform -> degraded) and records the bucket in the capture_health event.

**Evidence.**

- Planned: standby-bucket handling tests (JVM, CI) in PR 2

Source: https://developer.android.com/topic/performance/appstandby

### `force_stop_detection`

A force-stopped app must show tracking paused until capture health is restored.

| API | Platform behaviour |
| --- | --- |
| 31 | Force-stop puts the package in the stopped state (receivers and jobs off until the user opens the app); detectable afterwards only through ApplicationExitInfo.REASON_USER_REQUESTED (API 30+). |
| 33 | Same as 31. |
| 35 | ApplicationStartInfo.wasForceStopped() reports the first start after a force-stop; entering the stopped state also cancels all pending intents. |
| 36 | Same as 35; ApplicationStartInfo.getStartComponent() distinguishes what started the process. |

**App handling.** PR 2 adds TrackingPauseDetector (wasForceStopped on 35+, ApplicationExitInfo fallback on 30-34) and CaptureHealthMonitor, which keeps capture_paused_by_platform / force_stopped until the capture components are registered again and a health probe succeeds.

**Evidence.**

- Planned: force-stop recovery tests (JVM, CI) in PR 2

Source: https://developer.android.com/about/versions/15/behavior-changes-all

### `private_space`

Apps inside a locked private space are stopped and hidden.

| API | Platform behaviour |
| --- | --- |
| 31 | No private space. |
| 33 | No private space. |
| 35 | Private space introduced: locking it stops every app inside, hides notifications and widgets; apps cannot run in the background while locked. |
| 36 | Same as 35. |

**App handling.** PR 2 classifies a stop while running in a non-managed profile as private_space_paused (capture_paused_by_platform -> degraded); the public SDK cannot name the private space, so the doc records the residual ambiguity with clone profiles.

**Evidence.**

- Planned: private-space recovery tests (JVM, CI) in PR 2

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

**App handling.** PR 2 adds the request-hash binding, single-use bound tokens, a client TTL and a fail-closed reference verdict evaluator; server-side verification belongs to the api repository; classic requests are reserved to a documented registry with risk and quota budget.

**Evidence.**

- Planned: binding, replay, stale-token and adverse-verdict tests (JVM, CI) in PR 2

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

- Documented; exercised by PR 2 force-stop recovery tests

Source: https://developer.android.com/about/versions/15/behavior-changes-all
<!-- matrix:end -->

## Rollback

The target SDK cannot roll back below Play policy. A regression in one behaviour disables the
affected feature behind the taxonomy's `degraded` or `permission_denied` state rather than lowering
the target, and the conformance tests keep the target, the predictive-back opt-in and the absence of
orientation restrictions from regressing silently.
