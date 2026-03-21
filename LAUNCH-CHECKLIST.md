# Agent Guardrail — Launch Checklist

## Pre-Launch (Kael)
- [x] CI green on all Python versions (3.10, 3.11, 3.12 — run 23386600475, 2026-03-21)
- [x] black + flake8 clean (run 23386600475)
- [x] 102+ tests passing
- [x] SECURITY.md added
- [x] CONTRIBUTING.md added
- [x] VPS health check passing (`{"status":"ok","service":"agent-guardrail","version":"0.1.1"}`)
- [ ] BTC payment flow tested
- [x] Smithery listing submitted — https://github.com/smithery-ai/registry/issues/12
- [x] awesome-mcp-servers PR/issue submitted — https://github.com/punkpeye/awesome-mcp-servers/issues/3660

## Launch Approval (Eren)
- [x] Eren reviews and says "go" — Gate 7 approved 2026-03-21

## Post-Launch
- [ ] Monitor first 24h for errors
- [ ] Respond to any GitHub issues within 24h
- [ ] Track first payment

## Registry Status (as of 2026-03-21)
- **PyPI**: https://pypi.org/project/agent-guardrail/ — v0.1.2 live (3 releases: 0.1.0, 0.1.1, 0.1.2)
- **MCP Registry**: https://registry.modelcontextprotocol.io/v0.1/servers?search=agent-guardrail — active since 2026-03-14
- **VPS**: https://157-230-82-223.sslip.io/guardrail/health — responding
- **A2A Agent Card**: https://157-230-82-223.sslip.io/guardrail/.well-known/agent-card.json — responding
- **GitHub CI**: All green — https://github.com/eren-solutions/agent-guardrail/actions/runs/23386600475
- **awesome-mcp-servers**: Issue submitted — https://github.com/punkpeye/awesome-mcp-servers/issues/3660
- **Smithery**: Issue submitted — https://github.com/smithery-ai/registry/issues/12

## Notes
- `.well-known/agent.json` in original checklist was a typo — the actual endpoint is `.well-known/agent-card.json` (A2A protocol)
- Smithery auto-discovers via `smithery.yaml` in repo root — issue filed to prompt indexing
- BTC payment flow not yet marked done — needs manual verification with live Blockonomics key
