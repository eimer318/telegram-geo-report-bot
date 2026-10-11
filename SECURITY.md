# Security Policy

## Supported version

Security fixes are applied to the latest revision on the default branch.

## Reporting a vulnerability

Use **Security → Report a vulnerability** in this repository to submit a private
security report. Do not open a public issue containing tokens, encryption keys,
user data, photos, channel identifiers, or exploit payloads. Include only the
minimum information needed to reproduce the problem, and redact all unrelated
secrets and personal data.

## Deployment requirements

- Keep `.env`, `data/`, and `photos/` outside version control.
- Restrict their filesystem permissions to the service account.
- Use a dedicated Telegram bot and grant only required channel permissions.
- Back up the Fernet key separately from encrypted runtime data.
- Never log Telegram updates, raw user input, tokens, or request headers.
- Run only one long-polling instance for a token.
- Review dependency audit results before deployment.

Suspicious text remains encrypted and pending. It is excluded independently by both export and channel code until an administrator replaces the description and explicitly approves the report.
