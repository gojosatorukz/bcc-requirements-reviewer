## Transfer Flags

The transfer `flags` value is a bitfield. See details for these flags in
the [Transfers
reference](https://docs.tigerbeetle.com/reference/transfer#flags).

To toggle behavior for an account, use the `types.TransferFlags` struct
to combine enum values and generate a `uint16`. Here are a
few examples:

* `TransferFlags{Linked: true}.ToUint16()`
* `TransferFlags{Pending: true}.ToUint16()`
* `TransferFlags{PostPendingTransfer: true}.ToUint16()`
* `TransferFlags{VoidPendingTransfer: true}.ToUint16()`

For example, to link `transfer0` and `transfer1`:

```go
transfer0 := Transfer{
	ID:              ToUint128(4),
	DebitAccountID:  ToUint128(101),
	CreditAccountID: ToUint128(102),
	Amount:          ToUint128(10),
	Ledger:          1,
	Code:            1,
	Flags:           TransferFlags{Linked: true}.ToUint16(),
}
transfer1 := Transfer{
	ID:              ToUint128(5),
	DebitAccountID:  ToUint128(101),
	CreditAccountID: ToUint128(102),
	Amount:          ToUint128(10),
	Ledger:          1,
	Code:            1,
	Flags:           0,
}

transferResults, err := client.CreateTransfers([]Transfer{transfer0, transfer1})
// Results handling omitted.
```

### Two-Phase Transfers

Two-phase transfers are supported natively by toggling the appropriate
flag. TigerBeetle will then adjust the `credits_pending` and
`debits_pending` fields of the appropriate accounts. A corresponding
post pending transfer then needs to be sent to post or void the
transfer.

#### Post a Pending Transfer

With `flags` set to `post_pending_transfer`,
TigerBeetle will post the transfer. TigerBeetle will atomically roll
back the changes to `debits_pending` and `credits_pending` of the
appropriate accounts and apply them to the `debits_posted` and
`credits_posted` balances.

```go
transfer0 := Transfer{
	ID:              ToUint128(6),
	DebitAccountID:  ToUint128(101),
	CreditAccountID: ToUint128(102),
	Amount:          ToUint128(10),
	Ledger:          1,
	Code:            1,
	Flags:           TransferFlags{Pending: true}.ToUint16(),
}

transferResults, err := client.CreateTransfers([]Transfer{transfer0})
// Results handling omitted.

transfer1 := Transfer{
	ID: ToUint128(7),
	// Post the entire pending amount.
	Amount:    AmountMax,
	PendingID: ToUint128(6),
	Flags:     TransferFlags{PostPendingTransfer: true}.ToUint16(),
}

transferResults, err = client.CreateTransfers([]Transfer{transfer1})
// Results handling omitted.
```
