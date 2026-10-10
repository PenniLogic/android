# Scaffold-first privacy traffic inspection

Android #16 / T-QA-09 owns this source, shared policy and assertions. This is not
product, emulator, physical-device, native CI, signed-RC or release acceptance.
The original eight acceptance criteria and five Definition of Done items remain
mandatory. The source harness is built before product journeys and release
candidates, as the original issue permits.

## Actual providers and ownership

`app/src/main/res/raw/privacy_traffic_policy.json` is the only packaged policy.
`PrivacyTrafficPolicy` reads it on Android; `scripts/privacy_traffic/policy.py`
reads those same bytes. The actual `app_start` and `capture_health` emitters use
`PrivacyEventLogger`, which validates before logging. A refusal logs only the
declared `privacy_payload_refused` event and fixed code. Native tests compare the
packaged bytes with the harness source and run the actual producers against it.
This is instrumentation binding, not an analytics implementation.

The scaffold has **no network destinations, diagnostic host names, analytics transport or ingestion,
clarification or analytics journey providers**. Those registries are deliberately
empty, not wildcard allowlists. `run-rc` returns exit **2**, naming each missing
provider. Source probes always report `observed_journeys: []`, all three real
journeys unmet and `release_qualified: false`. Starting the app or reaching a
synthetic route cannot satisfy real journey coverage.

The component registry pins every external coordinate in the native debug and
release runtime resolution (132 / 127 at the accepted scaffold). It grants none
of those libraries permission to send traffic. The app-project native inventory
task resolves actual dependencies; the Python assertion compares both complete
variant lists, including versions. A new component or destination requires a
reviewed shared-policy change. Neither command updates the registry automatically.

Owned source surfaces:

| Path | Responsibility |
| --- | --- |
| `app/src/main/res/raw/privacy_traffic_policy.json` | Shared destination, component and payload policy |
| `app/src/main/kotlin/com/pennilogic/android/observability/PrivacyTrafficPolicy.kt` | Native policy consumer and actual logger boundary |
| `PenniLogicApplication.kt`, `platform/scheduling/QueueDrainWorker.kt` | Bind existing instrumentation, without product or permission changes |
| `app/src/test/kotlin/com/pennilogic/android/observability/PrivacyTrafficPolicyTest.kt` | Actual native producers, resource identity and planted drift |
| `scripts/privacy_traffic/` | Bounded capture, assertions, ephemeral TLS, scrubbed storage, signature primitive and rollout model |
| `scripts/privacy_traffic_harness.py` | Source commands and explicit RC refusal |
| `scripts/tests/test_privacy_traffic.py`, `scripts/tests/fixtures/privacy_traffic/synthetic-network.json` | Synthetic process/socket controls and one shared native/Python test fixture |

Generated workflow commands, agent policy and command documentation are adopted
from the accepted canonical Infra generator; they are never hand-edited. Hooks,
checkers, setup and the fresh native component extractor retain their existing
bytes. This source adoption does not supply the separate proxy-enabled RC runner
or change its coordinator-owned integration boundary.

## Local commands and runtime

The task-specific dependencies are pinned in
`scripts/privacy_traffic/requirements.txt`: cryptography 50.0.2, pyOpenSSL 26.4.0,
cffi 2.1.1, pycparser 3.0 and typing_extensions 4.15.0 on Python below 3.13.
Use Python 3.10 or newer. Keep the scaffold's JDK 21 / SDK 36 toolchain and
unchanged Gradle/Kotlin/AndroidX versions.

On Windows, from this checkout:

```powershell
python -m venv build\privacy-venv
.\build\privacy-venv\Scripts\python.exe -m pip install -r scripts\privacy_traffic\requirements.txt
$priorPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = Join-Path (Get-Location) 'build\privacy-venv\Lib\site-packages'
    python scripts\privacy_traffic_harness.py self-test
    python scripts\privacy_traffic_harness.py source-probe clean
    python scripts\privacy_traffic_harness.py source-probe raw
    python scripts\privacy_traffic_harness.py run-rc
} finally {
    $env:PYTHONPATH = $priorPythonPath
}
```

