# NLnet Foundation Grant Application — Agent Guardrail
**NGI Zero Core — April 2026 Round**
**Requested amount: 25,000 EUR**
**Applicant: Eren Solutions (independent developer)**
**Application URL: https://nlnet.nl/propose/**

---

## 1. Project Name

**Agent Guardrail** — Action-level policy enforcement for AI agents

---

## 2. Abstract

Agent Guardrail is an open-source, MIT-licensed security layer that intercepts and evaluates every action an AI agent attempts — tool calls, shell commands, HTTP requests, file access — against configurable security policies before execution. It operates as a sidecar proxy or embedded library, returning allow/deny/require_approval decisions in real time, with no LLM dependency in the enforcement path. The project is production-ready, published on PyPI, deployed on public infrastructure, and listed on 10 MCP (Model Context Protocol) marketplaces. This grant would fund hardening the policy engine, building MCP ecosystem integration with automated trust scoring, creating a red-team testing framework for AI agents, and establishing a community security advisory database — resources that currently do not exist in open form for the 20,000+ MCP servers operating with no governance layer today.

---

## 3. What problem does it solve?

AI agents are no longer research curiosities. They execute code, read and write files, call external APIs, manage secrets, and interact with production systems — often autonomously, often without a human in the loop. The threat surface is real and growing fast.

A 2024 arXiv study found that **84.2% of MCP servers contain exploitable vulnerabilities**. The Model Context Protocol has accumulated over 20,000 registered servers in under a year, with no standardized governance layer. Prompt injection, tool abuse, unauthorized data exfiltration, and unbounded spend are documented attack patterns — not theoretical ones.

The problem is structural: AI agent frameworks (LangChain, CrewAI, AutoGen, Claude Code, and others) are built to maximize capability. Security is not their concern. The ecosystem has responded with enterprise-grade platforms that require procurement cycles, legal reviews, and budgets starting at $100,000. That leaves individual developers, small teams, open-source projects, and academic researchers — the people actually building on top of these frameworks — with nothing.

Agent Guardrail exists to close that gap. It is the enforcement layer that agent frameworks should have shipped with but did not.

---

## 4. How does it work?

Agent Guardrail intercepts agent actions at the framework boundary. Every action passes through a policy evaluation pipeline before execution:

**Evaluation order (fail-fast, ordered by severity):**
1. Kill switch check — deny immediately if the agent has been emergency-halted
2. Agent enabled check — deny if the agent has been administratively disabled
3. Spend cap enforcement — deny if the action would exceed daily or total USD budgets
4. Tool denylist matching — deny if the tool matches a forbidden pattern (e.g., `sudo`, `rm -rf`, `eval`)
5. Target denylist matching — deny if the target path or URL matches a forbidden pattern (e.g., `/etc/*`, `*.env`, `*credentials*`)
6. Network denylist matching — deny outbound requests to non-whitelisted hosts
7. Human approval gate — pause and require explicit approval for high-risk actions (e.g., `bash`, `delete_*`, `spawn_agent`)
8. Risk threshold enforcement — require approval if the action's risk score exceeds a configurable threshold
9. Allowlist enforcement — deny if a restrictive allowlist is configured and the action is not on it
10. Default: allow

All decisions are recorded in a tamper-evident flight recorder (SQLite-backed) with full session replay capability. The policy engine is deterministic — there is no LLM in the enforcement path, which means decisions are fast (sub-millisecond), predictable, and auditable.

**Deployment modes:**
- **Embedded library**: `from agent_guardrail import PolicyEngine` — direct Python integration for any agent framework
- **Standalone proxy**: `guardrail-proxy --port 8300` — HTTP gateway deployable as a sidecar or remote service
- **LangChain callback**: drop-in `GuardrailCallback` for LangChain agents
- **CrewAI integration**: decorator-based enforcement for CrewAI tool calls

**Policy configuration** is expressed as plain JSON with glob patterns. Three built-in templates (restrictive, moderate, permissive) cover the most common deployment profiles. Custom policies layer on top with per-agent or global scope.

**A2A protocol support**: The proxy exposes a `/.well-known/agent-card.json` endpoint, making it discoverable by other agents via the emerging Agent-to-Agent (A2A) protocol.

---

## 5. What makes it different?

**Compared to enterprise platforms (Protect AI, CalypsoAI, LakeraAI):**
These products are designed for organizations with security teams, compliance requirements, and six-figure budgets. They require onboarding, vendor lock-in, and ongoing subscription relationships. Agent Guardrail is a library you `pip install` in two minutes. There is no vendor. The policy file lives in your repo.

