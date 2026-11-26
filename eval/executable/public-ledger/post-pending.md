# Two-Phase Transfers

A two-phase transfer moves funds in stages:

1. Reserve funds ([pending](#reserve-funds-pending-transfer))
2. Resolve funds ([post](#post-pending-transfer), [void](#void-pending-transfer), or
   [expire](#expire-pending-transfer))

The name "two-phase transfer" is a reference to the
[two-phase commit protocol for distributed transactions](https://en.wikipedia.org/wiki/Two-phase_commit_protocol).

## Reserve Funds (Pending Transfer)

A pending transfer, denoted by [`flags.pending`](../reference/transfer.md#flagspending),
reserves its `amount` in the debit/credit accounts'
[`debits_pending`](../reference/account.md#debits_pending)/[`credits_pending`](../reference/account.md#credits_pending)
fields, respectively. Pending transfers leave the `debits_posted`/`credits_posted` unmodified.

## Resolve Funds

Pending transfers can be posted, voided, or they may time out.

### Post-Pending Transfer

A post-pending transfer, denoted by
[`flags.post_pending_transfer`](../reference/transfer.md#flagspost_pending_transfer), causes a
pending transfer to "post", transferring some or all of the pending transfer's reserved amount to
its destination.

- If the posted [`amount`](../reference/transfer.md#amount) is less than the pending transfer's
  amount, then only this amount is posted, and the remainder is restored to its original accounts.
- If the posted [`amount`](../reference/transfer.md#amount) is equal to the pending transfer's
  amount or equal to `AMOUNT_MAX` (`2^128 - 1`), the full pending transfer's amount is posted.
- If the posted [`amount`](../reference/transfer.md#amount) is greater than the pending transfer's
  amount (but less than `AMOUNT_MAX`),
  [`exceeds_pending_transfer_amount`](../reference/requests/create_transfers.md#exceeds_pending_transfer_amount)
  is returned.

<details>
<summary>Client &lt; 0.16.0</summary>

- If the posted [`amount`](../reference/transfer.md#amount) is 0, the full pending transfer's
  amount is posted.
- If the posted [`amount`](../reference/transfer.md#amount) is nonzero, then only this amount
  is posted, and the remainder is restored to its original accounts. It must be less than or equal
  to the pending transfer's amount.

</details>
