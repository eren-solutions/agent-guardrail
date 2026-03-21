# Contributing to Agent Guardrail

Thanks for your interest. Contributions are welcome — bug fixes, new features, docs, and tests.

## Quick Start

```bash
git clone https://github.com/eren-solutions/agent-guardrail.git
cd agent-guardrail
pip install -e ".[dev,proxy,stripe]"
```

## Workflow

1. **Fork** the repo and create a branch from `main`
2. **Make your changes** with tests
3. **Verify** everything passes locally (see below)
4. **Open a pull request** — describe what you changed and why

Branch naming convention: `fix/short-description` or `feat/short-description`

## Code Style

We use [black](https://black.readthedocs.io/) and [flake8](https://flake8.pycqa.org/).

```bash
# Format
black --line-length=100 agent_guardrail/ tests/

# Lint (must be clean)
flake8 agent_guardrail/ --max-line-length=100 --extend-ignore=E501

# Both are enforced in CI — PRs must be clean before merge
```

Configuration is in `pyproject.toml` (`[tool.black]`).

## Tests

All new features and bug fixes must include tests.

```bash
# Run the full suite
pytest tests/ -v

# Run a specific test file
pytest tests/test_billing.py -v
```

CI runs on Python 3.10, 3.11, and 3.12 — test locally on your version, CI covers the rest.

## Pull Request Checklist

- [ ] `black --check` passes
- [ ] `flake8` passes (zero errors)
- [ ] All existing tests pass (`pytest tests/`)
- [ ] New tests added for new behavior
- [ ] Docstrings updated if public API changed
- [ ] `CHANGELOG` entry added (if applicable)

## Issue Templates

- **Bug report**: Describe expected vs actual behavior, include version and minimal repro
- **Feature request**: Describe the use case and why it belongs in agent-guardrail

## Design Principles

- **Zero core dependencies** — the `agent_guardrail` engine uses stdlib only. Optional extras (`proxy`, `stripe`) are allowed but must stay behind `try/except ImportError` guards.
- **Backward compatible** — billing is gracefully disabled when keys are absent; new features should degrade cleanly.
- **Test-first for billing/auth** — the credit/payment logic is security-sensitive; tests are non-negotiable.

## License

By contributing, you agree your contributions are licensed under the [MIT License](LICENSE).
