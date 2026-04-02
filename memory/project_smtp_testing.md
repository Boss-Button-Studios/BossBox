---
name: SMTP Testing Setup — Proton Mail
description: User has a burner Proton Mail address for SMTP testing; Proton Mail Bridge not yet installed
type: project
---

User created a burner Proton Mail address for SMTP testing in Step 17.

**Why:** Needed a safe, disposable email account to test BossBox SMTP notifications without exposing a real address.

**How to apply:** When testing live SMTP, remind the user that Proton Mail requires Proton Mail Bridge (proton.me/mail/bridge) running locally. Bridge exposes SMTP at 127.0.0.1:1025 with use_tls: false. The Bridge password (not Proton account password) is used for SMTP auth. This is already documented in config/notify.yaml comments. Bridge is not yet installed — step 17 SMTP tests use mocks only.
