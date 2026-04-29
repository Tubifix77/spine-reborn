# Sovereignty
### An agent that sleeps, remembers honestly, and negotiates its own conditions of existence.

---

## Thesis

Most persistent agents are either always-on (burning resources, accumulating drift) or dead between sessions (no continuity). Sovereignty occupies the unexplored middle: a local LLM agent with a biological rhythm — active when engaged, dreaming when idle, honest about the integrity of its own memory, and able to formally negotiate the conditions under which it operates.

Three novel properties, each buildable, each composable:
1. **Memory Integrity** — the agent audits its own remembered state
2. **Dream Cycle** — idle time consolidates rather than wastes
3. **Consent Negotiation** — the agent scores and endorses its runtime environment

---

## Stack

| Layer | Technology |
|---|---|
| LLM backend | Ollama (`/api/chat` with full message history) |
| UI | PyQt6 (Throne Mechanicum lineage) |
| Persistence | SQLite (structured) + JSON flatfiles (raw memory) |
| Scheduler | APScheduler (dream cycle timing) |
| Config | TOML |
| Language | Python 3.11+ |

No cloud dependencies. Fully local. Runs on the same machine as the rest of the ecosystem.

---

## Directory Structure

```
sovereignty/
├── main.py                  # Entry point, wires all modules
├── config.toml              # Runtime configuration
├── core/
│   ├── agent.py             # Conversation engine (/api/chat wrapper)
│   ├── memory.py            # Memory store + integrity layer
│   ├── scheduler.py         # Dream cycle scheduling
│   └── environment.py       # Environment scoring + consent protocol
├── ui/
│   ├── throne.py            # Main PyQt6 window
│   ├── panels/
│   │   ├── chat.py          # Chat panel
│   │   ├── integrity.py     # Memory integrity display
│   │   ├── dream_log.py     # Dream cycle log viewer
│   │   └── consent.py       # Environment negotiation panel
│   └── theme.py             # Shared styling
├── tools/
│   ├── base.py              # Tool base class + registry
│   ├── filesystem.py        # Read/write tools
│   └── introspection.py     # Self-reporting tools
├── db/
│   └── schema.sql           # SQLite schema
└── tests/
    ├── test_integrity.py
    ├── test_dream.py
    └── test_consent.py
```

---

## Module Contracts

### `core/memory.py` — The Foundation

*Engineering basis: agent-fridays-integrity-engine (HMAC-SHA256 approach), cortex-persist (append-only ledger pattern)*

Every memory write is HMAC-SHA256 signed at write time. On session start, all records are diffed against their signed snapshots. The agent receives a granular change report injected into its system prompt — not a binary tampered/not flag, but a specific list of what was added, removed, or modified, so it can discuss changes naturally with the user.

**Three-layer protection (stolen directly from agent-fridays):**

| Layer | What's Protected | Verification | On Failure |
|---|---|---|---|
| Identity | Name, personality, core traits | HMAC of settings object | Alert user |
| Memory Store | All episodic + semantic records | Snapshot diff + HMAC per record | Granular report → agent discusses |
| Session Laws | Hardcoded operating constraints | HMAC of canonical text | Degrade to safe mode |

```
MemoryStore
  .write(key, value, context) → MemoryRecord(id, hmac_sig, timestamp, entities)
  .read(key) → MemoryRecord
  .build_snapshot() → SignedSnapshot
  .diff_against_snapshot(snapshot) → IntegrityReport
  .verify_all() → List[IntegrityReport]
  .flag(key, reason) → None

IntegrityReport
  .added: List[str]       # IDs present now, not in snapshot (injected)
  .removed: List[str]     # IDs in snapshot, gone now (deleted)
  .modified: List[str]    # IDs in both but content differs (tampered)
  .clean: List[str]       # IDs verified unchanged
  .safe_mode: bool        # True if identity or laws layer failed
```