`clean` returns 0 only for **source** assertions. Planted leaks return 1. Invalid
inputs, runtime drift and missing RC providers return 2, not a skipped-tool PASS.
`self-test` executes subprocesses, verified TLS handshakes, intercepted HTTP bytes
and real loopback origin responses; it also exercises storage and Ed25519.
Windows subprocess controls reuse `scripts/windows_processes.py` and its owned
Job/confirmed-shutdown boundary. The one unexercised literal-link control on
Windows without symbolic-link privilege is reported as a skip; the actual
Windows junction control still runs. No host privilege or setting is changed.

Use the **base** `python` executable with these process-local isolated libraries
when running the complete Windows script suite. The Windows venv redirector is
an additional process: the existing creation-pinned inherited-writer regression
correctly refuses its different startup PID. The documented base-runtime command
preserves that assertion and all ownership budgets; it does not monkey-patch
identities, change global Python packages or weaken the fixture. On a clean
hosted Linux runner, restoring the pinned requirements into the job's selected
Python runtime needs no Windows redirector workaround.

For one complete script-suite pass with the same privacy evidence summary, use
`python scripts/privacy_traffic_harness.py self-test --all-scripts` in that runtime.
It selects every `test_*.py`, including the focused privacy tests, once. Failures
from either subset remain failures; the summary's test count covers the full
suite and retains actual skips, capture runs and boundary observations. The
combined mode also preserves ordinary unittest's warning policy: `default` when
no explicit Python warning options exist, otherwise the caller's `PYTHONWARNINGS`
and `-W` selection, without overriding explicit `ignore` or `error`. The focused
command without the option keeps its existing warning behavior. Do not run the combined mode
and then repeat focused privacy or full unittest discovery as a CI optimization.

Native inventory and source assertion:

```powershell
.\gradlew.bat :app:privacyComponentInventory --init-script scripts\privacy_traffic\components.init.gradle --no-configuration-cache --console=plain --no-daemon --stacktrace
# Save only the JSON after the single PRIVACY_COMPONENT_INVENTORY prefix as the inventory input.
# Within the process-local runtime block above:
python scripts\privacy_traffic_harness.py check-components build\privacy-component-inventory.json
```