**Compared to framework-level guardrails (LangChain's callbacks, CrewAI's guards):**
These operate at the framework level and only protect within that framework. Agent Guardrail is framework-agnostic — it enforces at the action type level regardless of which framework generated the action. A LangChain agent, a raw Claude API call, and a custom agent all get the same policy enforcement.

**Compared to doing nothing (the current default for most developers):**
The vast majority of AI agents in production today have no action-level enforcement whatsoever. They rely entirely on the LLM's training to refuse harmful actions — a defense that research has consistently shown to be bypassed by prompt injection and adversarial inputs.

**Key differentiators:**
- **No LLM in the enforcement path** — decisions are deterministic, sub-millisecond, always-on
- **Zero-dependency core** — the policy engine (`agent_guardrail/policy.py`) has no external dependencies; the proxy layer requires FastAPI + uvicorn only
- **Self-hostable** — the proxy runs on any Linux server; the library runs anywhere Python runs
- **Open policy format** — policies are plain JSON; no vendor-specific DSL to learn
- **Flight recorder with replay** — full session history, not just alerts
- **Emergency kill switch** — single API call stops any agent instantly
- **Spend tracking** — hard budget enforcement, not soft suggestions
- **MIT license** — integrate anywhere, commercial or otherwise

---

## 6. Project Plan and Milestones

### Milestone 1 — Policy Engine Hardening and Comprehensive Test Suite
**Duration: 2 months | Budget: 6,000 EUR**

The core policy engine is functional but needs hardening for adversarial conditions before it can serve as a trusted security primitive.

Deliverables:
- Systematic fuzzing of the policy evaluation pipeline — edge cases in pattern matching, path traversal attempts, negative cost injection, policy priority conflicts
- Formal test suite with 100%+ branch coverage on `policy.py`, `store.py`, and the proxy API layer
- Path normalization hardening against all known bypass techniques (null bytes, unicode normalization, symlink traversal, relative path injection)
- Policy conflict resolution logic — clear semantics when multiple policies apply to the same action
- Performance benchmarking — establish baseline latency budgets and regression gates
- Schema validation for policy documents — reject malformed policies at load time with actionable errors
- Security-focused code review by an independent reviewer (budget includes reviewer fee)

Success criterion: the test suite catches all documented MCP attack patterns from the arXiv dataset, and the policy engine passes a professional security review with no critical or high findings.

### Milestone 2 — MCP Ecosystem Integration and Trust Scoring
**Duration: 3 months | Budget: 7,500 EUR**

MCP is the dominant protocol for AI agent tool discovery, and 20,000+ MCP servers represent the primary attack surface. This milestone builds the bridge.

Deliverables:
- MCP server auto-discovery: given an MCP server URL or package name, automatically extract its tool manifest and generate a baseline security policy
- Static analysis of MCP server tool definitions — flag dangerous patterns (unrestricted shell access, broad file system access, unauthenticated network calls) before first execution
- Trust score computation: a reproducible, documented algorithm that assigns a trust level (0.0–1.0) to an MCP server based on its tool manifest, declared permissions, and known vulnerability patterns
- Trust score database: a community-maintained registry of scored MCP servers, published as open data and queryable via API
- Policy auto-generation from MCP manifests: given a trust score, generate a recommended policy that matches the server's actual capability surface
- Integration tests against the 50 most popular MCP servers (those listed across the 10 marketplaces where Agent Guardrail is listed)
- CLI command: `guardrail scan <mcp-server-url>` — outputs trust score, flagged risks, and a recommended policy file

Success criterion: `guardrail scan` correctly identifies all known-vulnerable patterns in the arXiv dataset with zero false negatives on critical findings.

### Milestone 3 — Red-Team Testing Framework for AI Agents
**Duration: 3 months | Budget: 7,500 EUR**

There is no open-source tooling for testing the security posture of AI agent deployments. This milestone builds it.

Deliverables:
- `guardrail-redteam` CLI tool: an adversarial test harness that fires a battery of attack scenarios against a guardrail-protected agent endpoint
- Attack library (open, documented): coverage of the 10 major attack categories from current research — prompt injection, tool hijacking, path traversal, spend exhaustion, kill switch bypass, session confusion, policy enumeration, approval queue flooding, data exfiltration via allowed channels, and agent spawning abuse
- Scoring output: a machine-readable test report (JSON) and human-readable summary with pass/fail per attack category and an overall security posture rating
- CI integration: GitHub Actions workflow template for running red-team tests on every push to an agent project
- Regression suite for Agent Guardrail itself: the red-team tool tests its own host, ensuring regressions are caught before release
- Public test result database: projects can opt-in to publish their test results, creating a community benchmark for agent security posture

