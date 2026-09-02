# Deferred work

Running ledger of everything consciously left undone, and where it will land.
Updated at the end of every phase: items are added when a phase defers them and
struck off when a later phase picks them up.

Anything here is a **decision**, not an oversight. If it is not written down, it
was not deferred — it was missed.

---

## Open

### From Phase 1 — foundation

| # | Item | Why deferred | Lands in |
| - | ---- | ------------ | -------- |
| 1.1 | Session state model and the authentication boundary | Needs the customer/claim data it authorises access to | Phase 3 (`services/`) |
| 1.2 | The six agent tools (`lookup_customer`, `verify_identity`, `get_claim_status`, `search_faq`, `request_representative`, `complete_call`) | Tools wrap services, which do not exist yet | Phase 3 (`tools/`) |
| 1.3 | Agent prompts, turn handling, escalation and emergency routing | Depends on tools | Phase 4 (`agents/`) |
| 1.4 | Voice platform webhook + API key enforcement (`VOICE_PLATFORM_API_KEY` is configured but unread) | No conversation to drive yet | Phase 4 (`api/v1/`) |
| 1.5 | FAQ knowledge content | Not needed until the FAQ tool exists | Phase 3 (`knowledge/`) |
| 1.6 | Post-call interaction persistence (Integration #2) | Separate integration from customer/claims | Phase 5 (`integrations/`) |

---

## Closed

_Nothing yet._
