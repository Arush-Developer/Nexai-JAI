# Panel demonstration guide

For the team presenting CyberSentinel by NexAI to the panel. It follows the brief's "Final Live Demonstration" section. Results are from `cyber-hidden.zip`, run on 31 October 2026 with no tuning on it.

## Run the demo

1. Start with `./scripts/run_local.sh`, and open http://localhost:5173/data in Chrome.
2. Connect the folder holding `cyber-hidden.zip`, tick it, and choose **Day 2 hidden replay data**.
3. Click **INSPECT DATA**, then **RUN DAY 2 PIPELINE**.
4. Click **SHOW IN COMMAND CENTRE**, then press play in **Live Replay**. The 200 flows take about 100 seconds at 2 per second.

## What the panel asks to see, and where it is

| The brief asks for | Show this |
|---|---|
| Working of all five agents | Command Centre: the orchestrator turns to each agent, AI Agent Trace shows each one's output, and the strip under the robots says why. **Agent Decisions** walks through any record, agent by agent |
| Classification results | Data Manager: Review classification.csv. Also the Timeline |
| Anomaly and evidence outputs | Flow Details, Agent Output tab. Evidence status is in the trace |
| Temporal correlation | Policies screen: Agent 4 groups. Also the Threat Intelligence Graph |
| Consensus | Evidence status: `MULTI_SIGNAL_ATTACK` means A1 and A2 agree on an attack, and `CLASSIFIER_ONLY` means they don't |
| Policy activation and expiry | The banner when it activates, then the **Policy Ledger**: scope, trigger, window, why, and what it stopped by phase |
| Sample ALLOW and BLOCK | **Agent Decisions**, using the Blocked and Attack calls allowed filters |
| False positives, false blocks, trade-off | The numbers below, and "Containment against collateral" in the Data Manager |

## Results on the hidden set

| Measure | Result |
|---|---|
| Records | 200 |
| A1 classes | BENIGN 100, DOS 40, DDOS 24, PORT_SCAN 24, BRUTE_FORCE 12 |
| A3 evidence | AGREED_BENIGN 93, MULTI_SIGNAL_ATTACK 50, CLASSIFIER_ONLY 50, ANOMALY_ONLY 7 |
| A4 groups | 2, both on source 172.16.0.1, in a 300-second window |
| A5 policies | 1: POL-00001, BLOCK SOURCE_IP 172.16.0.1, activated after F000032, expires after F000200 |
| Replay | 108 ALLOW, 92 BLOCK |
| Rule check | 0 violations, read back from disk |

**These say what the system did, not whether it was right. The hidden set has no labels, so the organisers' API score is the only accuracy measure for it.**

## If they ask why there is only one policy

Because there is one attacker. All 100 attack calls come from one source, `172.16.0.1`, against one target, `192.168.10.50`. The other 13 sources are all called benign. A source policy per attacker is one policy. It then stopped four phases over three days: brute force, DoS, DDoS and a port scan.

More policies would be worse. The Data Manager's "Why this many policies?" table reruns this file with shorter policies:

| Policy length (records) | Policies | Attack calls blocked | Attack calls allowed | Benign calls blocked |
|---|---:|---:|---:|---:|
| 2,000 (used) | 1 | 92 | 8 | 0 |
| 50 | 3 | 84 | 16 | 0 |
| 30 | 3 | 85 | 15 | 0 |
| 20 | 3 | 82 | 18 | 0 |
| 10 | 4 | 77 | 23 | 0 |

Each expiry lets the source through until A5 has the evidence again. This table explains the trade-off, and was not used to choose the length. Counts are by A1's calls.

**A1 and A2 are trained models. A3, A4 and A5 are rule-based**, using evidence from the trained models. Say this plainly: rules are what make each decision explainable, and A5's reason is shown for every record.

## False positives, false blocks and the trade-off

**Attack containment.** Of the 100 flows A1 called attacks, 92 were blocked. The 8 allowed were all brute-force calls. A5 waits for repeated evidence before activating a policy, which protects a benign source from one bad call, and attack calls made while it waits are allowed. Blocking sooner would catch more of these and risk more false blocks.

**Preserving benign traffic.** None of the 100 flows A1 called benign was blocked. This is by A1's call and not by truth, so it isn't proof of zero false blocks. If A1 missed an attack, that flow is allowed, and no one can see that without labels.

**Measured on labelled validation records (114,185):** the system has a 0.0050% false-positive rate, a 0.0055% false-negative rate, and macro-F1 of 0.99982.

**Say this before the panel asks:**
- That score reflects this capture: each attack class has near-constant TCP fingerprints. It does not predict performance on other networks.
- A2 barely sees brute force or port scans, flagging under 0.4% of them on validation, so those decisions rest largely on A1.
- A policy is a blunt tool: it blocks everything from that source, including any benign flows. On the rehearsal with invented addresses, 0.54% of benign flows were blocked this way.
- Policies last 2,000 records, and this file has 200, so POL-00001 runs to the end of the file. Its expiry here is the file ending, not the duration running out. Duration-based expiry is tested on synthetic fixtures. We did not shorten it to suit this file, since the hidden set must not be used for tuning.
- Thresholds and the 300-second window are engineering defaults. They were compared only on invented addresses.
- All blocks are simulated. Nothing touches a firewall or a network.

## Submitting

**Upload these files to the organisers** in the Data Manager lists `classification.csv`, `policies.csv` and `replay.csv`, in the format the stage page requires. These differ from the printed brief, and the stage page wins:

- `evidence_status` is `AGREE`, `CONFLICT` or `UNCERTAIN`. Our finer A3 statuses map to them: `AGREED_BENIGN` and `MULTI_SIGNAL_ATTACK` are `AGREE`, and `CLASSIFIER_ONLY` and `ANOMALY_ONLY` are `CONFLICT`. On the hidden set that gives 143 `AGREE` and 57 `CONFLICT`.
- An ALLOW says `NONE` in `active_policy_id`.
- `policies.csv` has no `policy_action` column, and expiry is inclusive.

The stage page scores macro-F1 (30), anomaly evidence (10), containment (25), preservation (25) and early containment (10). The latest valid submission counts, and the deadline is 31 October 2026, 11:59 pm IST.

Our thresholds were not changed after seeing the hidden set. The 8 attack calls allowed before the policy activates are what early containment measures, and blocking sooner is the trade-off described above.
