# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in bcli, please report it privately instead of opening a public issue.

**Preferred method:** Use GitHub's [Private Vulnerability Reporting](https://github.com/igor-ctrl/bcli/security/advisories/new) feature.

**Alternative:** Open a draft security advisory on the repository, or contact the maintainer directly.

Please include:
- A description of the vulnerability
- Steps to reproduce
- The version of bcli affected
- Any known mitigations

We'll acknowledge the report within 7 days and work with you on a fix and disclosure timeline.

## Supported Versions

Only the latest minor version receives security updates during the alpha phase.

## Scope

In scope:
- The `bcli` Python SDK and CLI (this repository)
- Authentication and secret handling
- Input validation in request construction

Out of scope:
- Microsoft Business Central server-side vulnerabilities (report to Microsoft)
- Issues in third-party dependencies (report upstream; we'll track via dependency updates)
