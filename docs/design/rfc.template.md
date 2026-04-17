---
title: "<RFC title>"
status: draft        # draft | accepted | superseded
created: 2026-04-18
stage: 3
owners:
  - backend
consumers:
  - <module(s) in Stage 4 that will implement this>
---

# <RFC title>

## Context

One or two short paragraphs. *Why does this RFC exist?* What upcoming
Stage-4 module(s) will consume the decision? What breaks if we don't
decide this now?

## Decision

**One paragraph.** State the actual choice clearly so a module author
reading the RFC for the first time learns the answer without having to
reconstruct it from the discussion below.

## Details

### Scope

What is covered. Be concrete — name the endpoints, tables, components,
or flows this RFC governs.

### Non-goals

What is explicitly NOT covered. If a reader might reasonably expect this
RFC to handle a thing and it doesn't, call it out here and point them
to the RFC that does.

### Implementation sketch

Not a full spec — just enough for the module author to know *where* the
code lands:

- Files / modules touched
- Key data structures (e.g. Redis key layout, new DB columns)
- Required Stage-0/1 primitives this builds on (e.g. `src/shared/security/tenant.py`, `write_audit()` from `src/shared/security/audit.py`)
- Pseudocode only for the non-obvious parts

### Alternatives considered

Two or three, with one-sentence reasons each was rejected. "Why not X?"
questions that reviewers will ask anyway.

## Consequences

### Positive

Bullet list. What gets easier / safer / faster because of this decision.

### Negative / tradeoffs

Bullet list. What we accept as cost. Be honest — if the tradeoff is
"more code", say so.

### Future work

- Known follow-ups (link to NEXT_SESSION.md entries if applicable)
- Conditions under which this decision should be revisited
  (e.g., "when messages table exceeds 10M rows")

## Cross-references

- Related RFC: [name](name.md) — reason for the link
- Consumed by: `src/modules/<name>/` — module(s) that implement this
- Reference doc: `reference-docs/<path>` — if the decision edits or
  contradicts an existing reference doc, say so explicitly
