# AI Usage

## Tools used
- **GitHub Copilot (AI assistant in VS Code):** assisted with the browser-local SHA-256 ledger module, record validation and chaining, localStorage schema checks, QR payload/display flow, verification UI, the welcome/Get started page, serif display typography and reduced-motion-aware transitions, accessibility/responsive refinements, the default graphite-and-neon-lime theme, and focused automated tests.

## Human review and testing
- Automated tests cover valid chains, changed fields, incomplete records, duplicate IDs and results, QR/ID/hash lookup, and malformed payloads.
- Browser checks exercised the welcome/Get started flow, publishing, duplicate rejection, verification, QR generation, persistence across reloads, tamper detection, correction re-hashing, settings persistence, damaged-storage handling, app-view typography/entrance transitions, reduced-motion behavior, and responsive layouts from 320px to desktop without page overflow.
- No independent human code review is claimed; the project owner should review the UI and security limitations before presenting or extending it.
- No real student records, credentials, or confidential data were used as test fixtures.

## Important limitation
This application runs entirely in the browser. LocalStorage and client-side hashes are not a trusted source of academic credentials: users can edit the data or rebuild the hash chain. Production use requires an authenticated backend, server-side verification, and a trusted signing key.
