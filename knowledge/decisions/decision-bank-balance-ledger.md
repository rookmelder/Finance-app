# Decision: bank balance as an append-only ledger
> The bank balance is the last typed amount plus later changes, computed from an append-only ledger so every change and the chart history are kept.

Back to [Decisions](decisions.md).

## Decision
Store every typed balance ('set') and every automatic pay/subscription change as an event; compute the current balance and history from them; expenses stay in their own table and are counted by date and id relative to the set. The balance never resets at month end.

## Why
- The user wants to see how the balance evolves and keep every change; a single stored number would lose that.
- Pay and subscriptions must apply automatically on their dates without the user entering real pay.
- Corrections (typing the balance again, entering the real pay) are new events, never edits.

## Alternatives not taken
- One mutable balance column (no history).
- Copying expenses into the ledger (they could go stale when edited or deleted).

See [Bank account balance](../systems/system-bank-balance.md).