Run the scaffold's existing build, test, lint, coverage, self-test and script
suite, using the isolated Python runtime for the latter. No hosted CI rerun,
emulator claim or release qualification follows from local results. The generated
CI restores this runtime, runs `self-test --all-scripts` once, then runs the separate
fresh native component inventory assertion. No focused or full-discovery duplicate
is retained. The accepted source binding and generation command are recorded in
[SCAFFOLD.md](../../SCAFFOLD.md#explicit-combined-script-self-test); a new composed
native run must still establish the unchanged job and full-workflow budgets.

## Capture and refusal boundary

The source fixture lives under `scripts/tests/fixtures`, never in the packaged
destination registry. It declares only `*.synthetic.invalid` probe routes and a
clearly synthetic component. The probe's ingestion/clarification/analytics names
are **synthetic path checks**, not application journeys.

Both services bind `127.0.0.1` on ephemeral ports. The interceptor accepts bounded
HTTP/1.1 CONNECT, performs a real TLS interception and forwards only validated
JSON to its owned loopback TLS origin. It never resolves an arbitrary requested
host or opens a remote upstream socket. A per-process CA, leaf keys and signing
test keys exist only in memory. Upstream and client certificate verification
remain enabled; no certificate file or trust-store mutation is required.

Each run admits at most 64 connections/requests, 8 KiB headers, 64 KiB bodies,
1 MiB aggregate inspected bytes and 20 seconds of capture. A final budget-refusal
counter may be the 65th diagnostic, never a 65th admitted payload. A declared
oversized body is refused before reading it. Duplicate headers/JSON keys,
compression, transfer encoding, opaque data, malformed/truncated traffic,
unexpected headers, stale run binding, upstream failures and unknown outcomes
are explicit failures. Empty traffic is never evidence of success.
The same absolute monotonic deadline reaches every plain/TLS header/body receive,
send, handshake, upstream connection and idle listener. Native socket waits are
clamped to the remaining budget; completion is checked again after a read/write.
Continuing sub-two-second activity cannot restart the 20-second deadline.
Timeouts close the owned connection and stop its listener; teardown still
requires joined threads. The real slow-CONNECT test uses the full 20 seconds,
not a reduced budget or only an injected clock.

Requests are asserted before forwarding. The raw synthetic sentinel, JSON-escaped
text, headers/URLs, derived sentinel digest, monetary fields or numeric analytics
values, unknown destinations and schema/allowlist drift are planted negatives.
DNS grammar alone never establishes safe metadata or non-raw origin. Names are
retained only on exact membership in the immutable source policy's destination
names or its separate `diagnostic_hosts` metadata list. Diagnostic membership
does **not** grant network permission. The packaged scaffold list is empty; the
source fixture names only the ordinary `unlisted.synthetic.invalid` negative.
Case normalization can return that same declared constant, never an arbitrary
request spelling. Unknown opaque names, private identifiers, delimiter/case/
escape/disguised digests, URLs and user info are withheld before inventory or
violation insertion. `hosts_withheld` records their count and the violation
keeps its fixed code/count with `host: null`; no real violation is dropped.
Strict report/storage/signature validation repeats that source-membership check.
There is no digest-detection heuristic or auto-enrollment of observed hosts.
Any future real RC host naming still needs reviewed source-bound inventory and
provenance; the original full unknown-host criterion remains unaccepted here.
Undeclared field names and their values are not echoed.

ACCEPTED ADR-018 at Docs commit `3e4afcb9575badf8669a50136da69fcc6f634500`
requires `*_bidx`, `raw_event_digest`, `raw_hash`, `message_digest` and other
raw-derived artefacts to stay local. They cannot enter an outbound source schema;
real synthetic wire controls prove refusals. `source_event_id` is refused until a
real producer can prove opaque, non-raw origin: a string shape is insufficient.
This does not ban source Git/policy hashes, signature metadata or hashes of
already scrubbed, non-financial evidence. No money codec, currency registry,
ingestion feature or analytics feature is introduced.

## Evidence, signatures and retention

Only source-declared host inventories, withheld-host counts, allowed payload-field inventories, violation codes/counts,
capture counters, policy identity and run/expiry metadata survive assertion.
Request/response values, bodies, URLs, headers and their raw-derived hashes are
never written. Strict document validation runs before output. Invented real
journeys, missing inventories, inconsistent counters, extra fields, opaque or
oversized evidence, stale nonce/policy bindings and expired evidence are refused.

Source-only private persistence:

```powershell
# Within the process-local runtime block above:
python scripts\privacy_traffic_harness.py source-probe clean --store build\privacy-evidence --owner-id <32-hex-owned-store-id>
```

`EvidenceStore` creates a **new** named directory privately (0700 on POSIX; a
protected current-owner/System/Administrators DACL on Windows). It never repairs
or relaxes an existing public directory. Files use exclusive creation, are
verified private, physical, single-link and bound to the opened file identity.
Existing evidence, mismatched ownership, aliases, junctions and foreign entries
are refused. Reopening needs the same store ID; it is custody metadata, not
identity attestation. Retention is positive and at most 24 hours (default one
hour); at most 64 evidence files are retained. Writes purge expired owned
evidence first. A stable, private, single-link `privacy-store.lock` is bound by
device/inode and random lock ID to the `privacy_store_v2` custody marker.
Purge, capacity admission and exclusive evidence creation share one
interprocess kernel lock: Windows no-share `OPEN_EXISTING` with no reparse
following, or POSIX `flock`. Contention has a five-second bounded refusal,
not a stale-PID lease or a configurable capacity. Handles are non-inheritable;
exception and process crash release them without deleting/replacing the lock.
Unknown, public, hardlinked, missing, replaced or mismatched locks are refused.
The actual two-process 63-to-64 interleaving admits one writer and refuses the
other; real crash/recovery and exact 64/65 boundary controls also execute.
No OS setting, privilege or shared service is changed. Older v1 markers are
refused unchanged, never silently repaired or migrated; create a new owned store.
The filename comes from the validated immutable report snapshot, not mutable
caller data changed during lock admission.
That snapshot is now captured **before any report schema check**: only bounded
built-in JSON values are recursively copied into privately owned containers,
with the existing byte/node/depth limits enforced during capture. Unsupported
types, non-finite numbers, excessive inputs and structural mutation have fixed,
echo-safe refusals; there is no custom converter, shared nested copy or fallback.
The snapshot becomes immutable canonical bytes before validation. Every report
check reads a private parse of those bytes, and the exact same bytes are returned
for persistence; run ID, filename and expiry facts come from that copy.
Concurrent changes can produce only a closed bounded snapshot that passes every
check or a refusal, not an unvalidated artifact. This is not an atomic transaction
over arbitrary caller-owned graphs or a hostile same-owner-code sandbox.
Storage, source probe output and signing/verification consume the validated copy,
never another serialization of the mutable original. Signing binds its payload,
run context and signature to the same bytes even if the caller changes while the
signer runs. Normal validation/signing never mutates the caller.
Purging validates the complete named set before deleting only
expired leaves; siblings and unknown files are preserved. A runner must also
schedule expiry cleanup and bound published artifact retention to one day;
local expiry checks alone do not prove remote deletion.

`pack.sign_pack` implements a domain-separated Ed25519 signature over the exact
canonical, scrubbed report. Verification requires a caller-supplied trusted
32-byte public key and out-of-band expected run ID, not an issuer claimed inside
the pack. `verify_pack` requires a non-empty, exact lowercase 32-hex context
before runtime checks, parsing or cryptography. `None`, booleans, bytes, collections
and malformed strings cannot select unbound verification. The report validator
can omit a context only for newly built source evidence; an explicit invalid
context is refused there too. The CLI keeps a required typed `--run-id` and
echo-safe fixed errors. The actual crypto tests cover tampering, wrong keys, wrong domains,
missing signer, malformed pack and stale evidence. Only test keys are generated,
in memory; no private signing key is persisted or accepted as a CLI argument.

`verify-pack --trusted-public-key <public-key-file> --run-id <expected-run-id>`
verifies **source-synthetic packs only** and still returns
`release_qualified: false`. A checksum manifest is not a signature, and successful
cryptographic verification does not prove an approved signer, protected producer,
publication provenance or RC coverage. The actual mandatory signed RC pack,
release record attachment, Data Safety consumer and component-inventory consumer
remain **UNMET** until those providers exist and are independently qualified.

## Rollout, rollback and canonical handoff

The original rollout is reporting for **one identified RC**, then blocking.
Rollback is reporting for a recorded RC with an explicit expiry; evidence is
always required. `ReportingWindow`/`privacy_exit` test that decision boundary,
including the next RC, expiry and missing evidence/providers. They do not verify
owner authorization or history themselves. There is no CLI reporting switch,
default reporting mode or approved production window: `run-rc` still fails.
The coordinator must supply and verify the owner-authorized rollout record and
one-RC history before later adoption. Reverting to reporting without that record
is not an authorized rollback.

Canonical inputs/outputs that remain owner-controlled:

| Owner | Required input/adoption | Output |
| --- | --- | --- |
| Infra canonical source owner | Restore pinned Python task runtime; retain existing native commands; require one `self-test --all-scripts` followed by fresh component inventory assertion | Source wiring adopted; current native `CI` evidence and unchanged job/full-workflow strict under-600-second acceptance remain required |
| Infra proxy-runner owner | Explicit isolated synthetic account/device, proxy routing, no opaque/unintercepted traffic, bounded teardown and retention | Real RC capture transport and trusted runner evidence |
| Android journey/analytics owners | Actual ingestion, clarification and analytics adapters consuming the packaged schema; approved destinations; deny-permission usability | Real route, component, payload and journey evidence |
| Release/Security owner | Approved signer, external trusted public key, RC APK identity and qualified protected-source attestation | Verifiable mandatory RC evidence pack, not a test signature |
| Coordinator/Compliance consumers | Authorized reporting RC/expiry, protected publication provenance, release artifact attachment and consumer adoption | Reviewable release record, Data Safety/component-inventory evidence before beta |

[PenniLogic/infra#24](https://github.com/PenniLogic/infra/issues/24)'s report-only closure does not qualify the still-open trusted-producer
requirement in [PenniLogic/infra#22](https://github.com/PenniLogic/infra/issues/22).
No key for the old Governance App is requested, no token/workflow permission is
widened and no custom CheckRun or fabricated approval replaces the native job.
Independent non-author Core, affected-risk and QA review, native CI, current-base
PR-only integration, resolved threads and empty bypass remain required.
The independent f39/e6 and e669/e6 **FAIL** records remain immutable.
Author correction/regression results do not relabel them or constitute a new
independent positive review. Root must bind the retained Core/risk and QA review
contexts to the corrected exact head. The separately scoped finite QA PASS
without the N1 case does not override the e669 Core FAIL. N1's validation-before-
serialization gap also existed in f39; it is not described as introduced by e669.

This correction changes only the Android source interface: the packaged policy
adds `diagnostic_hosts`, source reports add mandatory `hosts_withheld`, private
stores use v2 lock custody, `read_http` requires an absolute deadline, and pack/
explicit report verification rejects invalid context. CLI commands and runtime
pins, native component lists, quality commands and fixed budgets are unchanged.
These new source hashes/schema fields require Root's later serial canonical
review/adoption. Frozen Infra A/B and all generated consumers remain untouched.

## Four source-finding regression bindings

The tests in `scripts/tests/test_privacy_traffic_boundaries.py` use actual owned
loopback sockets, private persistence, Ed25519 and native kernel/process paths:

| Finding | Named regression and additional boundary controls |
| --- | --- |
| F1 - encoded host data retained | `test_encoded_raw_derived_hostname_is_scrubbed_before_private_persistence_and_signing`; case/delimiter/escape/private-ID variants; direct report/store/real signed-payload revalidation; declared unknown-host naming without network permission |
| F2 - resetting CONNECT inactivity wait | `test_connect_absolute_capture_deadline_with_real_continuing_activity`; full real 20-second stream, timeout/listener exit and joined helpers; late completion and already-expired deadline refusal |
| F3 - missing expected run disables binding | `test_missing_expected_run_id_cannot_disable_real_signature_binding`; before-runtime/parse invalid-type controls; valid/wrong/expired real packs and actual CLI missing/empty/malformed/wrong/valid context |
| F4 - capacity race | `test_two_real_store_processes_cannot_admit_65_files`; two actual spawned writers at 63 files; exact 64/65 boundary, exception/crash/busy recovery, private marker/lock/link/replacement/legacy refusals and immutable admission snapshot |

`scripts/tests/test_privacy_traffic_snapshot.py` covers N1 at the earlier window.
`test_caller_thread_cannot_insert_private_field_after_checks_before_serialization`
uses a real caller thread and line-trace scheduling, without replacing the
production validator, serializer, schema, lock or budget. It reproduces the e669
private-write failure before correction and checks actual private readback and
cleanup afterward. Additional tests cover the first schema check's detached
containers, nested field/host/run/policy/counter/journey/expiry changes, mutation
during bounded copying, immutable byte snapshots, real signing after caller
mutation, caller preservation, and exact byte/node/depth/type/refusal controls.
The existing 63-to-64 two-process store test remains required and unchanged.
This N1 correction changes no policy/report field, CLI command, runtime pin,
native resource, capture/store limit or canonical consumer; later exact-source
hash/review adoption still belongs to Root.

Native tests exercise the same packaged metadata policy, source-only fixture
and default-deny network boundary, in addition to all original producer tests.

## Original acceptance and Definition of Done mapping

These are source-versus-UNMET mappings, not completed issue checkboxes. Names
below are in `scripts/tests/test_privacy_traffic.py` unless marked native.

| Original acceptance criterion | Named source demonstration | Still UNMET |
| --- | --- | --- |
| A planted raw message string in an outbound payload fails the run | `test_planted_raw_message_fails_real_outbound_capture`; escaped/header/URL controls | Real RC/journey run and required pipeline adoption |
| A planted monetary value in an analytics payload fails the run | `test_integer_minor_units_and_currency_are_forbidden_in_analytics`; `test_money_disguised_as_allowed_analytics_field_value_is_refused`; native monetary boundary | Actual feature analytics/RC instrumentation |
| A request to a host outside the allowlist fails the run and names the host | `test_unknown_destination_is_named_only_as_safe_host`; actual TLS CONNECT refusal | Real RC host inventory and runner gate |
| The evidence artifact is attached to the release record and reviewable without rerunning the pipeline | `test_scrubbed_evidence_is_reviewable_without_rerunning_capture`; private process persistence | Protected release publication/attachment |
| The run covers ingestion, clarification and analytics, not only application start | `test_required_synthetic_path_missing_fails_source_run`; `test_actual_rc_command_refuses_all_missing_real_providers` | **All three real application journeys**; probes do not satisfy this criterion |
| Adding a new third-party component without updating the allowlist fails the run | `test_new_component_without_allowlist_update_fails_real_process`; actual native inventory; route/schema drift tests | RC component/host inventory adoption |
| Signed evidence pack consumed by Data Safety and component inventory before beta | `test_real_ed25519_signature_and_tamper_wrong_key_and_domain_refusals`; `test_real_signed_source_pack_is_verified_by_the_actual_cli_without_rc_approval` | Approved signer, protected producer, actual signed RC pack and consumers |
| Every RC is inspected once builds exist; a new undeclared destination fails release | `run-rc` fails closed; real-process unknown-host, missing-provider and one-RC/expiry controls | Actual RC pipeline/runner rollout, RC identity and release coverage |

| Original Definition of Done | Source disposition |
| --- | --- |
| Current-head separate qualified Core reviewer attestation before merge | **UNMET**; two author reviews are not independent review or approval |
| Every acceptance criterion demonstrated by named test or attached evidence | Named source tests above; all remaining real RC/consumer requirements stay **UNMET** |
| Ticket tests run in CI and are required by branch protection | **UNMET**; source commands are wired through generated CI, but current composed native execution and real RC runner qualification are not established here |
| Observability, rollout and rollback notes on the ticket before merge | Manual notes here; coordinator-owned ticket publication still **UNMET** |
| Declared dependencies mirrored as native GitHub blockedBy edges | Read-only task-start verification found [#1](https://github.com/PenniLogic/android/issues/1), [PenniLogic/infra#24](https://github.com/PenniLogic/infra/issues/24) and [PenniLogic/infra#3](https://github.com/PenniLogic/infra/issues/3) present and closed; no graph change made, and that is not RC acceptance |

ADR-018 additionally has named source controls
`test_blind_index_value_never_serialized`,
`test_raw_derived_digest_not_serializable`,
`test_raw_digest_stays_local_even_under_an_allowed_field_name` and source-schema
refusals. Actual device/RC non-egress and opaque-ID origin proof remain pending.
