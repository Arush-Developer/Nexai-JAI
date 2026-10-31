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
| Working of all five agents | Command Centre: the orchestrator turns to each agent, and AI Agent Trace shows each one's output |
| Classification results | Data Manager: Review classification.csv. Also the Timeline |
| Anomaly and evidence outputs | Flow Details, Agent Output tab. Evidence status is in the trace |
| Temporal correlation | Policies screen: Agent 4 groups. Also the Threat Intelligence Graph |
| Consensus | Evidence status: `MULTI_SIGNAL_ATTACK` means A1 and A2 agree on an attack, and `CLASSIFIER_ONLY` means they don't |
| Policy activation and expiry | Active Policies, and the Data Manager's Policy activity table |
| Sample ALLOW and BLOCK | Timeline cards, or Review replay.csv |
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

## Before submitting

The Data Manager's **Upload these files to the organisers** section lists the three files: `classification.csv`, `policies.csv` and `replay.csv`, in the names and headers of the organisers' templates. These differ from the printed brief: `replay.csv` uses `active_policy_id`, and `policies.csv` has no `policy_action` column. Once the organisers confirm the expiry rule, set `level2.defense.expiry_boundary` and `level2.expiry_boundary_confirmed` in `configs/default.yaml`, restart, and process the file again.
