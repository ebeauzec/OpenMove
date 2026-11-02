# Safety

OpenMove runs against production clouds. The owner's requirement (R13, R14) is that no failure of any kind, in OpenMove, the network, a host, an array or a person, may cause an outage. This document is the safety case: the hazards, the control for each, where the control is in the code and which test proves it.

## What this document claims, and what it does not

* **Claimed and tested today.** Everything marked "built" below. Those controls are in the code and covered by automated tests, run against a mock cloud.
* **Required but not yet built.** The controls marked "planned" belong to the migration engine. The engine does not exist yet. It must not be released until each of them is built, tested with injected faults, and proven in the lab.
* **Not claimed.** That nothing can go wrong. No software can promise that. The claim is narrower and checkable: OpenMove changes nothing without passing every gate; the source volume is only read until the cutover; and every failure before the cutover leaves production as it was.
* **Never tested against a real cloud.** No release is validated. Until a passed lab validation is recorded for a release (playbook Phase 0), OpenMove treats that cloud as read only.

## Principles

1. **Read-only by construction.** Reading code cannot change a cloud. The HTTP client refuses every call that is not a read.
2. **Fail closed.** If OpenMove cannot decide, it refuses. An error while checking a condition is a refusal, never a pass.
3. **Ask again, every time.** Conditions are checked before each change, not once at the start. A gate that went stale or a Halt pressed a second ago stops the next change.
4. **The order cannot be bent.** Pre-checks, validation, copy, sync, two rechecks, cutover. No phase can be skipped, and a failed check moves a volume backward, never forward.
5. **The source is never touched before the cutover.** It is read. Nothing is written to it, locked, frozen or deleted until the cutover, and it is kept after it until the soak period ends.
6. **One thing at a time, one volume at a time.** A failure is limited to one volume. A failing volume stops itself and never blocks or damages another.
7. **Every step is written down first.** The intent and its inverse go in the journal before the step runs. A restart reads the journal and cannot guess.
8. **An unknown outcome is not a success.** A change that timed out may or may not have happened. The state is read back before anything else.
9. **A person can always stop it.** Halt works at once, from the screen, with no condition.
10. **Gentle on production.** Copy rates and concurrency are capped per host. Nothing is installed on a production host by default. Anything that would change a host is a plan step that needs approval, and a site can forbid it.

## Hazards and controls

