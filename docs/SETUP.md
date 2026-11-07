# Local setup

This repository publishes application source code, not a hosted public service.
The loopback Python server is not intended to be exposed directly to the internet.

## Requirements

- Python 3.14 and uv; the project manifest requires Python >=3.14,<3.15.
- A Groq account with access to the model you configure.
- An installed Chrome or supported browser for PDF export.

From the project folder:

```powershell
uv sync --locked
```

Create `.local/.env` using `.env.example`. Configure `GROQ_API_KEY` and
`GROQ_MODEL` locally. Do not commit the populated file. `CHROME_PATH` optionally
specifies the PDF browser. Actual writing and reviewing use provider credits.

## Private source prerequisite

The current live entry point still checks its existing Ramayan workspace at startup,
even though original-fiction requests do not retrieve Ramayan passages. Its audited
source extraction, original PDF and matching automated source-QA records are private
and intentionally excluded from this repository. A fresh clone therefore cannot
start this particular live entry point until those prerequisites are prepared.

```powershell
uv run python -m backend.ramayan_live_server --check
uv run python -m backend.ramayan_live_server --port 8003
```

The readiness check does not call a paid model. Missing source QA is not generated
automatically at startup. Open `http://localhost:8003/index.html` after readiness
passes. Register your own parent account and select/create a child profile. There
are no published parent/admin credentials or preloaded private accounts.

## Controlled checks without provider credits

These use temporary synthetic accounts and controlled provider responses:

```powershell
$env:PYTHONPATH='.;tests'
uv run python -m unittest tests.test_original_fiction_flow tests.test_compact_story tests.test_text_only_release tests.test_local_intent tests.test_stage9_account_api
uv run python scripts/check_original_fiction_browser.py
```

The browser check needs permission to launch the installed browser. No live
generation or held evaluation dataset is run by these commands. Temporary test
passwords and invalid credential-shaped URLs in security tests are fixtures, not
working service or account credentials.

## Publication boundaries

Private configuration, PDFs, account databases, saved user books, generated traces,
browser profiles, pitch files and historical runtime reports are not included.
Use only documents and artwork you have permission to use. The repository contains
user-supplied static selection artwork; publication is not a universal rights grant
for the source documents, referenced artwork or generated content.
