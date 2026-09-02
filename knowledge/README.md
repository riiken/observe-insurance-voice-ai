# Knowledge

Content the agent is allowed to say, kept **out** of the system prompt so it can
be reviewed and changed by the claims team without touching agent behaviour
(CLAUDE.md §12).

> ⚠️ **Everything here is demo content.** Observe Insurance is a fictional
> company invented for a take-home exercise. The hours, addresses, websites and
> timescales are all made up. Nothing in this directory is any real business's
> policy, and each file repeats that warning in its own header.

## FAQ topics — one Markdown file each

| File | Topic | Required by the assignment |
| ---- | ----- | -------------------------- |
| `office_hours.md` | Office hours | yes |
| `mailing_address.md` | Mailing address | yes |
| `new_claim.md` | Starting a new claim | yes |
| `claims_process.md` | How the claims process works | yes |
| `document_submission.md` | Sending documents in | no — see below |

The four required topics must all be present or **the service refuses to
start**. Better to fail on boot than to discover the gap when a caller asks.

`document_submission.md` is extra. It was written in Phase 5 and is kept
because a caller told their claim needs documents asks this next; removing it
would regress working behaviour.

### File format

```markdown
---
id: office_hours
topic: Office hours
keywords: hours, open, when, weekend, ...
---

> ⚠️ **DEMO CONTENT — NOT REAL COMPANY POLICY.** ...

## Answer

The text spoken to the caller, verbatim.

## Notes for maintainers

Never spoken. Context for whoever edits the file.
```

Only the `## Answer` section is ever read aloud — which is exactly why the demo
disclaimer can live in the same file. Write that section the way it should
sound: short sentences, no lists, no markdown, no abbreviations a text-to-speech
engine would mangle ("Massachusetts", not "MA").

`keywords` is how the question is matched. Adding a phrasing a caller actually
used is the normal way to improve recall — it needs no code change.

A Markdown file with no frontmatter block (this README, for one) is skipped. A
file *with* frontmatter but missing `id`, `topic`, `keywords` or `## Answer`
fails the service on startup: that is a broken knowledge file, not an unrelated
one.

## Other content

| File | Purpose |
| ---- | ------- |
| `claim_guidance.json` | Per-status next steps and document submission instructions, read by `get_claim_status`. Every claim status must be configured or startup fails. |

`claim_guidance.json`'s `submission` block duplicates `document_submission.md`.
They are kept in step by hand — see `docs/DEFERRED.md` item 4.4.
