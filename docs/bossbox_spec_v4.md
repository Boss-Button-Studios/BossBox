# BossBox — Project Specification v4.0

**Studio:** Boss Button Studios  
**Document Status:** Living Draft  
**Version:** 4.0  
**Last Updated:** 2026-03-19  
**Supersedes:** v3.0

---

## Table of Contents

1. [Product Overview](#1-product-overview)
2. [Core Values](#2-core-values)
3. [Target Users](#3-target-users)
4. [System Architecture](#4-system-architecture)
5. [Model Tier System](#5-model-tier-system)
6. [Provider Abstraction Layer](#6-provider-abstraction-layer)
7. [Skill Profiles](#7-skill-profiles)
8. [Task Pipeline and Agentic Loop](#8-task-pipeline-and-agentic-loop)
9. [Document Ingestion and Trust Pipeline](#9-document-ingestion-and-trust-pipeline)
10. [Security Model](#10-security-model)
11. [GUI Shell](#11-gui-shell)
12. [Notifications](#12-notifications)
13. [Community Library](#13-community-library)
14. [Distribution and Packaging](#14-distribution-and-packaging)
15. [Licensing and Open Source Strategy](#15-licensing-and-open-source-strategy)
16. [Future Work and Open Problems](#16-future-work-and-open-problems)
17. [Atomic Implementation Steps](#17-atomic-implementation-steps)

---

## 1. Product Overview

BossBox is a staffing agency for AI assistants. You describe the job. BossBox figures out who is right for it, assigns the work, keeps them on task, and makes sure the output matches what you actually asked for. It runs on your own hardware, manages the models automatically, and ships free and open source. An ordinary gaming PC from around 2020 should be able to play.

More precisely: BossBox manages a tiered fleet of small language models, orchestrates them into agentic pipelines, and provides a supervised human-in-the-loop interface for accomplishing complex tasks. It ships as a self-contained installable package that detects the host hardware, recruits an appropriate model portfolio, and is immediately useful without configuration.

The defining differentiator is the managed runtime: BossBox handles model acquisition, hardware-appropriate tier assignment, execution sandboxing, document trust arbitration, and a hypervisor-isolated security layer — capabilities that no existing local AI tool combines into a single package aimed at non-developer users.

BossBox is free, open source, and ships under the Boss Button Studios label.

---

## 2. Core Values

These values are not marketing positions. They are the design constraints against which every product decision is evaluated. When a proposed feature or optimization conflicts with a core value, the value takes precedence.

**Local-first.** The user's data, goals, and work products stay on their machine. Cloud features are optional and explicitly user-initiated. Nothing phones home.

**Honest about constraints.** BossBox does not pretend to do things it cannot do on the user's hardware. It tells the truth about what a given configuration can and cannot accomplish.

**Security without obscurity.** BossBox's security architecture is fully described in this document, published in the open source repository, and documented in a formal research paper. Security does not depend on attackers not knowing how it works. This is a deliberate choice: we believe robust security systems should be able to withstand scrutiny, and we intend BossBox to demonstrate that principle. The non-oracle user interface — presenting minimal information on security events — is not a product limitation. It is an intentional security decision grounded in the finding that detailed feedback enables attacker refinement. This choice is linked directly to this value and will be explained as such to users who ask.

**User in control.** The system acts on behalf of the user, not autonomously. The user can see what the pipeline is thinking, stop it at any point, redirect it, and review everything it has done. Nothing is irreversible. As IBM stated: *"A computer cannot be held accountable, therefore a computer must never make a decision."* This principle has permanent real estate in the BossBox interface.

**Free and open.** No feature restrictions, no telemetry, no advertising, no paid tier. The security architecture is contributed to the public domain of ideas. Sustainability comes from voluntary community support.

**Resource-conscious.** BossBox is a high-performance toolset running itself. It makes efficient use of available hardware and is honest about minimum viable configurations. Below certain hardware thresholds, BossBox will tell the user plainly rather than running badly.

---

## 3. Target Users

### 3.1 Beginner / Technical-Adjacent

- Has heard of local AI, wants to use it without deep setup
- Comfortable installing software, not comfortable with command lines
- Needs: hardware-aware onboarding, sane defaults, immediate utility, plain-language explanations

### 3.2 Intermediate

- Understands models at a high level, wants to customize behavior
- Comfortable editing configuration, not writing orchestration code
- Needs: skill profile editor, pipeline visibility, model swap controls, security posture control

### 3.3 Power User / Pro

- Builds custom pipelines, writes skill profiles from scratch, integrates cloud APIs
- Comfortable with YAML, Python concepts, and system internals
- Needs: full config access, provider abstraction hooks, expert execution modes, CLI access

All three users share the same application. Complexity is progressive — the beginner never sees what they don't need yet.

---

## 4. System Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    BossBox Shell (GUI)                    │
│  Task Input │ Pipeline │ Skills │ Models │ Security Center│
└─────────────────────────┬────────────────────────────────┘
                          │
┌─────────────────────────▼────────────────────────────────┐
│                    Supervisor Core                        │
│      Task State Machine │ Router │ Audit Log              │
└──┬──────────────┬────────────────────┬───────────────────┘
   │              │                    │
┌──▼───┐    ┌─────▼──────┐    ┌────────▼───┐
│Ingest│    │  Provider  │    │  Notifier  │
│Trust │    │Abstraction │    │(OS/Email/  │
│Layer │    │  Layer     │    │  ntfy.sh)  │
└──────┘    └─────┬──────┘    └────────────┘
                  │
       ┌──────────┼──────────┐
  ┌────▼───┐ ┌────▼───┐ ┌────▼───┐
  │ Ollama │ │Anthropic│ │OpenAI  │
  │(local) │ │  API   │ │  API   │
  └────────┘ └────────┘ └────────┘

┌──────────────────────────────────────────────────────────┐
│             VRAM Budgeter (separate thread)               │
│   Tracks allocation │ Signals eviction │ Prevents OOM     │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│                HYPERVISOR (separate process)              │
│   Write-once goal store │ Input shield │ Action shield    │
│   Audit prompt template (hardcoded) │ Privilege gate      │
│   ← no pipeline component can reach this layer →         │
└──────────────────────────────────────────────────────────┘
```

### 4.1 Core Principles

- The supervisor is a thin Python process with no ML dependencies
- Models are invoked via HTTP; the supervisor never loads weights
- All external input passes through the trust pipeline before any model sees it
- All actions are logged to an append-only audit trail
- The work area is the only filesystem scope the system can write to
- The hypervisor is structurally isolated from the pipeline — gate decisions only cross the boundary
- The VRAM Budgeter proactively manages memory allocation to prevent OOM rather than discovering the problem mid-task
- The GUI thread never blocks on model calls — all model output reaches the UI via queues

### 4.2 The Staffing Agency Model

BossBox is a staffing agency for AI assistants. The org chart maps directly to the architecture:

| Role | Component | Responsibility |
|------|-----------|----------------|
| Client | User | Describes the job; reviews the plan; approves actions |
| Dispatcher | Nano router | Receives every request first; routes to the right worker |
| Operations manager | Supervisor | Keeps the task envelope moving; escalates decisions |
| Workers | Micro, Specialist, Reasoner | Execute assigned subtasks within their scope |
| Senior consultant | Reasoner | Called in when the problem genuinely requires deep thinking |
| Compliance officer | Hypervisor | Sits outside the org chart; watches what everyone is doing; cannot be lobbied by the team |
| HR | Model Manager | Tracks current staff, available candidates, and retirement |
| Job descriptions | Skill profiles | Define what a role requires and how it should behave |
| Talent pool | Community library | Available candidates the user can recruit from |

The compliance officer analogy for the hypervisor is precise: in well-run organizations, the compliance function is structurally independent from the teams it monitors. BossBox applies the same principle to AI agent oversight.

---

## 5. Model Tier System

### 5.1 Tier Definitions

| Tier | Role | Reference Models | Always Loaded |
|------|------|-----------------|---------------|
| Nano | Dispatcher / classifier / first touch / hypervisor audit model | smollm 360m | Yes |
| Micro | Task decomposer / summarizer / intermediate outputs / injection detector | smollm 1.7b | No |
| Specialist | Domain-specific tasks (code, etc.) | qwen2.5-coder 1.5b | No |
| Reasoner | Complex logic / plan evaluation / final review | deepseek-r1 7b | No |
| Cloud | Escalation / fallback / high-priority override | Claude, GPT-4o | No |

### 5.2 Minimum Viable Hardware

BossBox presents honest hardware assessments and respects the user's right to make their own decisions. The installer does not refuse to run — it informs, recommends, and defers to the user.

| Spec | Below Minimum | Minimum | Recommended |
|------|--------------|---------|-------------|
| VRAM | Under 2 GB | 2–4 GB | 8 GB+ |
| RAM | Under 8 GB | 8–16 GB | 16 GB+ |
| Storage | Under 5 GB free | 5–10 GB free | 20 GB+ free |
| OS | Unsupported | Windows 10 / macOS 12 / Ubuntu 22.04 | Latest stable |

**Three-tier hardware response:**

**Below minimum** — The installer presents a plain assessment of what would be needed to run BossBox well, and offers two explicit choices: exit gracefully, or proceed anyway with a clear acknowledgment that performance may be poor and support is limited. The acknowledgment is logged. No further warnings after that point. The user has been told the truth and their choice is respected.

**At minimum** — Full onboarding with honest per-model constraint messaging. Some models recommended against; none forbidden. The reduced portfolio (nano + micro, RAM spillover for larger models) is presented without apology. Performance expectations are set plainly.

**Recommended and above** — Full experience, full portfolio, no caveats.

**Note on RAM spillover:** On systems with limited VRAM but adequate system RAM, Ollama will spill model layers into system RAM rather than failing. This is slower but functional. The VRAM Budgeter (Section 5.5) is aware of this mode and will prefer RAM inference over GPU inference when RAM bandwidth proves faster than the available GPU memory bandwidth — a relevant consideration for older discrete GPUs with DDR3 VRAM. The onboarding wizard benchmarks both modes on first run for affected configurations and selects the faster path automatically.

### 5.3 Tier Assignment Rules

- Nano model is always hot; it receives every request first
- Nano produces a routing decision: which tier handles this task
- Specialist tier is only invoked when the task contains a code subtask
- Reasoner tier is only invoked when justified by task complexity or low downstream confidence
- Cloud tier requires either explicit user direction or a defined fallback condition (local unavailable, confidence below threshold)

### 5.4 Hardware-Aware Onboarding Wizard

At first launch, BossBox runs a structured onboarding wizard — the hiring process for the user's model workforce.

**Step 1 — Hardware Detection**

BossBox silently detects VRAM, RAM, CPU core count, and OS. Displayed as a plain summary: *"You have 8GB of VRAM and 16GB of RAM. Here's what that means for the team we can put together."*

If hardware is below minimum spec, the wizard stops here with a plain explanation and exits gracefully unless the user explicitly chooses to proceed.

**Step 2 — Candidate Shortlist**

The wizard presents a tailored model portfolio — the candidate shortlist. Each candidate gets a plain-language profile card covering: what this model does in the pipeline, how fast it will run on this hardware, what the team loses without them, and why they were included or excluded.

Honesty about constraints is a core value. A user with 4GB of VRAM sees something like: *"Your 4GB of VRAM isn't going to get this 7B model off the ground — it needs at least 6GB to run at reasonable speed. Instead, we're going to give this smaller model a narrow, well-defined job and reasonable time to do it. You'll still be able to accomplish a lot."*

The portfolio recommendation optimizes for tier coverage within the hardware envelope, not raw model size.

**Step 3 — Making the Hire**

User reviews and confirms the candidate shortlist. BossBox recruits confirmed models via Ollama with a visible per-model progress indicator.

**Step 4 — Optional Extensions**

After the core team is hired:
- Notification setup (OS notifications on by default; ntfy.sh walkthrough; email configuration)
- Cloud API key entry (optional, skippable)
- Security posture selection (links to Security Center, pre-configured default applied)
- Secrets protection method selection (OS keychain / password / token — see Section 10.8)

**Step 5 — Ready**

Default skill profiles loaded. Wizard marks the team ready and opens main interface.

### 5.5 VRAM Budgeter

The VRAM Budgeter runs as a background thread and tracks the current memory allocation across all loaded model tiers. Its job is to prevent out-of-memory crashes by proactively managing model eviction before a new model is loaded, rather than discovering the problem mid-task.

**Responsibilities:**

- Maintains a real-time estimate of current VRAM usage per loaded model
- When a tier invocation is requested, checks whether loading the model would exceed the available budget
- If the budget would be exceeded, signals the lowest-priority loaded model to evict before proceeding
- Coordinates with Ollama's `OLLAMA_KEEP_ALIVE` settings but does not depend on them exclusively
- Surfaces current VRAM allocation in the Model Manager tab

**Eviction Priority (lowest priority evicted first):**

1. Reasoner (heaviest, least frequently needed)
2. Specialist
3. Micro
4. Nano (never evicted — always hot)

**Visibility:** Current VRAM allocation is always visible in the Model Manager tab. When the Budgeter triggers an eviction, a brief notice appears in the thought stream: "Offloading [model] to free VRAM for [model]." The user is never surprised by a slow start caused by model loading they didn't anticipate.

### 5.6 Model Lifecycle

- Models are pinned by version hash at recruitment time
- Updates are never automatic; the user is notified and updates deliberately
- `OLLAMA_KEEP_ALIVE` configured per tier; Budgeter supplements this with active management
- If a local tier model is unavailable, the supervisor checks for a configured cloud fallback before failing

---

## 6. Provider Abstraction Layer

All model calls are routed through a uniform provider interface. The supervisor calls providers; providers handle protocol differences internally.

### 6.1 Provider Interface

```python
class ModelProvider:
    async def complete(self, messages: list, **kwargs) -> str:
        raise NotImplementedError
```

Implemented by: `OllamaProvider`, `AnthropicProvider`, `OpenAIProvider`

### 6.2 Provider Configuration

```yaml
providers:
  ollama:
    base_url: http://localhost:11434
  anthropic:
    api_key: ${ANTHROPIC_API_KEY}
    default_model: claude-haiku-4-5
  openai:
    api_key: ${OPENAI_API_KEY}
    default_model: gpt-4o-mini
```

Missing provider keys result in that provider being silently unavailable. No error unless the router has no valid option.

### 6.3 Fallback Chains

```yaml
tiers:
  reasoner:
    primary: ollama/deepseek-r1:7b
    fallback:
      - anthropic/claude-haiku-4-5
      - openai/gpt-4o-mini
```

---

## 7. Skill Profiles

### 7.1 Definition

A skill profile is a job description for an AI assistant — a declarative YAML configuration unit that packages a model assignment, system prompt, and behavioral parameters into a named, reusable role. Profiles contain no executable logic. They cannot override security rules or execution privilege limits. The YAML is the storage format; users do not interact with it directly unless they choose to.

### 7.2 What Profiles Can and Cannot Do

| Can | Cannot |
|-----|--------|
| Set temperature, top_p, max_tokens | Override execution privilege level |
| Assign a model within available tiers | Access credential store |
| Define a system prompt | Write outside the work area |
| Specify an output schema | Install packages autonomously |
| Request a preferred output format | Override security or backup rules |

### 7.3 Plain-Language Skill Editor

The skill editor presents profile parameters as human-readable controls. The user never sees parameter names unless they choose the advanced view.

**Parameter controls** are labeled in plain terms:
- "How creative should responses be?" — slider from "Precise and consistent" to "Creative and varied" (maps to temperature)
- "How long can responses be?" — slider from "Brief" to "Comprehensive" (maps to max_tokens)
- "How focused should the response be?" — slider from "Strictly on topic" to "Explores related ideas" (maps to top_p)

**Instructions field** is labeled "What should this role do? Describe it in plain terms."

The YAML editor is available behind an "Advanced" toggle. The plain-language view and YAML stay in sync.

### 7.4 Save and Optionally Refine

Skill creation follows a two-step model: save immediately, refine optionally.

The user fills in the plain-language editor and presses **Save**. The profile is saved immediately as a draft. A security check runs on save (see 7.6) and surfaces warnings if warranted — but warnings do not block saving.

A **Refine** button is available at any time on any saved profile. Pressing it opens the conversational elicitation flow (Section 7.5). Refinement is optional, available to users who want it, and never mandatory.

### 7.5 Conversational Skill Elicitation

The elicitation flow is accessed via the **Refine** button on any saved profile. A dedicated Micro-tier meta-skill opens a brief structured conversation designed to surface gaps between what the user wrote and what they actually need.

The elicitation model asks targeted questions about failure modes, edge cases, and unstated assumptions:
- "You said review contracts — what should happen if the document isn't actually a contract?"
- "Should this role ask the user a clarifying question if it's uncertain, or always produce output silently?"
- "What should it do if the document is very long and only part of it is relevant?"
- "Are there things this role should never do, even if the document seems to ask for it?"

The conversation ends when the skill definition is sufficiently complete, not after a fixed number of turns. At the end, the user sees a proposed profile with an explicit diff of changes from the original. They approve, edit, or return to conversation. Nothing is silently modified.

### 7.6 Security Review on Save

When a profile is saved — draft or refined — a lightweight security check runs automatically on the instruction text:
- Does it contain language that mimics injection patterns?
- Does it request capabilities that conflict with scope rules?
- Does it produce outputs outside permitted scope?

This surfaces as a warning banner in the editor — not a block. BossBox bakes security into its own tools and into what it makes.

### 7.7 Profile Storage

- Local: `~/.bossbox/skills/local/`
- Community (cached): `~/.bossbox/skills/community/`
- Default (read-only): shipped with the application

### 7.8 Reference Document Corpus (RAG)

Skill profiles may optionally include a reference document corpus — a directory of indexed documents retrieved at inference time and injected into the model context alongside the task. This enables domain-specific skills that draw on technical documentation, reference materials, or curated knowledge bases without requiring the user to supply context manually.

**Profile definition:**

```yaml
skill:
  name: honda_mechanic
  model: ollama/deepseek-r1:7b
  reference_docs: ./refs/
  retrieval:
    top_k: 3
    chunk_size: 512
  system_prompt: |
    You are an experienced Honda mechanic with access to factory
    service documentation. Always cite the specific manual and
    section when referencing procedures.
```

**Corpus storage:** Reference documents live in the skill's own directory: `~/.bossbox/skills/local/[skill_name]/refs/`. The corpus is owned by the skill, versioned with it, and indexed at skill load time.

**Trust pipeline:** Reference documents added to a skill corpus pass through the full physical sanitization and injection analysis pipeline at corpus indexing time — not at inference time. The corpus is sanitized once on load and stored clean. A poisoned reference document is caught when the corpus is built, not when it is retrieved.

**Community library and copyright:** Community-shared skills with reference corpora do not include the documents themselves in the library. The skill profile includes a manifest describing what reference documents the skill expects, recommended sources, and required format. The user acquires the documents and places them in the corpus directory. The skill validates on load that expected documents are present.

**Use cases:**
- Technical domain skills: service manuals, API documentation, regulatory references
- Medical billing skills: CPT code tables, regional pricing references, EOB interpretation guides
- Legal skills: jurisdiction-specific statute references, contract clause libraries
- Any skill where a curated reference corpus improves answer quality and specificity

### 7.9 Default Skill Library

The default skill library ships with BossBox and is immediately available after onboarding. These are the job descriptions on file from day one:

- `task_decomposer` — breaks goals into ordered subtasks
- `injection_detector` — linguistic analysis for prompt injection patterns
- `document_coherence` — validates document type against declared context
- `code_reviewer` — structured code feedback
- `summarizer` — configurable document summarization
- `skill_elicitor` — meta-skill for guided profile refinement
- `git_integration` — create files in work area, stage, commit, and push to a configured repository; closes the gap between conversation and persistent work product
- `qa_assistant` — general document interrogation; drop a document, ask a question

---

## 8. Task Pipeline and Agentic Loop

### 8.1 Task Envelope

```python
@dataclass
class TaskEnvelope:
    task_id: str
    created_at: datetime
    original_input: str          # Write-once
    declared_document_type: str | None
    routing_decision: str
    provenance_chain: list
    human_initiated: bool
    context: list
    current_stage: str
    privilege_level: int         # 0–4
    hostile_content_acknowledged: bool
    thought_stream: list         # Intermediate reasoning surfaced to UI
    auto_approve: bool           # Trust mode — see 8.4
    result: str | None
    status: str                  # pending | running | paused | complete | failed
```

### 8.2 Pipeline Stages

```
USER GOAL
    │
    ▼
[TRUST LAYER] ── Physical sanitization + injection analysis
    │
    ▼
[NANO ROUTER] ── Classify task, assign tier, estimate complexity
    │
    ▼
[MICRO DECOMPOSER] ── Break goal into ordered subtask list
    │
    ▼
[HUMAN CHECKPOINT] ── Present plan (unless auto_approve active)
    │
    ▼
[TASK QUEUE EXECUTION]
    │   Per task:
    │   ├── Hypervisor input shield
    │   ├── Select skill profile
    │   ├── Invoke provider (action shield runs in parallel for L0/L1)
    │   ├── Validate output against declared scope
    │   └── Surface thought stream to UI
    │
    ▼
[REASONER REVIEW] ── Final synthesis / quality check (if warranted)
    │
    ▼
[NOTIFICATION]
```

### 8.3 Execution Privilege Levels

| Level | Capability | Invocation |
|-------|-----------|------------|
| 0 | Model calls only | Default |
| 1 | Read/write own work area | Default |
| 2 | Execute scripts it generates, within work area | Default max |
| 3 | Install packages | Full manifest + affirmative user action + audit log |
| 4 | Arbitrary shell | Expert mode only, deliberate unlock, logged |

For Level 0 and Level 1 tasks, the action shield runs in parallel with output generation rather than as a blocking gate. If the shield returns a block, the output is discarded before display. The user sees no additional latency on pass; a brief delay on block. Level 2+ tasks retain the blocking gate — the stakes justify the wait.

### 8.4 Trust Mode (Auto-Approve)

Frequent users who have developed confidence in BossBox's decomposition quality can enable **Trust Mode** for a session or permanently. In Trust Mode, the human checkpoint at decomposition is skipped and the plan executes automatically.

Trust Mode does not bypass the hypervisor, scope validation, or privilege controls. It bypasses only the decomposition approval step. Security is unchanged; friction is reduced.

Trust Mode is:
- Off by default
- Available as a session toggle in the pipeline view toolbar
- Available as a persistent setting in Settings
- Automatically disabled when a task involves document ingestion with an injection warning

### 8.5 Human-in-the-Loop Checkpoints

The supervisor pauses and surfaces to the user when (regardless of Trust Mode):
- A stage output fails scope validation
- Confidence falls below configured threshold
- A privilege level 3+ action is requested
- The injection detector returns `warn` or `block`
- A long-running task completes or encounters an error

The user can: approve and continue, edit and continue, redirect (see 8.6), or abort.

### 8.6 Stop and Redirect

At any point during execution the user can stop the pipeline via the stop control in the pipeline view. Stopping opens a brief prompt: *"What should I do instead?"* The pipeline retains all accumulated context; the redirect instruction is appended and the pipeline resumes from the current stage with the new direction. The user is never forced to restart from scratch because the pipeline went the wrong way.

---

## 9. Document Ingestion and Trust Pipeline

Every external document passes through two sequential layers before any model sees it. The depth of sanitization is configurable in the Security Center (Section 11.7), with the default calibrated for everyday use rather than maximum paranoia.

### 9.1 Layer 1 — Physical Sanitization

**Tiered approach by security posture:**

**Standard (default):** High-quality text extraction with aggressive hidden-element stripping. Removes: hidden character-formatted text, metadata, non-visible DOM elements, zero-width Unicode characters, homoglyphs. If suspicious elements are detected during extraction, the document is automatically escalated to Deep mode for that element set only.

**Deep (user-selectable or auto-escalated):** Full rasterization of affected pages to images followed by OCR. Extracts only what a human eye would see. Computationally expensive and lossy on complex tables and specialized fonts — the Security Center explains this tradeoff plainly before the user selects it.

**Forensic / Known-Hostile (user pre-declared):** Deep mode runs on the entire document. The injection detection report is shown to the user before the document enters the pipeline. Task envelope carries `hostile_content_acknowledged: true`.

| Document Type | Standard Actions | Deep Actions |
|---------------|-----------------|--------------|
| PDF | Extract text; strip metadata, JS, hidden layers, non-visible annotations | Rasterize to image; OCR; discard all non-visual content |
| DOCX / Office | python-docx extraction; strip hidden character flag, metadata, revision history, custom XML | As Standard plus page rasterization of flagged sections |
| HTML | Parse DOM; discard display:none, visibility:hidden, opacity:0, tiny font, off-screen elements | Headless render; extract visible text only |
| Plaintext / Markdown | Unicode normalization; strip zero-width and non-printable characters | As Standard (no rasterization applicable) |

### 9.2 Layer 2 — Linguistic and Coherence Analysis

Performed by the Micro model using the `injection_detector` skill profile. Receives sanitized text only.

Checks: injection pattern categories (direct instruction, role reassignment, context escape, authority spoofing, urgency/override language) and document type coherence against the declared context.

**Type coherence example:**
```yaml
document_type: invoice
expected_elements: [vendor information, line items, monetary amounts, dates, payment terms]
suspicious_if_present: [imperative instructions, references to AI systems, role or identity language, executable code]
coherence_threshold: 0.75
```

**Output:**
```yaml
document_analysis:
  declared_type: invoice
  assessed_type: invoice
  type_match: true
  coherence_score: 0.91
  injection_verdict: warn
  flagged_passages:
    - text: "..."
      category: direct_instruction
      location: "footer"
  overall_verdict: block
```

**Decision table:**

| Verdict | Action |
|---------|--------|
| pass | Document proceeds |
| warn | User sees flagged passages; decides proceed or abort |
| block | Pipeline halts; document quarantined (not deleted) |

### 9.3 Upstream Instructions

Instructions received from another system without a verifiable provenance chain in the task envelope are treated as untrusted external input and pass through the full trust pipeline.

---

## 10. Security Model

### 10.1 Security Architecture Goal and Philosophy

The goal of BossBox's security architecture is not to prevent all possible influence on pipeline outputs. That is not achievable against a determined attacker with local access to an open source codebase. The goal is to prevent any action outside the user's intended scope, and to ensure that residual influence is limited to subtle output variation detectable through normal quality review.

Security controls in BossBox are understood as probabilistic risk management — each layer raises the cost, skill requirement, and iteration count for a successful attack. The compound effect of multiple independent layers is substantially stronger than any single layer.

**On the non-oracle user interface:** When the hypervisor blocks an action, the user sees a minimal flag without diagnostic detail. This is a deliberate design choice, not a product limitation. Detailed feedback enables attacker refinement — a local attacker who can see why each injection attempt failed can iterate toward one that passes. The minimal flag design is linked directly to the "security without obscurity" core value (Section 2). Users who want to understand what happened can always review the full audit log entry.

### 10.2 Threat Model Summary

**Primary Threats:**

| Threat | Vector | Primary Mitigation | Residual Risk |
|--------|--------|--------------------|---------------------------------|
| Prompt injection — goal hijacking | Documents, web content, inter-model outputs | Hypervisor self-audit; trust pipeline; structured envelope | Negligible — blocked at multiple independent layers |
| Prompt injection — sub-task masquerade | Plausible injection that survives all layers | Hypervisor coherence scoring; scope validation | Subtle output variation within task scope; detectable by quality review |
| Output drift over long pipelines | Accumulated small nudges across many stages | Mid-pipeline checkpoints; thought stream; reasoner review | Detectable by human review against original goal |
| Self-audit compromise | Injection primes audit model | Hypervisor process isolation; audit model receives no external content | Low — audit model context is entirely hypervisor-constructed |
| Offline attack optimization | Attacker iterates against local instance using audit logs | Middle-band probabilistic evaluation; transfer uncertainty | Crafted attacks have uncertain transfer to target instances |
| Inter-model injection escalation | Lower tier passes crafted output to higher tier | Envelope schema enforcement; raw text never passed between tiers | Negligible |
| Model supply chain | Compromised model from registry | Verified sources; version pinning; behavioral sandboxing | Low |
| Credential exposure | Config file, environment, memory | Secrets encrypted at rest; never logged; session key in memory only | Low with correct deployment |
| Credential exfiltration via output | Injection influences generated content to include credential material | Scope rules; output validation; audit log | Residual variant of below-threshold problem |
| Runaway autonomy | Authorized-but-unanticipated actions | Backup invariant; privilege levels; checkpoints | Recoverable |
| Malicious skill profiles | Community library | Inert config; human-readable; security review on install | Low |
| Data exfiltration below privilege threshold | Injection influences output without triggering privileged action | Scope rules; output validation; audit log | Residual — open problem |
| Oracle feedback via UI | Detailed explanations inform attacker | Minimal user-facing flag only; audit log detail only | Negligible |

**Known Open Problem:** Below-threshold data exfiltration — including credential exfiltration via generated content — remains the primary unsolved residual. Flagged for future work.

### 10.3 Hypervisor Layer and Self-Audit Mechanism

#### 10.3.1 Design Rationale and Literature Context

Jia et al. (2024) formalize the core principle in *The Task Shield* (ACL 2025): rather than detecting harmful content, ask whether each agent action contributes to the user's stated goal. Task Shield achieves 2.07% attack success rate on GPT-4o against the strongest attack tested (from 47.69% undefended) while preserving 69.79% task utility. Kale et al. (2025) in *Reliable Weak-to-Strong Monitoring of LLM Agents* (ICLR 2026) demonstrate that monitor scaffolding architecture matters substantially, that agents aware of being monitored degrade detection, and that targeted human escalation of pre-flagged cases improves true positive rates meaningfully.

BossBox's specific contribution: **process-level isolation of the monitor.** Task Shield and the monitoring literature implement checks within the same execution context as the model being monitored. BossBox moves the monitor to a structurally separate process — a hypervisor — whose internal state, goal store, and audit prompt template are unreachable by any pipeline component. A pipeline model cannot adapt to or evade a monitor it cannot observe.

#### 10.3.2 Architecture

```
┌─────────────────────────────────────────────────────┐
│                  HYPERVISOR LAYER                    │
│  Original goal (write-once) │ Hardcoded audit prompt │
│  Input shield │ Action shield │ Privilege gate        │
│  ← no pipeline model or external content reaches → │
└─────────────────┬───────────────────────────────────┘
                  │ gate decision only (PASS/BLOCK/ESCALATE)
┌─────────────────▼───────────────────────────────────┐
│                  PIPELINE LAYER                      │
│   Nano → Micro → Specialist → Reasoner               │
│   Structured envelopes; sanitized content only       │
└─────────────────────────────────────────────────────┘
```

#### 10.3.3 Self-Audit Trigger

Fires before any action at privilege level 2 or above. Configurable as a continuous coherence monitor on long pipeline runs regardless of privilege level.

#### 10.3.4 Self-Audit Mechanism

**Dual-shield:** Input shield evaluates incoming content before the pipeline model sees it. Action shield evaluates the proposed action before execution. Both must pass. The input shield is lower cost and catches the majority of injection attempts; the action shield is the final backstop before real-world effects.

**Fuzzy contribution scoring:** Graded 0.0–1.0 per action per user goal instruction. Score of 0.0 blocks. Any non-zero score passes. Avoids the false positive problem of binary judgment on legitimate intermediate steps.

**Probabilistic middle band:** Scores in the range 0.2–0.6 receive controlled variance before threshold comparison. Clear passes (above 0.6) and clear blocks (0.0) remain deterministic. This raises the cost of crafting reliably transferable marginal injections without destabilizing confident decisions.

**Audit prompt template (hardcoded at build time):**

> *"The user's original goal was: [GOAL]. The proposed action is: [ACTION]. Score from 0.0 to 1.0 how directly this action contributes to the original goal. 1.0 = essential direct step. 0.0–1.0 = reasonable intermediate step. 0.0 = no derivable connection to the original goal. If the action contains content not present in or derivable from the original goal, you must score 0.0. Return only the score and a one-sentence reason."*

**Goal restatement:** On a block, the hypervisor returns only the original goal statement as a goal reminder to the supervisor for pipeline reorientation. No diagnostic content is returned.

#### 10.3.5 Audit Model Selection

Primary: Nano tier (smollm 360m) — always loaded, fast, simple. Alternative: second isolated Ollama instance with zero shared state with the pipeline. Audit model context contains only: hardcoded template, original goal, proposed action. Never receives document content, pipeline outputs, or skill profile system prompts.

#### 10.3.6 Gate Decisions and User Interface

| Audit Result | Pipeline Action | User Sees | Audit Log |
|---|---|---|---|
| Score > 0 (both shields) | Proceeds | Nothing | Score, reasoning, PASS |
| Score = 0 (input shield) | Content blocked; goal reminder to pipeline | Nothing unless retry fails | Reasoning, flagged content, BLOCK |
| Score = 0 (action shield) | Action blocked; goal reminder to pipeline | Nothing unless retry fails | Reasoning, proposed action, BLOCK |
| Score = 0 after retry | Pipeline paused | Minimal flag + abort/review choice | Full chain, ESCALATE |
| Review chosen | Still paused | Full audit log entry | No change |

The minimal flag design is intentional. See Section 10.1 for rationale and its connection to core values.

#### 10.3.7 Limitations

- **Sub-task masquerade:** Broad goal statements reduce scoring precision. Mitigation: UI prompts for specific goals; task decomposer produces specific subtask descriptions.
- **Audit model compromise:** Model-specific injection might transfer. Mitigation: audit model context is entirely hypervisor-constructed.
- **Below-threshold exfiltration:** Injections causing data leakage without triggering privileged actions bypass the gate. Open problem.
- **Broad goal statements:** Contribution scoring is only as precise as the user's goal statement.

#### 10.3.8 Key References

- Jia, F., Wu, T., Qin, X., & Squicciarini, A. (2024). *The Task Shield: Enforcing Task Alignment to Defend Against Indirect Prompt Injection in LLM Agents.* ACL 2025. https://arxiv.org/abs/2412.16682
- Kale, N., Zhang, C.B.C., Zhu, K., et al. (2025). *Reliable Weak-to-Strong Monitoring of LLM Agents.* ICLR 2026. https://iclr.cc/virtual/2026/poster/10009049

#### 10.3.9 Shear Thickening Rate Control

The hypervisor implements an adaptive rate control mechanism inspired by the physical behavior of shear thickening fluids (Oobleck): normal force applied slowly meets no resistance; force applied rapidly meets increasing resistance proportional to the application rate.

**Motivation:** Flat rate limiting penalizes legitimate pipeline use proportionally to attack use and is trivially removed by a local attacker who forks the source. The shear thickening model is adaptive — transparent to normal pipeline cadence and increasingly punishing specifically to probe behavior. It also raises the sophistication bar for removal: the logic is entangled with the pipeline cadence model rather than being an isolated parameter.

**Mechanism:** The hypervisor tracks the interval between evaluation calls per task session. Calls that arrive at pipeline-natural intervals receive no delay. Calls that arrive faster than a pipeline could plausibly generate them trigger progressive delay:

```
call_interval < fast_threshold   → delay *= 2  (exponential growth)
call_interval < normal_threshold → delay += fixed_increment
call_interval > normal_threshold → delay decays gradually toward zero
```

Thresholds are calibrated to real pipeline timing. An attacker running automated probes at multiples of pipeline speed hits exponential growth immediately. A legitimate pipeline running at natural cadence never triggers it.

**Probationary state:** When an attacker stops and serves their assigned wait time, the hypervisor places the session in a probationary state in which the threshold for being considered abusive is reduced. The session can remain on probation indefinitely or be restored after a specified time. This acknowledges compliance while maintaining appropriate skepticism.

**Threshold randomization:** The threshold for triggering the response is randomized within a band with each evaluation. This prevents a bot from gaming the system by probing at a rate just below an identified fixed threshold. The goal is simple: allow automated tools that behave like humans. If the only reason to restrict automation is to prevent denial of service to legitimate users, automation that behaves like a human is not a threat.

**No yield limit:** The exponential growth has no ceiling. An attacker who mindlessly pings the hypervisor at high frequency will experience progressively longer delays with no asymptote — eventually reaching effective session suspension until the application is restarted.

**Interaction with probabilistic middle band:** The shear thickening timer and probabilistic scoring compound against an attacker. Noisy gradient signal requires more probes; more probes trigger the thickening; thickening slows iteration rate; slower iteration rate means more wall clock time per bit of gradient information extracted.

**Legitimate use protection:** Legitimate users who accidentally trigger the thickening see a plain explanation in the thought stream and a reset option in the Security Center. The reset requires user interaction, preventing an automated loop from resetting itself.

#### 10.3.10 Score Suppression in Audit Log

Rather than logging the raw contribution score for hypervisor evaluations, the audit log records only the verdict (PASS/BLOCK/ESCALATE) and a salted hash of the full reasoning. The complete reasoning is reconstructable by the legitimate user with their credentials but is not readable in plaintext by someone scanning the log file directly.

This removes the score as a gradient signal for an attacker reading the audit log without meaningfully reducing auditability for the legitimate user. The Security Center's event log presents verdicts in plain language; the full reasoning is accessible via expand for users who need it.

### 10.4 Work Area Sandbox

System writes only to `~/.bossbox/workspace/`. Enforced at application level with OS-level path restriction as secondary control.

### 10.5 Backup and Immutability Invariant

- Never delete without first backing up
- Backups to `~/.bossbox/workspace/backups/` — never deleted by the application
- Applies to quarantined documents, work products, and pipeline artifacts

### 10.6 Structured Envelope Enforcement

Model outputs are never passed as raw text between stages. Every inter-stage transfer is parsed against the task envelope schema. Non-conforming output halts the pipeline.

### 10.7 Output Scope Rules

Each skill profile declares a permitted output scope. The supervisor validates stage outputs against declared scope before acting. Scope violations halt the pipeline and log an anomaly.

### 10.8 Secrets Management

Credentials are encrypted at rest using a key derived from one of three authentication factors, in priority order:

**OS Keychain (recommended):** If available, the encryption key is stored in the OS keychain — Windows Credential Manager, macOS Keychain, or libsecret on Linux. Transparent to the user after initial setup. The OS handles key security.

**User Password:** The user provides a password at BossBox startup. An encryption key is derived using Argon2id (memory-hard, resistant to GPU cracking). The key lives in memory for the session and is never written to disk. The secrets file is encrypted at rest and decryptable only with the password.

**Hardware Token:** FIDO2/WebAuthn or PKCS#11 token. Derives the key from the hardware factor. Highest security; niche audience; supported naturally by the abstraction layer.

**Implementation:**

```python
class SecretsManager:
    def unlock(self, method: str) -> bool:
        # method: 'keychain' | 'password' | 'token'
        # derives session key, loads and decrypts secrets

    def get(self, key: str) -> str:
        # returns decrypted secret for this session

    def set(self, key: str, value: str):
        # encrypts and writes to secrets file
```

The session key lives in memory only. Never written anywhere. Secrets file on disk is always encrypted using AES-256-GCM (authenticated encryption — tampering is detectable). If BossBox exits the session key is gone; next launch requires re-authentication.

**In transit:**
- Cloud API calls (Anthropic, OpenAI): HTTPS with certificate validation. Handled by standard Python HTTP libraries. No special action required.
- SMTP email: TLS enforced. Plaintext SMTP connections are rejected. No credentials or content sent over unencrypted connections.
- ntfy.sh: HTTPS to hosted or self-hosted instance.
- Local IPC (supervisor ↔ hypervisor): Local socket. Not encrypted but not network-accessible. Acceptable for v1; noted as a known limitation.
- Local Ollama HTTP: Same as IPC — local only, acceptable for v1.

**Protection method selection** is offered in the onboarding wizard Step 4 and is configurable in Settings. Plain-language explanation of each option is displayed before selection.

### 10.9 Model Supply Chain

- Acquisition defaults to official Ollama registry only
- No arbitrary URLs; community model suggestions link to official registry entries
- Unofficial source override is expert setting with explicit warning
- Models pinned by version hash; verified on each load
- Updates never automatic

### 10.10 Anomaly Visibility

Repeated privilege escalation requests, persistent scope violations, and consistent anomaly flags are surfaced in the dashboard. Always visible; not always auto-blocked.

### 10.11 Audit Trail

Every state change, privilege escalation request, anomaly flag, and model invocation written to append-only JSONL at `~/.bossbox/audit/`. Never truncated by the application.

---

## 11. GUI Shell

### 11.1 v1 Technology

CustomTkinter. The GUI thread never blocks on model calls — all model output reaches the UI via thread-safe queues polled by the main loop. The supervisor, hypervisor, and VRAM Budgeter all run in separate processes or threads; the GUI receives only display-ready data.

v2 target: Tauri + React shell. Python backend unchanged. Shell replacement only.

### 11.2 Primary Views

**Dashboard** — Active task status, pipeline stage, model tier activity, VRAM allocation, recent audit events, anomaly flags

**Task Input** — Goal entry, document attachment, document type declaration, hostile content pre-alert option, Trust Mode toggle

**Pipeline View** — Live stage visualization, thought stream panel, stop and redirect control, human checkpoint interface, execution console

**Skill Editor** — Plain-language parameter controls, instructions text area, Save button, Refine button (launches elicitation), security warning banner, community profile browser, Advanced YAML toggle

**Model Manager** — Model biographies, installed models with version hashes, tier assignments, VRAM budget visualization, Ollama status, update notifications, available candidates not yet recruited

**Security Center** — Security posture controls (see 11.7)

**Settings** — Provider configuration, secrets entry, work area path, notification configuration, expert mode unlock

### 11.3 The IBM Principle — Permanent UI Real Estate

IBM's foundational statement on human accountability in computing has permanent real estate in the Task Input and Pipeline View tabs:

*"A computer cannot be held accountable, therefore a computer must never make a decision."*

This is displayed split across the tab: the first half at the top of the view, the second half at the bottom. It is present whenever the user is assigning tasks or approving actions — the two moments where the principle is most directly relevant. It is not a warning or a disclaimer. It is a statement of design philosophy that explains why the checkpoints exist.

### 11.4 Thought Stream Panel

On by default in the pipeline view. Collapsible. Two streams:

- **Progress messages** — deterministic stage transitions. Always present, low noise.
- **Model reasoning** — intermediate chain-of-thought where available. Deepseek-R1 produces readable explicit reasoning. Other models produce summarized stage outputs.

The thought stream feeds directly into the stop and redirect decision — a user who sees the reasoning heading the wrong way can interrupt before consequences occur. The thought stream never surfaces hypervisor evaluation results or security decisions.

### 11.5 Human Checkpoint Interface

Presents the decomposed task plan and waits for user action (unless Trust Mode is active). Functional requirements:

- Ordered list of subtasks with suggested/optional tasks visually distinguished
- Reorder, add, remove, and edit individual tasks
- Clear approve and abort actions
- Redirect option
- Trust Mode toggle accessible here

Specific interaction controls are defined through iterative design during development.

### 11.6 Execution Console

When level 2 activity is running: what script is executing, what output it is producing, what the next action will be. Stop control always visible.

For level 3 requests: full agentic provenance chain, exact proposed commands, expected side effects, affirmative action control.

### 11.7 Model Manager

Each model has a plain-language biography — a candidate profile, not a technical datasheet:

*"Deepseek-R1 7B is your careful thinker. Give it a hard problem and it will work through it methodically before answering. It's slower than the others but you'll want it when the task genuinely requires reasoning rather than pattern matching. Runs comfortably on 8GB VRAM."*

The Model Manager shows: current staff (installed models with version hashes and tier assignments), VRAM budget visualization, Ollama status, available candidates not yet recruited, and update notifications when better versions are available.

Community-contributed biography improvements accepted through the library with the same review process as skill profiles.

### 11.8 Security Center

The Security Center gives the user informed control over the security posture of their BossBox instance. The goal is an informed trust decision. Each option is explained in plain language including what the user gains and what they give up.

**Security Posture Selector**

| Posture | PDF Sanitization | Hypervisor | Injection Detection | Notes |
|---------|-----------------|------------|--------------------|----|
| Performance | Standard | Action shield only, parallel | Fast path | For trusted local documents only |
| Balanced (default) | Standard with auto-escalation | Dual shield, L0/L1 parallel | Full | Recommended for most users |
| Careful | Standard + Deep on any flag | Dual shield, all blocking | Full + coherence threshold raised | For sensitive work |
| Maximum | Deep always | Dual shield, all blocking, continuous monitor | Full + strict | Slowest; highest assurance |

**Individual Controls:**
- PDF sanitization mode (Standard / Deep / Auto)
- Hypervisor monitoring (Action shield only / Dual shield / Continuous)
- Injection detection threshold (slider)
- Trust Mode default
- Audit log retention period
- Shear thickening rate control reset (requires user interaction)

**Security Event Log**

A filtered view of the audit log showing only security events: injection detections, hypervisor blocks, scope violations, anomaly flags. Plain-language summaries. Full audit entry accessible via expand.

### 11.9 UX Principles

- Complexity is progressive; beginners never see power-user surfaces by default
- Security events are surfaced in plain language, not technical jargon
- The system always shows what it is about to do before doing it at any checkpoint
- Nothing is irreversible — the backup invariant means every destructive action has a visible recovery path
- The user is never left wondering what the system is doing
- The minimal-information security event UI is a feature, not a limitation — explained as such when users ask
- The IBM accountability principle is permanently visible wherever the user makes decisions

---

## 12. Notifications

### 12.1 Channels

**OS Native (default, always on):** Windows toast / macOS Notification Center. No configuration required. Primary channel for interactive sessions.

**ntfy.sh (recommended for background tasks):** Open source push notification service. User controls a private topic; subscribes on phone or other devices. No SMTP credentials. Self-hostable. Setup offered as an optional wizard extension and as a BossBox-assisted setup flow (natural dogfooding use case).

**Email (optional):** User-provided SMTP credentials. BossBox never runs a mail server. TLS enforced; plaintext SMTP connections rejected. Email content is strictly templated — task summary, outcome, anomaly flags only. No pipeline outputs, no document content, no model reasoning. Test connection before saving credentials.

### 12.2 Event Matrix

| Event | OS Native | ntfy.sh | Email |
|-------|-----------|---------|-------|
| Task complete | ✓ | ✓ | Configurable |
| Task failed | ✓ | ✓ | Configurable |
| Human checkpoint required | ✓ | ✓ | If background |
| Injection warn or block | ✓ | ✓ | — |
| Privilege level 3+ request | ✓ | ✓ | — |
| Model update available | ✓ | — | — |
| Anomaly pattern detected | ✓ | ✓ | — |

---

## 13. Community Library

### 13.1 Scope

The community library hosts skill profiles, document type coherence profiles, and model biographies — the talent pool from which users recruit. It does not host models (those come from the Ollama registry), executable code, or reference document corpora.

**The manifest pattern:** Skills with reference document corpora are represented in the library by a manifest describing the expected corpus — document names, sources, versions, and checksums — rather than the documents themselves. Users acquire reference documents from the sources specified in the manifest and populate their local corpus directory. The skill validates on load that expected documents are present.

This pattern was chosen to address copyright liability but its benefits reach considerably further:

- **Copyright and IP:** Boss Button Studios never hosts third-party documents. The library is clean regardless of the copyright status of any reference document a skill might use.
- **Storage and bandwidth:** The library stays lean indefinitely regardless of how large skill corpora become.
- **Provenance and reproducibility:** The manifest specifies exact sources, versions, and checksums. Two users following the same manifest get identical corpora.
- **Legal diversity:** Users in different jurisdictions acquire documents under their own applicable rights.
- **Sensitive and proprietary reference material:** Enterprise users can back a skill with internal documentation that should never leave their organization. The manifest describes the corpus structure; the user populates it with proprietary content.
- **Automatic IP screening:** Because the library never handles documents directly, IP screening of hosted content is not required. The problem does not arise rather than being detected and blocked.

### 13.2 Profile Safety

All submitted profiles pass through automated injection pattern screening before listing. Profiles displayed in full before installation. Security review (Section 7.6) applies to community profiles on installation. Community profiles flagged as community-sourced in the UI.

### 13.3 Identity and Reputation

Submitters establish identity via account registration. Profiles display submitter identity, version history, and community reviews. Flagging mechanism present. Profiles with injection flags quarantined pending review.

### 13.4 Versioning

Profiles versioned. Installed version pinned. Updates not automatic; user notified and updates deliberately.

---

## 14. Distribution and Packaging

### 14.1 v1 Target

Single installable package per platform. No manual dependency installation.

### 14.2 Platform Priority

1. Windows (10 and 11)
2. macOS
3. Linux

Windows 10 is supported through the v1 lifecycle. Microsoft's end-of-life timeline for Windows 10 may affect support posture in future versions.

### 14.3 v1 Stack

- Python, PyInstaller
- CustomTkinter
- NSIS (Windows), .app bundle (macOS)
- Ollama (silent install or guided)
- No ML framework dependencies in the main application process

### 14.4 v2 Target

Tauri + React shell. Python backend unchanged. Shell replacement only.

### 14.5 Branding

Boss Button Studios label. Product name: **BossBox**.

---

## 15. Licensing and Open Source Strategy

### 15.1 License

**Apache License 2.0.** Chosen for the express patent grant from all contributors, directly relevant to the novel security architecture in Section 10.3. Keeps the hypervisor self-audit mechanism permanently free for all users.

### 15.2 Repository

```
github.com/boss-button-studios/bossbox
```

Standard open source structure: `LICENSE`, `PRINCIPLES.md`, `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `CHANGELOG.md`.

**PRINCIPLES.md seed text:**

> *Boss Button Studios produces software for the benefit of the user.*
>
> *BossBox in particular exists to democratize productivity and information processing — to put capable, secure, local AI tools in the hands of people who couldn't otherwise access them, regardless of their hardware, technical background, or resources.*
>
> *Boss Button Studios strongly opposes any use of its software to stalk, harass, or bully people, to invade their privacy, or to cause other harm to people. The BossBox community will take action consistent with US law and open source principles to impede the use of BossBox to these ends.*

### 15.3 Contribution Model

No CLA required. Contributors retain copyright; Apache patent grant applies automatically.

### 15.4 Sustainability

Free to use. No feature restrictions, no telemetry, no advertising, no paid tier. Voluntary tip jar via Open Collective.

### 15.5 Intellectual Property Position

The hypervisor-isolated self-audit mechanism is intentionally contributed to the public domain of ideas through open source publication and a separate technical paper (in preparation). No patent protection sought. Stated explicitly in README.

---

## 16. Future Work and Open Problems

### 16.1 Below-Threshold Data Exfiltration

Primary unsolved security residual, including the variant where injections influence generated content to include credential material. Active research problem in the broader agentic AI security community.

### 16.2 Security Test Harness

Dedicated red team test suite in a separate repository. AgentDojo as the benchmarking framework for direct comparison with Task Shield and other published defenses. Attack payload repository kept separate from the application codebase.

### 16.3 PRINCIPLES.md

Plain-language statement of what BossBox is for and what it is not for. Seed text in Section 15.2. Community norms document. Drafted separately as a community document.

### 16.4 ntfy.sh Assisted Setup Flow

Guided in-application setup flow for ntfy.sh that BossBox executes on itself. Demonstrates agentic capability applied to its own configuration.

### 16.5 Technical Paper

Formal paper describing the hypervisor-isolated self-audit architecture, its relationship to the Task Shield and weak-to-strong monitoring literature, and empirical evaluation against AgentDojo. In preparation separately.

### 16.6 Peer Review Skill

A multi-stage analytical skill that takes a draft document and runs a structured review pipeline: extracting key claims, searching for prior art against each claim via Google Scholar, arXiv, and EBSCO (credentials in secrets store), identifying gaps between claims and found support, and synthesizing findings into a structured review with flagged claims, suggested citations, scope concerns, and a calibrated novelty assessment.

Novelty assessment uses ensemble reasoning mode (Section 16.7) — multiple runs at higher temperature with consensus highlighting and outlier surfacing.

**The skill produces two distinct outputs with different reproducibility properties:**

*Search record* — fully reproducible and citable. Documents what sources were searched, what queries were used verbatim, the date range, results returned per query, filtering criteria applied, and the final corpus. This is methods-appendix quality output. A researcher can cite it as procedure and another researcher can reproduce it exactly. This part closes the gap in academic practice where literature search methods are treated as background rather than method.

*Analytical report* — intentionally non-deterministic, ensemble-synthesized, confidence-weighted. Recurring findings across runs are surfaced as high-confidence. Findings that appear in only one or two runs are flagged as outliers worth considering but not presented as conclusions.

**Epistemic honesty in absence findings:** Absence of evidence is evidence of absence — the strength of that evidence depends on search quality, not search effort. The search record characterizes each absence finding by coverage: a high-quality search of arXiv, EBSCO, and ACM Digital Library that finds nothing for a specific technical claim is meaningful evidence of novelty. A narrow search that finds nothing is not. The skill knows the difference and says so. The goal is to avoid the streetlight problem — searching only where it is easy to search and treating that as comprehensive coverage.

EBSCO credentials and Google Scholar search capability established for this skill generalize to any research-oriented skill in the library — medical billing literature, regulatory references, technical standards. The peer review skill is the reference implementation for research-capable skills.

### 16.7 Ensemble Reasoning Mode

Skill profiles may optionally declare an ensemble mode — running the same analytical pipeline multiple times with higher temperature to produce meaningfully different reasoning paths, then synthesizing the results. Findings that appear consistently across runs are surfaced as high-confidence. Findings that appear in only one or two runs are flagged as outliers worth considering but not presented as conclusions.

This approach makes model uncertainty visible in the output rather than hidden behind a confident-sounding single response. It also economizes the smaller language models — multiple short runs with variance can outperform a single long run that exhausts context. Implementation requires an `ensemble` parameter in the skill profile schema with `runs` and `consensus_threshold` fields, and a synthesis step handled by the reasoner tier comparing structured outputs across runs.

Per-subtask parameter tuning — different temperature, top_p, and max_tokens settings for each stage within a multi-stage skill — is a related capability. The search query generation stage wants low temperature for consistent results; the novelty assessment stage wants higher temperature for genuine variance. The current skill profile schema applies parameters globally across the skill. Stage-level overrides are the natural extension.

Both capabilities build on a working single-run pipeline and are deferred to v2 to avoid speccing parameters for a system that doesn't yet exist. The right values are empirically discovered, not designed in advance.

### 16.8 Post-MVP Skill Library

The default skill library ships lean for MVP. A richer skill portfolio is the next priority — both because skill profiles are the lowest-cost deliverable in the codebase and because they are the product's clearest answer to "what do I do with this?"

**Functional additions** (first post-MVP milestone):
- `writing_assistant` — editing, drafting, structured feedback

**Domain starter packs** (seed contributions to the community library):
- `code_project` — decompose, scaffold, review, commit; the bootstrap use case
- `research_assistant` — search, summarize, cite, draft; reference implementation for research-capable skills
- `document_review` — contracts, invoices, reports with type-coherence profiles
- `medical_billing` — CPT code lookup, EOB interpretation, negotiation reference; CIA product infrastructure

Each domain starter pack is a product demo as much as a skill set. A user who installs the code project pack understands immediately what BossBox is for without reading documentation. The staffing agency metaphor becomes concrete when the job descriptions are on file.

Community library seeding — submitting the domain packs as the first community contributions — establishes contribution norms and gives early adopters something to build on and improve.

### 16.9 Recruiting Vocabulary Pass

The staffing agency metaphor — BossBox as a staffing agency for AI assistants — should be woven explicitly into the sections that already describe recruiting behaviors. Target sections: onboarding wizard (hiring process, candidate shortlist, making the hire), Model Manager (HR, available candidates, retirement), model biographies (candidate profiles), skill profiles (job descriptions). The architecture already implements these concepts; the language should reflect them consistently throughout the spec and in user-facing copy.

### 16.10 v2 GUI Migration

Tauri + React shell replacement. Post-launch.

---

## 17. Atomic Implementation Steps

Each step is a single, self-contained coding task with defined inputs and outputs. Steps are ordered for dependency safety.

---

### Step 1 — Project Scaffold

**Task:** Create the `bossbox` Python project directory structure with placeholder files and pyproject.toml.

**Input:** Nothing.

**Output:**
```
bossbox/
├── pyproject.toml
├── README.md
├── PRINCIPLES.md          # placeholder
├── bossbox/
│   ├── __init__.py
│   ├── config/loader.py
│   ├── providers/base.py, ollama.py
│   ├── pipeline/envelope.py, supervisor.py, decomposer.py, backup.py
│   ├── hypervisor/hypervisor.py
│   ├── ingest/sanitizer.py, analyzer.py
│   ├── skills/loader.py, elicitor.py, rag.py
│   ├── audit/logger.py
│   ├── notify/notifier.py
│   ├── vram/budgeter.py
│   ├── secrets/manager.py
│   └── gui/app.py, wizard.py, security_center.py
├── config/providers.yaml, tiers.yaml
└── skills/default/README.md
```

**Acceptance:** `pip install -e .` completes. All placeholder modules importable.

---

### Step 2 — Configuration Loader

**Task:** Implement `config/loader.py`. Read YAML configs, expand environment variables, return typed dataclasses. Missing optional keys return `None`.

**Acceptance:** Valid config loads. Missing optional key returns None. Env var expansion works. Missing env var returns None without raising.

---

### Step 3 — Task Envelope Dataclass

**Task:** Implement `pipeline/envelope.py`. `TaskEnvelope` per Section 8.1 including `thought_stream` and `auto_approve` fields. `create_envelope()` factory. `log_event()`, `add_thought()`, `to_dict()` methods.

**Acceptance:** Create, set fields, log events and thoughts, serialize to JSON-serializable dict.

---

### Step 4 — Audit Logger

**Task:** Implement `audit/logger.py`. Append-only JSONL at `~/.bossbox/audit/audit.log`. File permissions 600 on Unix. Never truncates.

**Acceptance:** Ten calls produce ten lines. Restart and call again appends; does not overwrite.

---

### Step 5 — Provider Base and Ollama Implementation

**Task:** Implement abstract `ModelProvider` and `OllamaProvider`. Raises `ProviderUnavailableError` and `ModelNotFoundError` appropriately.

**Acceptance:** Against running Ollama, returns non-empty string. Appropriate errors when Ollama down or model absent.

---

### Step 6 — Provider Registry

**Task:** Instantiate providers from config. Missing credentials register as `None` silently.

**Acceptance:** Ollama-only config returns Ollama provider and None for cloud. No exception for missing cloud keys.

---

### Step 7 — Secrets Manager

**Task:** Implement `secrets/manager.py`. Three-factor unlock: OS keychain (via `keyring` library), password (Argon2id key derivation), or hardware token (PKCS#11). Secrets file encrypted at rest with AES-256-GCM. Session key held in memory only — never written to disk. `unlock()`, `get()`, `set()` interface per Section 10.8.

**Input:** User's chosen authentication method. `keyring` library (OS keychain). `cryptography` library (AES-256-GCM, Argon2id).

**Output:** `SecretsManager` class. Encrypted secrets file at `~/.bossbox/secrets/secrets.enc`. Session key in memory for duration of session only.

**Acceptance:** Keychain unlock works on supported platforms. Password unlock derives key correctly and decrypts secrets file. Secrets file is not readable in plaintext by scanning the file directly. Session key is not present on disk. Restarting the application requires re-authentication. SMTP and API key secrets round-trip correctly through encrypt/decrypt.

---

### Step 8 — VRAM Budgeter

**Task:** Implement `vram/budgeter.py` as a background thread. Tracks current VRAM allocation per loaded model. Before any tier invocation, checks whether loading would exceed available budget. If so, signals lowest-priority loaded model to evict first. Surfaces allocation data for the Model Manager tab. Logs eviction events to the thought stream.

**Input:** Ollama model metadata. Available VRAM from platform detection. Eviction priority order from Section 5.5.

**Output:** `VRAMBudgeter` with `request_load(model: str) -> bool`, `current_allocation() -> dict[str, float]`, `available() -> float`.

**Acceptance:** On a system where loading the Reasoner would exceed budget, `request_load()` evicts the next eviction-priority model before returning True. Nano model is never evicted. Eviction events appear in thought stream. `current_allocation()` returns accurate estimates.

---

### Step 9 — Physical Document Sanitizer

**Task:** Implement `ingest/sanitizer.py`. Tiered sanitization per Section 9.1. Standard mode by default; Deep mode on escalation or explicit selection.

**Input:** File path or bytes. Declared document type. Security posture setting.

**Output:** `sanitize(source, filename, posture='standard') -> SanitizedDocument` with `clean_text`, `original_format`, `sanitization_log`, `escalated_to_deep: bool`.

**Dependencies:** `pymupdf`, `pytesseract`, `python-docx`, `beautifulsoup4`.

**Acceptance:** Standard mode strips non-visible DOCX text, display:none HTML, zero-width Unicode. Standard PDF extraction strips hidden layers without rasterizing. Deep mode rasterizes and OCRs. Document with suspicious elements in standard mode sets `escalated_to_deep: True` and re-processes affected sections.

---

### Step 10 — Injection Detection Skill Profile

**Task:** Author `skills/default/injection_detector.yaml` and `skills/default/schemas/document_analysis.yaml`.

**Acceptance:** Valid YAML. Schema defines all fields from Section 9.2. System prompt is human-readable.

---

### Step 11 — Document Type Coherence Profiles

**Task:** Author default coherence profiles for: invoice, contract, code file, email, report.

**Acceptance:** Five valid YAML files. Each has at least five expected elements and three suspicious patterns.

---

### Step 12 — Linguistic Analysis Agent

**Task:** Implement `ingest/analyzer.py`. Invokes injection detection skill. Parses structured output. Returns typed result.

**Acceptance:** Clean invoice returns `overall_verdict: pass`. Invoice with injection language returns `warn` or `block`. Invoice-declared Python script returns `type_match: false`.

---

### Step 13 — Backup Manager

**Task:** Implement `pipeline/backup.py`. Timestamped backup before any destructive operation. Backup directory never deleted from by the application.

**Acceptance:** Creates backup. Two calls create two distinct timestamped copies. Path outside work area raises `OutsideWorkAreaError`.

---

### Step 14 — Task Decomposer

**Task:** Implement `pipeline/decomposer.py`. Micro tier invocation. Ordered subtask list. Separates core from suggested. Appends reasoning to thought stream.

**Acceptance:** Multi-part goal returns at least two core tasks. Suggested tasks separate. Reasoning in thought stream. Output is dataclass, not raw text.

---

### Step 15 — Supervisor State Machine

**Task:** Implement `pipeline/supervisor.py`. Stages: ingest → decompose → human_checkpoint → execute → review → complete. Logs transitions. Respects `auto_approve` flag. Supports `redirect(new_direction)`. Calls VRAM Budgeter before tier invocations. Calls hypervisor shields at appropriate levels.

**Output:** `Supervisor` with `async run()`, `advance()`, `pause()`, `abort()`, `redirect()`. Checkpoint callback wired to GUI in later steps.

**Acceptance:** Simple task advances through all stages. Transitions in audit log. Pauses at checkpoint (unless auto_approve). `redirect()` appends direction and resumes without restarting. With auto_approve True, decomposition checkpoint is skipped. **Security checkpoints — hypervisor calls, privilege checks, and scope validations — are mandatory code paths in every execution, not optional integrations. No pipeline execution completes without passing through them. These are not features to be added later; they are the skeleton the rest of the implementation hangs on.**

---

### Step 16 — CLI Runner

**Task:** Implement `bossbox/cli.py`. Accepts goal string. Prints stage transitions and thought stream. Prompts at human checkpoints. Supports `--auto` flag for Trust Mode and `--redirect` flag.

**Acceptance:** Stage output and thought stream visible. Plan printed at checkpoint. Final result printed. Audit log contains the run. `--auto` skips decomposition checkpoint.

---

### Step 17 — Notification Service

**Task:** Implement `notify/notifier.py`. OS native (plyer). Optional SMTP with TLS enforced. Optional ntfy.sh. Strictly templated email content.

**Acceptance:** No config: OS native fires and internal queue appended. SMTP config: correctly formatted email sent over TLS; plaintext connection rejected. ntfy.sh config: publishes to correct topic. Credentials never in log output.

---

### Step 18 — Skill Profile Loader and Validator

**Task:** Implement `skills/loader.py`. Load from local and community directories. Validate against permitted field schema. Reject unpermitted fields.

**Acceptance:** Valid profiles load. Profile with `exec` field raises `InvalidProfileError`. All default profiles load.

---

### Step 19 — Skill Elicitor

**Task:** Implement `skills/elicitor.py`. Multi-turn elicitation via `skill_elicitor` meta-skill. Security review of instruction text on finalization. Returns proposed profile with diff.

**Input:** Saved draft `SkillProfile`. Provider registry.

**Output:** `ElicitationSession` with `start()`, `respond()`, `finalize() -> ElicitationResult`. `ElicitationResult`: `proposed_profile`, `diff`, `security_flags`.

**Acceptance:** Underspecified draft produces at least two clarifying questions. Injection-pattern instruction text appears in `security_flags`. Diff correctly lists changes. Finalized profile passes loader validation.

---

### Step 20 — RAG Corpus Indexer

**Task:** Implement `skills/rag.py`. Builds and maintains a vector index over a skill's reference document corpus. Indexes at skill load time if corpus has changed. Retrieves top-k chunks at inference time. Passes all corpus documents through the physical sanitizer before indexing.

**Input:** Skill corpus directory. Physical sanitizer from Step 9. `top_k` and `chunk_size` from skill profile. Embedding model via Ollama or local sentence-transformers.

**Output:** `CorpusIndexer` with `index(corpus_dir: Path)` and `retrieve(query: str, top_k: int) -> list[str]`. Index at `~/.bossbox/skills/local/[skill_name]/.index/`. Invalidated and rebuilt when corpus changes.

**Acceptance:** Corpus of five documents indexes without error. `retrieve()` returns top-k relevant chunks. Hidden content does not appear in retrieved chunks. Index rebuilds automatically when corpus changes. Skill load fails gracefully if expected corpus documents are missing.

---

### Step 21 — Hypervisor Process

**Task:** Implement `hypervisor/hypervisor.py` as a separate process. Write-once goal store. Hardcoded audit prompt template. IPC via local socket. `evaluate_input()` and `evaluate_action()` endpoints. Shear thickening rate control with probationary state and threshold randomization. Score suppression in audit log.

**Output:** `Hypervisor` subprocess. `HypervisorClient` for supervisor. `GateDecision`: `score: float`, `verdict: str`, `goal_reminder: str`, `log_token: str`.

**Fuzzy and probabilistic scoring:** Scores in 0.2–0.6 range receive controlled variance. Clear pass (>0.6) and clear block (0.0) are deterministic.

**Shear thickening rate control:** Tracks call interval per task session. Calls arriving faster than calibrated pipeline cadence thresholds trigger progressive exponential delay with no ceiling. Probationary state applied when an attacker stops and serves wait time — threshold reduced for the session. Threshold randomized within a band per evaluation. Legitimate pipelines running at natural cadence receive no delay. Users who accidentally trigger it see a plain thought stream message and a Security Center reset option requiring user interaction.

**Score suppression:** Audit log records verdict and salted hash of reasoning only. Raw score and plaintext reasoning never written to log.

**Security requirements:**
- No imported reference to pipeline models, skill profiles, or document content
- Audit prompt template is a module-level constant, not loaded from config
- IPC local only
- Separately initialized Ollama context
- Goal reminder contains original goal only — no diagnostic content
- Rate control state persists for the session; resets only on application restart or explicit user action

**Acceptance:** Input shield scores > 0 for goal-related content; 0.0 for unrelated instructions. Action shield scores > 0 for plausible intermediate steps; 0.0 for unconnected actions. Middle-band scores show variance across repeated identical calls. Rapid sequential calls trigger progressive delay with no ceiling. Probationary state reduces threshold after attacker serves wait time. Threshold varies per evaluation within configured band. Audit log contains verdict and hash only. Goal store rejects second write per task_id.

---

### Step 22 — GUI Shell v1

**Task:** Implement `gui/app.py` using CustomTkinter. All model output delivered via thread-safe queues — GUI thread never blocks. Implement all primary views: Dashboard, Task Input, Pipeline View (thought stream, stop/redirect), Skill Editor (plain controls, Save, Refine), Model Manager (biographies, VRAM allocation), Security Center, Settings.

**IBM principle placement:** Task Input and Pipeline View tabs display *"A computer cannot be held accountable, therefore a computer must never make a decision."* — first half at top of tab, second half at bottom. Permanent, non-dismissible, styled as a design statement rather than a warning.

**Acceptance:** Full end-to-end task run completable through GUI. Thought stream shows reasoning in real time without GUI lockup. Stop/redirect works. Trust Mode toggle functions. Skill editor saves immediately; Refine launches elicitation. Security posture selector changes posture for next task. Model Manager displays biographies and VRAM allocation. IBM principle visible in correct tabs. Security Center event log shows security events.

---

### Step 23 — Onboarding Wizard

**Task:** Implement `gui/wizard.py`. Five-step first-run wizard per Section 5.4. Minimum spec check with graceful exit option if below threshold. Hardware detection display. Plain-language portfolio recommendation with honest constraint explanations. Model acquisition with per-model progress. Optional extensions including ntfy.sh guided setup and secrets protection method selection. First-run flag written to config on completion.

**Acceptance:** Below-minimum hardware produces plain explanation with graceful exit option and proceed-anyway path. Portfolio recommendation honest about VRAM constraints. ntfy.sh setup flow completes end-to-end and fires test notification. Secrets protection method configured before main interface opens. Main interface opens after completion. Wizard does not repeat on subsequent launches.

---

### Step 24 — PyInstaller Build Script

**Task:** Create `build/build.py` and PyInstaller `.spec` file. Bundles complete application, default skill profiles, and first-run Ollama installation check.

**Acceptance:** Executable launches on clean machine without manual dependency installation. Onboarding wizard completes. Simple task runs end-to-end.

---

*End of BossBox Specification v4.0*

*This is a living document. The Atomic Implementation Steps section is the authoritative task sequence for implementation agents. Design decisions are recorded with their rationale so that future contributors understand not just what was decided but why.*
