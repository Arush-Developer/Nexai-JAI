# Replay logic

The rules the replay follows, and where each is enforced.

## Before the first record

`check_replay_order` decides the order and looks for conflicts. `seq` is authoritative when present, as the organisers' README states. Otherwise file order is. Timestamps and record ids never reorder anything.

| Finding | Result |
|---|---|
| Repeated record id, or a missing or repeated `seq` | Always an error |
| File rows not in `seq` order | Conflict |
| A timestamp earlier than the record before it | Conflict |
| An unreadable timestamp | Conflict |

A conflict stops the run by default. Passing `--on-order-conflict report` continues, follows `seq`, and prints each ambiguity. Non-monotonic timestamps then run on a clock that never goes backwards, so an old observation cannot re-enter a window.

## At each record

This is `CausalReplayEngine.step`.

1. **Name the expiry.** If this record is a policy's boundary, its `expires_after_record_id` is set now. Under the exclusive convention the policy also ends now.
2. **Check active policies first.** Match the record's source against active `SOURCE_IP` policies.
3. **If one matches:** action BLOCK, `active_policy_id` is that policy. A4 and A5 do not see this record, so it cannot add evidence or trigger anything.
4. **If none matches:** action ALLOW, `active_policy_id` is `NONE`. A4 updates its windows and groups. A5 decides whether the evidence now justifies a policy.
5. **If A5 activates:** the policy covers later records only. This record stays ALLOW, and earlier actions are never revisited.
6. **Expire.** Under the inclusive convention, a policy whose boundary is this record ends now.

A record with a missing source address is allowed and not analysed, because missing values must not merge into one giant "source".

## State kept

`engine.state()` reports the current position and record, suspicious flows observed, temporal groups, active and expired policy ids, the activation log size, action counts, applied policy ids, STOP status and timestamp regressions. It is saved in `agent4_agent5_evidence.json`.

## Agent 4 groups

A flow is suspicious if A1 calls an attack, A2 flags it, or A3 reports `UNCERTAIN`. A group forms when a source's window holds `min_group_members` (2) suspicious flows, with those earlier flows as its first members. A destination forms a group when `converging_min_sources` (10) different sources make confident attack calls on it inside the window.

When a record has a timestamp, a flow leaves a window after `window_seconds` (300, an engineering default that has not been tuned on real timestamps). Only a record with no timestamp falls back to `window_records` (2,000), and the evidence's `window_basis` then says `records`, so a record count is never described as a time window. A gap longer than the window starts a new group.

## Agent 5 policy criteria

All of these must hold on the triggering record:

- A1 calls an attack, at `min_confidence` (0.90) or above, and A3 does not say `UNCERTAIN`.
- The source has no active policy, and STOP has not been requested.
- A4 has a group for this source or destination.
- The source's window has enough confident attack flows: 2 if A2 also flagged this flow or the destination is a converging group, 3 if only A1 did.
- At least `min_attack_share` (50%) of the source's analysed flows in the window are confident attacks. This protects shared addresses.

Anomaly-only evidence never creates a policy, because A2 flags about 5% of benign flows by design.

## Expiry

A policy lasts `ttl_records` (2,000) replay records, counted in replay order, never wall-clock time. A repeat offender's next policy lasts twice as long, up to `max_ttl_records` (16,000).

**Boundary convention:** `level2.defense.expiry_boundary`. The organisers' stage page confirms inclusive, which is what the system uses.

| Value | Is the record named by `expires_after_record_id` blocked? | Example: activated after R002, expires after R010 |
|---|---|---|
| `inclusive` (used) | Yes | R003 to R010 can be blocked |
| `exclusive` | No | R003 to R009 can be blocked |

The rule checker applies whichever convention is configured.

## End of the replay

- A policy that would outlast the file gets the last record as its expiry.
- If the evidence would justify a policy on the very last record, none is created, because no later record exists to name as its expiry. The log records `SKIPPED_NO_LATER_RECORD`.

A5 knows the file's record count for this purpose only. It is not evidence, and it cannot change any decision about a record. `check_prefix_invariance` confirms that.

## STOP

`engine.stop()`, or `POST /api/defense/stop` on the live replay, stops A5 activating anything further. Active policies run to their recorded expiry, because `policies.csv` has committed to that range and replay must stay deterministic. Separately, `POST /api/replay/stop` halts the whole live replay.

## How causality is enforced

| Guard | What it catches |
|---|---|
| `step` and `observe` refuse a position at or before the last one | Records out of order |
| `assert_causal`, at every activation and at the end | A later record inside any window or group |
| `check_prefix_invariance`, on every `run_level2.py` run | Any earlier decision changing when later records are removed |
| `replace(partition, labels=None)` in `run_level2` | Labels reaching scoring or replay |
