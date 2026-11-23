# VERIGRADE – Local Tamper-Evident Exam Result Ledger

> *Trust Every Result. Verify Every Record.*

A web-based exam-result ledger with an HTML5, CSS3 and Vanilla JavaScript frontend and a Flask backend.

## Problem Statement
Exam marksheets can be forged or silently edited after publication. Institutions and employers need a clear way to see whether a result is original, pending, or tampered.

## Objectives
- Show how a tamper-evident results workflow could look and feel.
- Demonstrate two-step approval for result corrections.
- Offer a public-facing verification screen.

## Features
- Welcome page with a clear Get started path into the ledger
- Overview dashboard with live statistics
- Students list with search, department filter, details and add-student modal
- Results ledger with search, integrity filters, pagination, record export and QR display
- Publish any student result with SHA-256 Web Crypto hashing and a chained `prevHash`
- Verify records by ID, SHA-256 hash or QR JSON payload
- Administrator account-role management
- Correction approvals using two distinct authenticated approver accounts
- Audit logs with search and status filter
- Settings and ledger records persisted in browser `localStorage`
- Dark graphite and neon-lime theme with adjustable green accents
- Responsive layout, keyboard-friendly controls and accessible text status labels

> **Security model:** Login uses signed server sessions and CSRF tokens for mutations. New accounts have the `viewer` role; publishing requires the `publisher` or `admin` role. Correction approval requires a signed-in approver and re-authentication of a second, distinct approver. Never expose API keys in the browser or commit secrets to the repository.

## Technology Stack
HTML5 · CSS3 · Vanilla JavaScript · Web Crypto API · Lucide icons (pinned CDN) · QRCode.js (pinned CDN) · Inter font (Google Fonts CDN)

## Folder Structure
```text
VERIGRADE/
├── index.html
├── ledger.js
├── style.css
├── script.js
├── AI_USAGE.md
├── tests/
│   └── ledger.test.js
├── .github/
│   └── workflows/
│       └── pages.yml
├── README.md
└── .gitignore
```

## How to Run Locally
1. Open the `VERIGRADE` folder in VS Code.
2. Install the **Live Server** extension.
3. Right-click `index.html` → **Open with Live Server**.

Web Crypto requires a secure context; use HTTPS or localhost rather than opening the page directly as a `file://` URL. The QR image is generated locally in the browser; it does not send record data to the QR library CDN.

The ledger starts empty. Add a student or publish a result to create records. A published result includes its record ID, roll number, name, exam, subject, marks, optional metadata, timestamp, `prevHash`, and SHA-256 `hash`. The first record links to the all-zero genesis hash. If stored fields or links change, verification reports the first affected block.

Records, correction requests, audit entries, and settings are saved per browser profile. A QR payload identifies a record but does not contain a server-verifiable signature; another browser can verify it only if it has the same local ledger data.

The login and signup flows use the Flask backend. New accounts default to viewer access. Set `VERIGRADE_ADMIN_EMAIL` to the email address of the trusted initial administrator; that account is promoted on sign-in and can assign roles from **Settings → Account access**. Correction requests are saved per browser; approval appends the corrected result to the shared server ledger after two distinct approvers authenticate. Rejection is not yet available.

Run the ledger tests with `node --test tests/ledger.test.js`.

## Deployment
The site is published with GitHub Pages from the `main` branch using GitHub Actions. Each push to `main` redeploys the static files; the workflow can also be started manually from the repository's **Actions** tab.

Live site: https://navin1518.github.io/Verigrade/

Backend API: https://web-production-fb17e087.up.railway.app/

If GitHub Pages has not been enabled for the repository yet, open **Settings → Pages** and set the build and deployment source to **GitHub Actions**.

### Run the Flask backend locally
```powershell
py -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
pip install -r Backend/requirements.txt
python Backend/app.py
```

The app is available at `http://127.0.0.1:5000`.

### Deploy the backend with Railway
1. Open Railway and choose **Deploy from GitHub repo**.
2. Select `Navin1518/Verigrade`.
3. Railway will use `railway.json` and start the Flask app with Gunicorn.
4. Open **Settings -> Networking -> Generate Domain**.
5. The generated URL is the public backend URL.

Configure these Railway environment variables:

- `VERIGRADE_CORS_ORIGINS`: comma-separated allowed frontend origins. Leave unset only for same-origin/API tooling scenarios.
- `VERIGRADE_SESSION_SECRET`: a long random secret used to sign login sessions.
- `VERIGRADE_COOKIE_SECURE`: set to `1` in HTTPS production deployments.
- `VERIGRADE_ADMIN_EMAIL`: email address of the account to bootstrap as the initial administrator.
- `VERIGRADE_API_KEYS`: backend-only credentials with publisher/approver roles for direct API integrations. The frontend no longer collects or sends these keys.

## Screenshots
_Add screenshots here._

## Roadmap
- Authenticated backend and shared database
- Server-side verification and cryptographic signatures
- Server-backed correction request and rejection workflow
- Integrated camera scanning for QR payloads

## AI Usage Acknowledgement
See [AI_USAGE.md](./AI_USAGE.md) for AI assistance and human review details.