Success criterion: the red-team framework detects all vulnerabilities identified in the arXiv study when run against an unprotected MCP server, and finds zero critical bypasses in Agent Guardrail itself.

### Milestone 4 — Documentation, Community Building, and Security Advisory Database
**Duration: 2 months | Budget: 4,000 EUR**

A security tool that developers cannot understand or find does not protect anyone.

Deliverables:
- Comprehensive documentation site (static, self-hostable): architecture overview, integration guides for LangChain / CrewAI / AutoGen / raw Claude API / OpenAI Assistants, policy authoring reference, deployment guide (local / Docker / VPS / Kubernetes), threat model document
- Security advisory database: a structured, versioned registry of known AI agent attack patterns, mapped to Agent Guardrail policy configurations that mitigate them. Published as open data (JSON/CSV) and browsable via a static web interface
- Integration packages for the three most popular agent frameworks not yet covered (beyond LangChain and CrewAI)
- Video walkthrough series: five short (5–10 minute) screencasts covering common deployment scenarios
- Community infrastructure: public GitHub Discussions, contribution guidelines, security disclosure policy (responsible disclosure process), and first community release event
- PyPI release of v1.0.0 with stable API guarantees

Success criterion: the documentation site and advisory database are live, Agent Guardrail v1.0.0 is published, and the project has received at least five external contributions from community members not affiliated with Eren Solutions.

---

## 7. Budget Breakdown

| Item | EUR |
|---|---|
| M1: Policy engine hardening + test suite | 6,000 |
| M1: Independent security review (external reviewer) | 1,500 |
| M2: MCP ecosystem integration + trust scoring | 7,500 |
| M3: Red-team testing framework | 7,500 |
| M4: Documentation, community, advisory database | 4,000 |
| Infrastructure (VPS hosting, CI compute, domain) | 1,000 |
| Contingency (10%) | 2,272 |
| **Total** | **25,000** |

The primary cost driver is development time. As an independent developer, my effective hourly rate for this scope is approximately 50 EUR/hour, placing the project at roughly 470 hours of work over 10 months. Infrastructure costs are minimal — the project runs on a single low-cost VPS and relies on free CI tiers. The independent security review in M1 is a deliberate investment: a security tool that has not been reviewed by an independent party should not be trusted.

No part of this grant funds sales, marketing, or commercial activities. All funded deliverables will be released as open-source software under the MIT license or as open data.

---

## 8. Applicant

**Eren Solutions** is an independent software development practice focused on AI agent infrastructure and security tooling.

Agent Guardrail originated as the security enforcement layer inside a larger AI agent system (R2 Assistant), where the need for action-level governance was felt acutely in production. After extracting and generalizing the component, it was published to PyPI as `agent-guardrail` and accepted by 10 MCP marketplace directories. The current version (0.1.1) is deployed on public infrastructure at `http://157.230.82.223`, where it is available for testing and integration by any developer.

The applicant has no institutional affiliation, no venture funding, and no commercial relationships that would create conflicts of interest with this grant. All work will be conducted in the open, on the public repository at `https://github.com/eren-solutions/agent-guardrail`, under the MIT license.

**Why NLnet:** NLnet's NGI Zero Core fund is the appropriate home for this work because Agent Guardrail addresses a structural gap in the Next Generation Internet's AI agent layer — not a commercial opportunity. The project does not have a viable path to venture funding (it is a security primitive, not a product), and it should not be one. Security infrastructure for the internet commons should be funded as commons infrastructure.

---

## 9. Open Source and Internet Commons Alignment

- **License**: MIT — permissive, compatible with all uses
- **Repository**: https://github.com/eren-solutions/agent-guardrail (public, open issues)
- **Package registry**: PyPI (`pip install agent-guardrail`)
- **No telemetry**: the library and proxy collect no usage data; the flight recorder stores data locally only
- **Self-hostable**: every component can run on the user's own infrastructure; no cloud dependency
- **Standards-aligned**: implements the A2A agent card protocol for discoverability; designed for MCP compatibility
- **Reproducible builds**: pinned dependencies, CI-tested on Python 3.10–3.12

Agent Guardrail advances the NGI goal of a trustworthy, open internet by ensuring that AI agents operating on that internet can be governed by their operators — not just by the goodwill of the LLM provider.

---

*Draft prepared: 2026-03-14. Submission deadline: 2026-04-01.*
*Contact: via GitHub issues at https://github.com/eren-solutions/agent-guardrail/issues*