| # | Hazard | Control | Status | Where | Test |
|---|---|---|---|---|---|
| 1 | A bug in reading code changes the cloud. | The HTTP client refuses every method except GET and HEAD and the login, before anything is sent. The only write path, `mutate`, needs a permit. | built | `connectors/httpclient.py`, `connectors/openstack.py` | `test_safety.ReadOnlyClientTests` |
| 2 | Code outside the connector grows a write call. | A source scan fails the build if any module other than the connector and the safety module names `PUT`, `DELETE` or `PATCH`. | built | `tests/test_safety.py` | `SourceScanTests` |
| 3 | A change on an untested OpenStack release. | The release profile must show a recorded, passed lab validation. | built | `profiles.py`, `safety.py` | `test_safety.PermitTests`, `test_cloud` |
| 4 | A change while a check is failing, a prerequisite is open, or sign-off is missing. | The permit requires the gate to be `approved`: no failure, every open item attested, two different people signed, run not older than 24 hours. | built | `checks/gate.py`, `safety.py` | `test_checks.GateFlowTests`, `PermitTests` |
| 5 | Conditions change after the run starts (stale gate, withdrawn sign-off, trouble on the array). | The permit checks again before every change. | built | `safety.Permit.check` | `PermitTests.test_conditions_are_checked_again_for_every_change` |
| 6 | The decision code itself crashes. | Any exception while checking is a refusal. | built | `safety.Permit.check`, `httpclient.Http.request`, `lifecycle.cutover_ready` | `PermitTests.test_a_broken_gate_function_fails_closed`, `ReadOnlyClientTests.test_a_permit_that_raises_or_says_no_is_a_refusal` |
| 7 | Something looks wrong and a person needs to stop everything now. | Halt: one button, a name and a reason. The next change is refused, and the permit refuses until someone resumes with a name and a reason. | built | `safety.halt`, `/api/halt` | `HaltTests`, `ServerSafetyTests` |
| 8 | Steps run out of order, or a step is skipped. | The lifecycle state machine allows only the listed transitions and requires evidence for each. State is read from the journal. | built | `lifecycle.py` | `test_lifecycle` (14 tests) |
| 9 | Cutover on a copy that was good once but is not now. | The cutover needs two passed rechecks in a row, at least five minutes apart, the newest no older than fifteen minutes, source unchanged between them, nothing pending, gate approved now, no halt. A failed recheck resets the count. | built | `lifecycle.cutover_ready` | `LifecycleTests` |
| 10 | The source changes while it is being copied. | Sync follows it; the source fingerprint must be identical at both rechecks. | state machine built; sync planned | `lifecycle.py` | `test_source_change_between_rechecks_blocks_the_cutover` |
| 11 | A change times out and nobody knows if it happened. | `outcome_unknown` is set on the error. No retry exists in the client. The engine must read state back. | built (client); engine planned | `httpclient.py` | `test_unknown_outcome_is_flagged_for_changes_only` |
| 12 | OpenMove or the machine stops mid-migration. | Phase and intent are in the journal, written before the step. A restart finds unresolved steps and resumes from the last completed one; it cannot jump ahead. | phase state built; step journal built; engine planned | `lifecycle.py`, `journal.py` | `test_state_survives_a_restart` |
| 13 | A person asserts something false to get past a gate. | A fail cannot be attested. Attestations need a name and evidence, are journaled and appear in the report. Two different people must sign. | built | `checks/gate.py` | `GateFlowTests` |
| 14 | A check that cannot run is mistaken for a pass. | Such checks are "not checked", name the access they wait for, and keep the gate open until attested. | built | `checks/run.py` | `RunTests.test_statuses_are_honest` |
| 15 | The demonstration is mistaken for a real run. | Demo mode uses a throw-away data folder, refuses to connect to a real cloud, labels every screen and report, and its synthetic validation is marked in the journal. | built | `demo/`, `server.py` | `test_demo`, `test_checks.test_demo_validation_is_labelled` |
| 16 | A password or token leaks. | The password lives in memory only. The journal scrubs secrets. Errors never contain headers. | built | `connectors/`, `journal.py` | `test_core`, `test_cloud` |
| 17 | Another program on the machine drives the interface. | Per-run token, Host header check, loopback only by default, Content Security Policy. | built | `server.py` | `test_cloud` |
| 18 | The copy puts too much load on production. | Per-host rate and concurrency caps; health check before each copy; impact measured in the lab. | planned | engine | lab |
| 19 | Data differs after the copy. | Size and hash compare of every image before sync; compare again at each recheck; staging snapshots must match Cinder's by id. | planned | engine | fault-injection |
| 20 | The cutover fails or half-completes. | Rollback R2 restores the source record and unlocks the instance; rollback R3 reverses the migration and keeps writes since the cutover. The source is kept until the soak ends. | planned | engine | lab T1 to T21 |
| 21 | A host, array or cloud service fails during the copy. | The step fails safe: the staging volume is discarded and the volume goes back to precheck. The source was only read. | planned | engine | fault-injection |
| 23 | A volume of the wrong class is moved: an encrypted, multiattach, grouped or replicated volume, a volume on a stopped instance, or a class A or B volume that has gained a snapshot. | The class is recorded at precheck; classes D and E are refused there; classes A and B are refused at validation unless no snapshot is left. | built | `lifecycle.py` | `test_handling.ClassRuleTests` |
| 24 | A class C volume is cut over with a snapshot history that does not match, or while tenants change its snapshots. | The copy cannot leave `copy` unless every image compared identical and the snapshot ids match; every recheck must reconcile; a failure sends the volume back or stops it. | rules built; rebuild and compare planned | `lifecycle.py` | `ClassRuleTests` |
| 25 | A volume is left unmigrated and nobody knows why or what to do. | Every unmigrated volume is written up with cause, timeline, state left behind and proposed steps (report and `GET /api/unmigrated`). | built | `handling.py`, `reports/build.py` | `test_handling.EnterpriseRunTests` |
| 26 | A volume that hosts a workload that may break (a legacy Windows server, no guest agent, an emulated disk bus) is moved. | Check V11 fails it and it is not admitted. Only two different people with evidence can clear it. A cleared volume is held for release and is watched. The check says it cannot see inside a guest. | built (hints only) | `risk.py`, `checks/run.py` | `test_selection.RiskTests` |
| 27 | The wrong volumes are migrated, or the whole cloud at once. | Only selected, admitted volumes start. The default leaves out high-risk volumes. A non-admitted id is refused. The selection cannot change during a run. | built | `selection.py`, `server.py` | `test_selection.SelectionTests` |
| 28 | A setting is set looser than the process allows, or silently changed. | Settings are validated against floors. A value below its floor is refused, never clamped. Rules from the operator can only be stricter than the process minimum. | built | `selection.py`, `lifecycle.py` | `SelectionTests`, `VolumeViewTests` |
| 29 | A run keeps failing and nobody notices. | The run halts itself after the set number of failures. Only a named person with a reason can resume. | built in the simulation; the engine must call the same rule | `demo/simulate.py` | `HoldAndStopTests` |
| 30 | A volume is cut over or released from the soak without a second person having looked. | G2 and G3 are conditions of the cutover and G4 of the soak; the signer must not be the operator; a sign-off is void when the volume is copied again, falls back to sync or is cut over again. | built | `lifecycle.py` | `test_lifecycle`, `test_selection.VolumeGateTests` |
| 22 | Cleanup removes the wrong thing. | Cleanup acts only on objects the journal says OpenMove created, by id; it shows the list and needs approval; it never touches the source before the soak ends. | planned | engine | fault-injection |
| 31 | A change is sent twice, or a lost answer is taken for a failure and sent again. | `Actions._change` never retries. A timeout, a dropped connection or a 5xx leaves the step open, halts everything, and the next tick reads the volume back and closes the step with what the cloud shows. | built | `engine/actions.py`, `engine/runner.py` | `test_engine.FailureTests` |
| 32 | The instance is changed while its volume moves, or is left locked. | The instance is locked before the retype (unless someone else locked it, then it is left alone) and unlocked only after the post-check passed or after the rollback. A failed post-check leaves it locked. | built | `engine/runner.py` | `test_engine.RunTests`, `FailureTests` |
| 33 | A volume that changed since it was validated is moved. | Everything is read again at the moment of the retype and any difference gives the volume up. | built | `engine/runner.py` | `test_engine.RunTests` |
| 34 | A failed post-check is accepted. | The post-check halts everything; `lifecycle.enter("soak")` refuses a volume whose post-check failed, also with G4 signed. | built | `lifecycle.py`, `engine/runner.py` | `test_engine.FailureTests` |

## Tests that must pass before the engine can be released

1. Fault injection for every step that changes anything: kill OpenMove, drop the network, time out the call, make the array return an error, between every pair of steps. After each, the volume is either unchanged or in a state the journal names and OpenMove can resume or roll back from.
2. A property test of the lifecycle: any sequence of calls leaves a volume in a legal state, and no sequence reaches the cutover without the conditions above.
3. The playbook's lab validation (T1 to T21) on a copy of the real release, signed, recorded as a validation for that release.
4. A measured copy under production-like load, with the rate caps in place.

## Reviewing this

Everything above can be checked by reading three small modules (`safety.py`, `lifecycle.py` and the top of `connectors/httpclient.py`) and their tests. Run the tests with:

    python -m unittest discover -s tests -t .

Report a hole in a control by opening an issue; a control without a test is treated as not built.
