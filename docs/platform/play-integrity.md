# Play Integrity: standard requests bound to the protected request

Android platform baseline, android#57 (`T-AND-07`), pull request 2. Source of truth:
`app/src/main/kotlin/com/pennilogic/android/platform/integrity/`. Architecture decision:
`docs/architecture/02-security-architecture.md` §5.3 (standard requests for protected server
actions, token bound to the exact request, validated on the backend; classic requests reserved for a
documented highest-value threat). Operation-specific policy for adverse or unavailable verdicts is
the versioned matrix of `T-SEC-05` (PenniLogic/android#27, the client hardening ticket), not this
baseline; server-side verification is PenniLogic/api#86.

## 1. Division of responsibility

| Where | Does | Owner |
| --- | --- | --- |
| Android (this repository) | computes the request binding, asks Play for a standard token bound to it, attaches the token exactly once to exactly that request within a client TTL, never fabricates or downgrades a token, reserves classic requests to a registry that must name risk and quota budget | android#57 |
| Server (`PenniLogic/api`) | decrypts the token (Play's decryption endpoint or local keys), verifies `requestDetails` (package name, request hash, freshness), keeps the replay registry with retention ≥ `maxAge + clockSkew`, evaluates the app/device/account verdicts per operation and decides; **the authoritative verification lives there** | [PenniLogic/api#86](https://github.com/PenniLogic/api/issues/86) — server-side Play Integrity verdict verification: nonce issuance, request binding, replay registry and fail-closed policy (blocked by android#57) |
| Operation policy | which protected actions require a verdict, what each may do on an adverse or unavailable one, which QA cohorts may exercise which operations | `T-SEC-05` = [PenniLogic/android#27](https://github.com/PenniLogic/android/issues/27) (client hardening; consumes the server verdict endpoint) |
| Play Console / Cloud project | enables the Play Integrity API for the app and provides the Cloud project number | release ticket; **not created by this baseline** |

The reference decision table (`IntegrityVerdictPolicy`) is kept executable in the Android
repository so the fail-closed behaviour android#57 requires is tested and so the client interprets a
server-relayed summary identically; it is not a substitute for the server implementation.

## 2. The binding

`ProtectedRequest` names everything that gives a server request its meaning: upper-case method,
absolute path, hex SHA-256 of the exact body bytes, an opaque server-issued account scope (never an
email or name), a client nonce of at least 16 characters, and the issue time. Amounts and currencies
stay inside the body and are covered by its digest; they are never repeated in the binding. No string
field may contain a control character (below U+0020, or U+007F); `ProtectedRequest` refuses one at
construction. `IntegrityRequestBinding.requestHash` is the base64url SHA-256 (43 characters, far
under Play's 500-byte limit) of the canonical serialization `pennilogic-integrity-v1 \n method \n
path \n bodySha256 \n accountScope \n clientNonce \n issuedAt`; because no field can contain the
`\n` delimiter (or any other control character) the serialization is injective — two different
requests never produce the same bytes, so a change in any field changes the hash. The api recomputes
exactly this string, byte for byte, from the request it received. Play returns the value verbatim
inside the signed verdict (`requestDetails.requestHash`); the server recomputes it from the request it
actually received and refuses a mismatch. The client nonce is **not** a replay defence: it only makes
two otherwise identical requests distinct. Replay defence is the server's replay registry (§4) plus
Play's own protection.

## 3. Client flow

1. `StandardIntegrityClient.bind(request)` computes the hash and asks the `StandardIntegrityTokenProvider`
   for a token bound to it. The production provider is `PlayStandardIntegrityTokenProvider`
   (`IntegrityManagerFactory.createStandard`, one `prepareIntegrityToken` per process with the Cloud
   project number, then `request(requestHash)` per action); tests use a fake and never call Google.
2. The result is `Bound(BoundIntegrityToken)` or `Unavailable(cause)` (`play_not_available`,
   `network`, `too_many_requests`, `provider_invalid`, `client_error`, `unknown`). A provider that
   answers for a different hash is treated as unavailable, never trusted. Unavailable means no token:
   the operation's `T-SEC-05` policy (android#27) decides what the action may do without a verdict;
   the client never fabricates a token or falls back to an unbound one.
3. `BoundIntegrityToken.attachTo(request, now)` produces the `X-PenniLogic-Integrity` header value
   exactly once: a different request (`wrong_request`), a second use (`already_used`) or a token
   older than the client TTL of five minutes or dated in the future (`expired`) is refused, and a
   refused token is burnt so a retry must obtain a new one. The raw token never appears in the
   `toString()` of `BoundIntegrityToken`, of the provider's `IntegrityTokenResult.Token` or of the
   `IntegrityAttachment`, so a log line that interpolates any of them cannot leak it.

## 4. Server verification (reference decision table)

`IntegrityVerdictPolicy.evaluate(verdict, expectation, replays)` first refuses a replay registry
whose retention does not cover the freshness window (`retentionMillis ≥ maxAgeMillis +
clockSkewMillis`, default 10 min + 60 s) with an `IllegalArgumentException` — a misconfigured
registry can never allow anything — and requires a non-blank token identity. It then applies, in this
order, denying on the first failure:

| Check | Denial | Note |
| --- | --- | --- |
| `requestDetails.requestPackageName` = expected package | `wrong_package` | the debug and release application ids are different packages |
| `requestDetails.requestHash` = hash recomputed from the received request | `wrong_request` | binding |
| `now − timestampMillis` ≤ max age (default 10 min) | `expired` | Play's own freshness window is shorter; this is the server ceiling |
| `timestampMillis − now` ≤ clock skew (default 60 s) | `future_timestamp` | |
| token identity not seen before | `replayed` | **Invariant the api must keep:** the registry retains token identities for at least `maxAge + clockSkew` (default 10 min + 60 s); a shorter retention re-opens replay inside the freshness window, and `evaluate` refuses such a registry outright. `InMemoryReplayRegistry.covering(expectation)` is the reference shape. Play also protects standard requests against replay; when it does, the verdicts come back `UNEVALUATED` and are denied below. The client nonce is not part of this defence |
| `appIntegrity.appRecognitionVerdict` = `PLAY_RECOGNIZED` | `app_not_recognized` | `UNRECOGNIZED_VERSION` and `UNEVALUATED` deny |
| `deviceIntegrity.deviceRecognitionVerdict` contains `MEETS_DEVICE_INTEGRITY` | `device_integrity_not_met` | basic-only, virtual-only and empty deny |
| `accountDetails.appLicensingVerdict` = `LICENSED` when the operation requires a licence | `unlicensed` | `UNLICENSED` and `UNEVALUATED` deny; an operation may declare it needs no licence |

`IntegrityVerdictPolicyTest` proves each row, that the same token presented again anywhere inside
its freshness window is `replayed`, that a registry with insufficient retention is refused, and the
shape Play returns when its own replay protection triggers. Sideloaded QA builds are `UNLICENSED` by
definition (no Play entitlement); the `T-SEC-05` matrix (android#27) decides which operations a QA
cohort may exercise — production policy is never weakened for them.

## 5. Classic requests

Classic requests cost seconds of latency, count against the default quota of 10,000 requests per day
across all installs, and leave replay and exfiltration protection to the app. `ClassicRequestRegistry`
is the only place one may be justified; **this baseline reserves none**. A feature ticket that needs
one adds a `ClassicRequestReservation` (snake_case action id, risk statement, daily quota budget,
approving ticket); `ClassicRequestRegistryTest` enforces the shape, unique action ids and a total
budget under half the default quota, so standard-request fallbacks and retries never starve.

## 6. What is and is not covered

- Covered by JVM tests in CI, both variants: binding determinism, sensitivity to every field and the
  control-character rule that makes the canonical form injective; single use, wrong-request burn,
  expiry and future-dated refusal; token redaction in every `toString()`; provider unavailability and
  mismatched-hash distrust; every verdict denial including replay across the whole freshness window
  and the refusal of a short-retention registry; registry rules.
- Not covered and not claimed: a call to Google Play services (the adapter compiles and is wired
  behind the interface; its first real run is a feature ticket's instrumented test with a Cloud
  project number), server-side decryption and storage of the replay registry (PenniLogic/api#86), the
  operation policy for adverse or unavailable verdicts (`T-SEC-05`, android#27), Play Console
  configuration.
- Dependency: `com.google.android.play:integrity:1.6.0` adds `PlayCoreDialogWrapperActivity`
  (`exported="false"`) and the `com.google.android.gms.version` meta-data to the merged manifest;
  listed in `SCAFFOLD.md`.

## 7. Rollback

Revert the pull request; no action depends on integrity tokens yet.