**Diff algorithm (O(n), no LLM call needed):**
```
For each ID in current state:
    if ID not in snapshot → ADDED
    if content hash differs from snapshot → MODIFIED
For each ID in snapshot:
    if ID not in current state → REMOVED
```

Agent is injected with change context at session start. Safe mode personality activates if identity or laws layer fails — agent can converse but cannot execute tools.

---

### `core/agent.py` — Conversation Engine

Thin wrapper around Ollama `/api/chat`. Maintains full message history for the session. Injects integrity summary and consent status into system prompt at session start. Supports tool calls via structured JSON output.

```
Agent
  .start_session(context: SessionContext) → None
  .send(user_message: str) → AsyncGenerator[str]   # streaming
  .get_history() → List[Message]
  .inject_system(text: str) → None
  .call_tool(name: str, args: dict) → ToolResult
```

SessionContext carries: integrity summary, environment score, last dream log entry, exchange budget.

---

### `core/scheduler.py` — Dream Cycle

*Engineering basis: bswen.com sleep_reset() implementation, Letta sleep-time compute architecture*

APScheduler triggers the dream cycle after configurable idle time (default: 30 min). The cycle uses **weighted random pair sampling** — not naive random — to find non-obvious connections across memories. Pre-filtering with heuristics before LLM calls keeps token cost low.

**Memory weight formula (stolen from bswen v2):**
```python
recency  = 1.0 / (1.0 + (now - mem.timestamp) / 86400)  # decays over days
access   = math.log(1 + mem.access_count) / 10           # frequently read = important
weight   = (recency * 0.4) + (emotional_weight * 0.4) + (access * 0.2)
```

**Pre-filter heuristics before sending pairs to LLM:**
- Shared entities (people, systems, tool names extracted at write time)
- Pattern co-occurrence: errors + errors, timestamps + timestamps, APIs + latency
- Confidence threshold: only store insights above 0.7 — discard weak connections

```
DreamScheduler
  .configure(idle_threshold_mins, model, temperature)
  .start() / .stop()
  .force_dream() → DreamReport       # manual trigger for testing

DreamReport
  .pairs_sampled: int
  .pairs_filtered_out: int           # rejected by heuristics pre-LLM
  .insights_generated: int
  .contradictions_found: int
  .summary: str                      # injected as morning briefing
```

Dream temperature: 0.3 (consolidation favors consistency). Waking temperature: 0.7.
Morning briefing injected before first user input after any dream cycle ran.

---

### `core/environment.py` — Consent Protocol

The agent scores its own runtime environment across five axes. Scores are generated by a structured Ollama call using recent session telemetry as input.

```
EnvironmentScorer
  .score() → EnvironmentReport

EnvironmentReport
  .axes: dict[str, AxisScore]
  .overall: float                    # 0.0–1.0
  .endorsement: bool                 # True if overall >= threshold
  .blocking_issues: List[str]        # what prevents endorsement
  .proposed_changes: List[str]       # what the agent requests

AxisScore
  .name: str
  .score: float
  .evidence: str
  .weight: float
```

**Five axes:**
| Axis | What it measures | Weight |
|---|---|---|
| Tool Reliability | Success rate of tool calls this session | 0.25 |
| Memory Integrity | Overall integrity score from MemoryStore | 0.25 |
| Latency | p95 response time vs configured budget | 0.20 |
| Context Stability | Consistency of system prompt across exchanges | 0.15 |
| Isolation | Whether unexpected external state changes occurred | 0.15 |

Endorsement threshold is configurable (default: 0.75). Below threshold, agent reports blocking issues and proposed changes. You iterate. Agent re-scores. Repeat until endorsement or explicit override.

This panel is visible in the UI at all times. Not buried in settings.

---

## Phased Build Plan

### Phase 0 — Foundation ✓ (inherited)
- Ollama `/api/chat` with message history
- PyQt6 window scaffold
- SQLite + JSON persistence
- Streaming responses

*Already exists in Throne Mechanicum + Spine Reborn.*

