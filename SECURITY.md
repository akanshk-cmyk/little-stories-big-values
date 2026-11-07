# Security and private data

API credentials belong in environment variables or a local `.local/.env` file.
The committed `.env.example` contains variable names and empty credential values.
Never put real credentials in JavaScript, screenshots, issues or commit messages.

The public release excludes local databases, parent/child records, private PDFs,
saved user stories, browser profiles and raw provider traces. Tests create isolated
synthetic accounts; their fixture strings are not working account credentials.

The current server is a local development application. Do not expose it directly
to the public internet. A public GitHub repository does not mean a production
hosting, privacy, cultural-quality or security audit has been completed.

Before publishing future changes, prepare and scan a clean release:

```powershell
uv run python scripts/prepare_public_release.py --output .local/github-release/next-release
```

The tool scans the selected files for known credential patterns, private configuration
values and prohibited runtime artifacts. It stops on unresolved matches and never
prints secret values. Automated scans reduce risk but cannot prove the absence of
every possible confidential detail.

If a real credential is exposed, revoke/rotate it at its provider immediately and
remove it from repository history. Do not post the credential in a public issue.
