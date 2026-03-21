# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | Yes       |

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

### Option 1 — GitHub Private Vulnerability Reporting (preferred)

Use GitHub's built-in private reporting:
[Report a vulnerability](https://github.com/eren-solutions/agent-guardrail/security/advisories/new)

### Option 2 — Email

Send details to **security@eren.solutions** with the subject line:
`[agent-guardrail] Security Vulnerability`

Please include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact assessment
- Your preferred contact for follow-up (optional)

## Response Timeline

| Stage | Target |
|-------|--------|
| Acknowledgment | 48 hours |
| Initial assessment | 5 business days |
| Fix for critical/high | 7 days |
| Fix for medium/low | 30 days |
| Public disclosure | After fix is released |

We follow coordinated disclosure. We will credit reporters in the release notes unless you prefer to remain anonymous.

## Scope

In scope:
- `agent-guardrail` Python package (all versions)
- Hosted Guardrail Proxy API (`http://157.230.82.223`)
- Authentication and authorization logic
- Billing and credit management
- Webhook signature verification

Out of scope:
- Vulnerabilities in third-party dependencies (report upstream)
- Issues already publicly known
- Theoretical vulnerabilities with no practical exploit path

## Security Design Notes

- **Zero core dependencies** — the core guardrail engine uses stdlib only, minimizing supply-chain risk
- **API key authentication** — all agent endpoints require `X-API-Key`; admin endpoints require `X-Admin-Key`
- **Webhook verification** — Blockonomics and Stripe webhooks are signature-verified before processing
- **SQLite WAL mode** — concurrent-safe writes with `BEGIN IMMEDIATE` for credit operations
