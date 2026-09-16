# Review notes

This is a design package, not a product release. Review each PRD with its linked
screen, then record decisions on the corresponding private project task.

## What was checked

- All 21 PRDs have working local document links, dependencies and acceptance criteria.
- All 22 gallery sections were opened at 390px and 1440px. No document-level
  horizontal overflow was found. Wide data tables and the board scroll within
  their own panels.
- The phone navigation, customer portal, Comms conversion dialog, invoice fee
  toggle/rate, and Command-K search were exercised in Chrome.
- A 1% sample fee on $14,500 produces $14,645; switching it off restores $14,500.
- Search accepts keyboard input and Enter navigation; Escape closes it.
- No gallery-origin console errors were observed. An unrelated browser extension
  logged an error while loading pages.
- New design files were checked for deployment/customer identifiers and private
  provider details. This does not certify older repository content; F21 covers it.

## Boundaries

The gallery uses sample data and simulated actions. It does not enforce permissions,
send email, create tasks, move money or run agents. PRDs and shared contracts govern
implementation where a simplified screen omits a rule. Browser review here is
layout/interaction evidence, not the authorization, payment or recovery testing
required for release.

Download `wireframes.html` and open it in a browser; it works without a server or
external assets. Each PRD names its screen. Use the sidebar on desktop, the screen
picker on a phone, or Command-K. Keep private agency/customer branding examples
in the hosted tenant's review package rather than this repository.
