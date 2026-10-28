# Opportunity Hunter MVP

Approved scope (2026-10-17): hybrid internet discovery and monitoring in four categories, evidence-based personal filtering, SQLite, Hermes natural-language Telegram interface, favorites/hiding/rechecks, confirmed profile changes, notifications/digests/deadlines and native Hermes Cron. No applications, registrations, resume delivery, email, trading or financial account connections.

Decision: B, a blank `opportunity-hunter` profile in the existing v0.21.6 installation. A mixes default profile state and modifies its tools/cron; C adds an unnecessary interpreter/Gateway. B separates Hermes state but is not an OS sandbox. The existing Gateway is currently single-profile (`served_profiles=[]`); activation and root configuration persistence require separate permission. All implementation and tests are local until that approval. No production secrets are cloned. No upgrade needed.

Work: (1) audit exact installed interfaces; (2) test and implement SQLite/evaluation; (3) native research bridge with query rotation, HTTP cache/robots/budgets and evidence validation; (4) profile-scoped plugin and deterministic trusted Telegram commands; (5) native Cron/profile packaging with offline verification; (6) real discovery report, automated regression/security tests and usage instructions. Deployment/token-dependent checks remain explicit pending gates.

Tests distinguish isolated backend/component tests from an actual Hermes/Gateway/Telegram integration. Fixture import or mocked transport is not evidence of live scheduled delivery. Search failures and partial runs must never be reported as complete coverage or no opportunities.
