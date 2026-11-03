# Security policy

skillcheck runs untrusted skills through an AI agent on your machine. Read
`skills/skillcheck-run/references/isolation.md` for what is and is not isolated.

## Reporting a vulnerability

Please report privately: use GitHub's "Report a vulnerability" (private security advisory) on this repository,
or email the maintainers listed in the repository profile. Do not open a public issue. We aim to acknowledge
within 3 working days and to agree on a disclosure date with you.

In scope: sandbox or permission escapes caused by the runner's configuration, secret leakage into traces or
reports, hash-chain verification bypasses, and grader code execution outside the configured sandbox.
