# VERIGRADE – Tamper-Proof Exam Result Ledger

> *Trust Every Result. Verify Every Record.*

A frontend-only prototype of a premium exam-result ledger dashboard, built for a hackathon with HTML5, CSS3 and Vanilla JavaScript.

## Problem Statement
Exam marksheets can be forged or silently edited after publication. Institutions and employers need a clear way to see whether a result is original, pending, or tampered.

## Objectives
- Show how a tamper-evident results workflow could look and feel.
- Demonstrate two-step approval for result corrections.
- Offer a public-facing verification screen.

## Features
- Overview dashboard with live statistics
- Students list with search, department filter, details and add-student modal
- Results ledger with search, status filter, pagination, view and download actions
- Publish Result form with validation
- Verify Result screen (simulated)
- Corrections & Approvals with two approvers and a confirmation modal
- Audit logs with search and status filter
- Settings UI, responsive layout, keyboard-friendly controls

> **Important:** All data is synthetic and lives in browser memory. Verification is a **simulated demo**. No cryptography, backend or database is used.

## Technology Stack
HTML5 · CSS3 · Vanilla JavaScript · Lucide icons (CDN) · Inter font (Google Fonts CDN)

## Folder Structure
```text
VERIGRADE/
├── index.html
├── style.css
├── script.js
├── README.md
└── .gitignore
```

## How to Run Locally
1. Open the `VERIGRADE` folder in VS Code.
2. Install the **Live Server** extension.
3. Right-click `index.html` → **Open with Live Server**.

Demo verification: Student ID `STU-1001` with reference `VG-2001` shows *Verified*; `STU-1003` / `VG-2003` shows *Tampered*; `STU-1004` / `VG-2004` shows *Invalid record*; anything else shows *Record not found*.

## Deployment
The site is published with GitHub Pages from the `main` branch using GitHub Actions. Each push to `main` redeploys the static files; the workflow can also be started manually from the repository's **Actions** tab.

Live site: https://navin1518.github.io/Verigrade/

If GitHub Pages has not been enabled for the repository yet, open **Settings → Pages** and set the build and deployment source to **GitHub Actions**.

## Screenshots
_Add screenshots here._

## Roadmap
- Real backend and database
- Hash-based ledger and cryptographic verification
- Role-based login for administrators and approvers
- QR code generation and scanning

## AI Usage Acknowledgement
_Describe which parts of this project were created with AI assistance._