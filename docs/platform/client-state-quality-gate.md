# Accepted client-state taxonomy binding

Android consumes the immutable provider for
[PenniLogic/docs#1](https://github.com/PenniLogic/docs/issues/1) (`T-UX-01`), not a second
locally maintained state list. The exact data and schema are vendored under
[`client-state-taxonomy/`](client-state-taxonomy/); [`source.json`](client-state-taxonomy/source.json)
records the full accepted commit, tree, Git blob identities, byte counts and SHA-256 hashes.
The source document is referenced and hashed, not vendored. Nothing is downloaded at test
or application runtime, and the vendored files contain data, not executable provider code.

## Provider and version adoption

The accepted provider is Docs commit
[`a700e639585c61a4610e7b99dbd02b2dab28bdcc`](https://github.com/PenniLogic/docs/commit/a700e639585c61a4610e7b99dbd02b2dab28bdcc),
tree `3879d3893a3f289bd6b0d4474f09dbebf1fef195`, taxonomy **1.1.0**, schema **1**.
Its document's sections
[12 and 13](https://github.com/PenniLogic/docs/blob/a700e639585c61a4610e7b99dbd02b2dab28bdcc/product/client-state-taxonomy.md#12-future-client-gate-adoption)
define future client adoption and additive versioning.

This is an explicit additive migration from Android's former **1.0.0** pin. The existing
eight state identifiers, allowed scopes, four permission-denial causes, three capture-health
conditions and seven platform reasons retain their meanings. Version 1.1.0 publishes those
Android reason identifiers and adds copy variants and ordinary content renderings; adopting
its source does not implement those renderings or register their examples as product surfaces.
`ClientStateTaxonomy.VERSION`, the signal's `taxonomy_version` and the capture-health document's
taxonomy/source block move together. Platform detection, condition/reason behavior, status,
privacy, retention and implementation evidence are unchanged.

## Native assertion

`ClientStateTaxonomyTest` reads the verified provider bytes with the existing Gson and
`RepositoryFiles` helpers. Its `taxonomy_first` assertion compares the actual `ClientState.entries`,
scopes and causes with the accepted source, rather than comparing two Android lists. Android
currently enumerates the complete published state set, which is stricter than the provider's
subset requirement. Capture conditions must be published client-determined conditions with the
same state, cause and single recovery-action identifier; platform reasons must be published under
the same condition. Signals use the provider's prefix, per-state names and exact attribute/value
contract. The existing local platform-permission-name restriction is retained.

The binding also refuses altered or missing data/schema/provenance, duplicate, unknown, missing
or malformed client identifiers and mismatched code/data/metadata/history versions.
`ClientStateGateTest` exercises these refusal paths with in-memory planted defects. Byte pins
protect the accepted data and schema; this is not a new general-purpose schema validator.
Changing a source pin requires another explicit, independently reviewed provider adoption,
not an edit to a local vocabulary.

Both classes are ordinary JUnit tests in the existing debug and release unit-test tasks, so the
unmodified `python scripts/quality_gates.py test` executes them in both variants. A focused run is:

```text
gradlew.bat testDebugUnitTest --rerun --tests com.pennilogic.android.platform.state.* testReleaseUnitTest --rerun --tests com.pennilogic.android.platform.state.* --console=plain --no-daemon --stacktrace
python scripts/check_repository.py
```

Use an exclusively owned `GRADLE_USER_HOME` for concurrent local work. Native evidence requires
both `:app:testDebugUnitTest` and `:app:testReleaseUnitTest` to execute in the current run, not
`UP-TO-DATE`, `FROM-CACHE`, `NO-SOURCE` or `SKIPPED`, and counts come from that run's JUnit XML.
On Windows, retain the existing creation-owned process guardian and its exit/pipe confirmation
before restoring any fixture. No SDK, JDK, formatter, pipeline, process-lifetime or budget contract
is changed by this adoption.

## Coverage boundary

**No product surfaces are registered. `client_state_coverage` is `not_exercised`.** There is no
empty-registry PASS, synthetic rendering claim, new production renderer or provider example
treated as a real registration. The native tests prove source-ID adoption and signal/platform
bindings only, not copy rendering, recovery controls, accessibility, device behavior, full
`T-QA-08` acceptance or hardware acceptance.

The future coverage helper and real surface registrations remain with the client surface/QA
work. They must use the pinned schema's `definitions.surface_registration`, observe the actual
rendering for every applicable state, compare canonical copy and exactly one recovery action,
and prove rejection with omissions, wrong copy, duplicate actions and privacy defects. The
illustrative `example_transactions_home` in the provider belongs to no real client.

## Rollback

Revert this adoption as one unit: vendored source, provenance, native assertions, the code version
and the capture-health taxonomy pin. Do not change capture/platform conditions or reasons as a
shortcut for making a source mismatch green. No product behavior or stored-data migration is
introduced here.
