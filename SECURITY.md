# Security Policy

## Reporting a Vulnerability

If you discover a security issue, please do **not** open a public GitHub issue.

Instead, report it privately:
- Email the maintainer, or
- Use GitHub Security Advisories (if enabled for this repository)

Please include:
- A clear description of the issue
- Steps to reproduce
- Impact assessment (what an attacker can do)
- Suggested fix (if you have one)

## Secrets

- Do not commit real API keys.
- Keep `.env` local; use `.env.example` as a template.
- This repo includes a lightweight secret scan for common key patterns:

```bash
python scripts/secret_scan.py
```

If you accidentally committed a real secret:
1. Rotate the key immediately.
2. Remove it from the codebase.
3. Consider rewriting git history if it was pushed.

## API Authentication

- Set `SOULSEARCHER_INTERNAL_API_KEY` to enable Bearer token or `X-API-Key` authentication on most `/api/*` endpoints.
- The `SOULSEARCHER_AUTH_USER_HEADER` (default: `X-SoulSearcher-User`) provides per-user session isolation behind an authenticated proxy.
- For public deployments, enable rate limiting (`RATE_LIMIT_ENABLED=true`) to prevent abuse.

## Sandbox Security

- **Remote sandboxes** (E2B/Daytona): Code execution is fully isolated from the host. This is the recommended configuration for production.
- **Host bash** (`HOST_BASH_ENABLED=true`): Opt-in only. When enabled, commands are audited and pipe-to-shell patterns are detected. Not recommended for untrusted environments.
- **Tool approval** (`TOOL_APPROVAL=true`): Enable Human-in-the-Loop approval for potentially risky tool executions.

## Skill Security

- Installed skills undergo automatic security scanning via `agent/skills/security_scanner.py`.
- Each skill declares allowed tools via `allowed-tools` in SKILL.md frontmatter; tool allowlisting is enforced at runtime.
- Skill evolution (`SKILL_EVOLUTION_ENABLED`) is disabled by default. Enable only in trusted environments.

## Dependency Updates

- Dependabot is configured for weekly automated updates (pip, npm, GitHub Actions).
- CI runs on every PR: linting, secret scanning, compilation checks, and tests.
- Pre-commit hooks enforce code quality (trailing whitespace, YAML validation, merge conflict detection, Ruff linting + formatting).

## Secure Development

```bash
# Run secret scan before commits
make secret-scan

# Full pre-commit check
make check          # lint + test + secret-scan

# Pre-commit hooks (auto-run on git commit)
pre-commit install
```
