# Mobile design review

## Changes

- A compact header replaces the permanently expanded project navigation. The
  Menu button opens a scrollable list of projects, TRIAGE and Account and closes it after navigation. The menu overlays the page and supports outside
  tap and Escape dismissal.
- The home page lists projects and provides one clear TRIAGE entry.
- Task descriptions precede the detailed property panel. Secondary task actions
  sit under More actions on small screens; Edit and Move remain visible.
- Board columns scroll sideways, with a partial next-column cue and distinct
  status backgrounds. The column selector also scrolls to the chosen status.
- Inputs use 16px text and controls have a 44px minimum height. Dialog actions
  stay visible while editing, with safe-area padding.
- Preview screens have a sticky selector. Forms stack vertically, tables retain
  their own horizontal scroll, and logo images retain their aspect ratio.

## Evidence and limits

The hosted browser suite checks a 390 × 844 viewport for project/task page overflow
and all 22 wireframe screens; screenshots cover a task and invoice preview.
Desktop account, task, sharing and invitation checks remain in the same suite.
These are browser viewport checks, not physical-device or assistive-technology
certification. Native email-code AutoFill depends on the browser, device and mail
setup. The app supports `autocomplete="one-time-code"`, typing and paste, and
submits a complete six-digit code automatically.

TRIAGE currently lists unarchived tasks by last update, newest first; it is not
an unread inbox. Projects currently have no archive state, so the index lists
all projects available in the selected workspace. The richer notification,
project permission and workflow designs remain review tasks.