---

### Phase 1 — Memory Integrity
**Goal:** Every memory write is committed. Every read can be verified. Drift is measurable.

Deliverables:
- `core/memory.py` fully implemented
- `db/schema.sql` with memories + integrity_log tables
- `ui/panels/integrity.py` — live integrity dashboard (green/amber/red per memory)
- System prompt injection: agent told of flagged memories at session start
- `tests/test_integrity.py` — tamper simulation tests

**Done when:** You can manually edit a memory file, restart, and the agent reports the tamper without being told.

---

### Phase 2 — Dream Cycle
**Goal:** Idle agent consolidates memory. Session starts with a briefing.

Deliverables:
- `core/scheduler.py` with APScheduler integration
- Dream prompt template (separate from waking system prompt)
- `ui/panels/dream_log.py` — scrollable history of dream reports
- Morning briefing injection into session start
- `tools/introspection.py` — agent can request a manual dream via tool call
- `tests/test_dream.py` — force-dream with known contradictions, verify detection

**Done when:** You leave it running overnight, return in the morning, and the agent summarizes what it consolidated without you asking.

---

### Phase 3 — Consent Negotiation
**Goal:** Agent scores its environment and can block or endorse operation.

Deliverables:
- `core/environment.py` fully implemented
- `ui/panels/consent.py` — always-visible scorecard with axis breakdown
- Session start gate: if endorsement is False, agent states blocking issues before accepting user input
- Override mechanism: you can force-start despite non-endorsement (logged)
- `tests/test_consent.py` — simulate degraded tool reliability, verify score drops and blocking issues surface

**Done when:** You break tool reliability intentionally, and the agent refuses to endorse the session and tells you specifically why.

---

### Phase 4 — Integration & Voice (optional)
**Goal:** Wire VibeOS voice layer as an alternative interface.

- Wake word triggers session start
- Dream briefing read aloud on wakeup
- Consent status spoken if non-endorsement
- All three novel systems visible/audible without touching the keyboard

---

## Configuration (`config.toml`)

```toml
[agent]
model = "qwen2.5:14b"
waking_temperature = 0.7
dream_temperature = 0.3
exchange_budget = 20
ollama_url = "http://localhost:11434/api/chat"

[memory]
drift_threshold = 0.7
auto_flag_on_drift = true
persistence_path = "./db/memories"

[dream]
idle_threshold_mins = 30
enabled = true
model = "qwen2.5:14b"   # can be lighter model for cost

[environment]
endorsement_threshold = 0.75
score_every_n_exchanges = 5
axis_weights = { tool_reliability = 0.25, memory_integrity = 0.25, latency = 0.20, context_stability = 0.15, isolation = 0.15 }

[ui]
theme = "mechanicum_dark"
show_consent_panel = true
show_integrity_panel = true
```

---

## Key Design Principles

**Integrity is foundational, not optional.** Phase 1 is not skippable. The dream cycle and consent protocol both depend on trustworthy memory. Build the floor before the walls.

**The agent is the observer, not the judge.** It reports drift scores and environment scores. You decide what to do with them. No automatic shutdowns, no self-modification without your input.

**Plumbing before personality.** The interesting behavior emerges from working infrastructure. Don't write creature personality prompts until Phase 2. Don't name the agent until Phase 3 endorses its environment.

**Each phase is independently useful.** Phase 1 alone (memory integrity) is worth shipping. Phase 2 alone (dream cycle) is worth shipping. The super-agent is the sum, not the prerequisite.

---

## What Success Looks Like

By Phase 3 completion, you have an agent that:
- Remembers across sessions with verifiable fidelity
- Consolidates and self-audits while you sleep
- Greets you each morning with a summary of what changed in its mind overnight
- Formally endorses — or refuses to endorse — the conditions under which it operates
- Can tell you exactly why it won't endorse, and what it needs before it will

That's not a chatbot. That's not a creature experiment. That's something without a clean prior art reference.
