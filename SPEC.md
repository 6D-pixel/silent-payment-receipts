# Silent Payment Receipts, draft v0

```
Title:    Silent Payment Receipts
Status:   Draft (not a BIP; for discussion)
Requires: BIP-340, BIP-341, BIP-352, BIP-374
Related:  BIP-375
```

## Abstract

A silent payment (BIP-352) pays a fresh, random-looking taproot output, so nobody but
the receiver can tell who was paid. That also means the sender has no way to show a
third party (a shop, marketplace, auditor or court) that a payment was made.

This draft defines a **receipt**: a small, private proof that a confirmed transaction
paid a given silent-payment address, tied to one order or statement. The sender makes
it from keys it already holds; anyone can check it against the blockchain. A mirror
**receiver proof** lets the receiver show that an output is its own.

The cryptography is not new. A receipt is the ECDH share and BIP-374 DLEQ proof that
BIP-375 already puts in PSBTs, with BIP-374's optional message field (which BIP-375
leaves empty) used to bind the proof to a transaction, an address and a memo.

## Motivation

* **Disputes.** "You never paid." The buyer needs proof a third party can check
  without the seller's keys.
* **Audits and accounting.** A company must prove single payments without opening its
  wallet.
* **Why not share the tweak?** BIP-352's out-of-band notification sends the output
  tweak `t`. A check of `P = B_m + t*G` is not enough: a sender can build an output
  `P = Q + hash(Q || script)*G` with `Q = B_m + r*G`, reveal `t = r + hash(...)`, and
  keep a hidden taproot script path to take the money back. The receiver's wallet
  never finds such an output. A receipt forces `t = hash(S || k)` for the real
  shared secret `S`, which rules this out. (`demo/regtest_demo.py` shows both on
  regtest.)

Similar tools exist on other chains: Monero's `get_tx_proof` / `check_tx_proof` and
Zcash payment disclosures (ZIP-311). This draft is the Bitcoin silent-payment version.

## Notation

* `G`, `n`: the secp256k1 generator and order. `ser(P)`: 33-byte compressed point.
* `hash_tag(x)`: BIP-340 tagged hash.
* From BIP-352: eligible inputs and their public keys `A_j` (taproot keys lifted to
  even y), `A = sum(A_j)`, `input_hash = hash_BIP0352/Inputs(outpoint_L || ser(A))`,
  address `(B_scan, B_m)`, `t_k = hash_BIP0352/SharedSecret(ser(S) || ser32(k))`,
  output key `P_k = B_m + t_k*G`.
* `DLEQ(x; G1, B, m)`: BIP-374 `GenerateProof` for secret `x`, base `G1`, point `B`,
  message `m`, proving `log_G1(x*G1) = log_B(x*B)`.

## The message

Every proof in a receipt signs the same 32-byte message:

```
m = hash_SPReceipt/v0/message( role || txid || ser(B_scan) || ser(B_m) || sha256(memo) )
```

* `role`: `0x00` sender, `0x01` receiver. A receiver proof can never pass as a claim
  by the payer, and the reverse.
* `txid`: 32 bytes, internal byte order. Binds the receipt to one transaction, so it
  cannot be moved to an RBF replacement that spends the same inputs.
* `memo`: UTF-8, at most 1024 bytes: an order id, invoice number or statement.

## Sender receipt

The sender controls a set `J` of eligible inputs with secret keys `a_j` (negated as
BIP-352 requires for odd-y taproot keys). Let `a_J = sum(a_j)`.

1. `C_J = a_J * B_scan`: the ECDH share. This equals BIP-375's `PSBT_GLOBAL_SP_ECDH_SHARE`
   when `J` is every eligible input.
2. `proof_J = DLEQ(a_J; G, B_scan, m)` with `role = sender`.
3. The share entry is `(J, C_J, proof_J)`.

A receipt is complete when its shares cover every eligible input exactly once. In
multi-party transactions (payjoin, coinjoin) each party makes a share for its own
inputs with the same memo, and the shares are merged.

## Receiver proof

The receiver knows `b_scan` and `b_spend` (plus the label tweak for `B_m`).

1. `C = b_scan * A`, covering every eligible input (the same value as the sender's `C`).
2. `proof = DLEQ(b_scan; G, A, m)` with `role = receiver`.
3. For every output `P_k` the transaction pays to `B_m`, a BIP-340 signature by the
   output key `d_k = b_m + t_k` over
   `hash_SPReceipt/v0/output( m || txid || ser32le(vout) )`.

The DLEQ alone only shows knowledge of `b_scan`, which scanning servers also hold.
The output signature shows the receiver can spend. Receivers must derive `A` from a
transaction they fetched and parsed themselves, never from points a requester
supplies; otherwise they become a static Diffie-Hellman oracle on `b_scan`.

## Verification

Given a receipt, the verifier:

1. Fetches the transaction by `txid` from its own node or explorer, and the funding
   transaction of every input. Each funding transaction must hash to the txid in its
   outpoint. Nothing about the inputs is taken from the receipt.
2. Decodes the address to `(B_scan, B_m)`; the prefix must match the network.
3. Applies the BIP-352 v0 rules: no input spends segwit v2 or higher, and at least one
   input is eligible. Computes each `A_j`.
4. Checks that the shares cover every eligible input exactly once.
5. Computes `m` for the receipt's role.
6. Sender: for each share, `A_J = sum(A_j for j in J)` and BIP-374
   `VerifyProof(A_J, B_scan, C_J, proof_J, m)` must pass.
   Receiver: exactly one share, and `VerifyProof(B_scan, A, C, proof, m)` with base `G`
   must pass.
7. `C = sum(C_J)`, `S = input_hash * C`. For `k = 0 .. (number of taproot outputs - 1)`
   (at most `K_max`), finds every output whose x-only key equals `P_k`. There must be
   at least one. If the receipt lists outputs, they must be exactly these.
8. Receiver: every paid output needs a valid output-key signature.
9. The transaction must have at least `min_conf` confirmations (default 1). A verifier
   that cannot check confirmations must say so and must not treat the receipt as final.

The result is: this transaction paid these outputs and amounts to this address, and
the payer (or the receiver) signed off on this memo.

## Encoding (JSON, v0)

```json
{
  "spreceipt": 0,
  "role": "sender",
  "network": "main | test | signet | regtest",
  "txid": "<display hex>",
  "address": "sp1q...",
  "memo": "Order #1001",
  "shares": [{"inputs": [0, 1], "share": "<33-byte hex>", "proof": "<64-byte hex>"}],
  "outputs": [{"vout": 0, "k": 0, "amount_sat": 30000000}],
  "output_sigs": {"0": "<64-byte hex>"},
  "bundle": {"tx": "<raw hex>", "prevout_txs": ["<raw hex>"]}
}
```

`outputs` is optional and only a claim; the verifier recomputes it. `output_sigs`
appears only in receiver proofs. `bundle` is optional: it lets a verifier check the
math offline, but it does not prove the transaction is confirmed. The cryptographic
core is one 33-byte share and one 64-byte proof per party.

## Security and privacy

* **What a receipt reveals.** `input_hash` is public, so a receipt reveals
  `a*B_scan`, the sender's shared secret with this receiver for this key sum. Anyone
  holding it can find other payments from the same input keys to the same receiver.
  Give receipts only to the counterparty or arbiter, and do not reuse input keys.
* **One payment, many memos.** The sender can make receipts with different memos for
  the same output. Give every order its own labelled address (BIP-352 labels), so the
  output itself names the order. Arbiters should accept each outpoint for one order only.
* **Recompute everything.** A verifier that takes `A` or prevouts from the receipt can
  be shown outputs the receiver's wallet will never find.
* **Confirmations.** Unconfirmed transactions can be replaced; only accept receipts
  against confirmed ones. Light verifiers need an SPV proof (future work).
* **Scan keys are shared.** Receiver proofs require the output-key signature for this
  reason.

## Test coverage in this prototype

* All BIP-352 send/receive test vectors: a receipt is made and verified for every
  sending case, and a receiver proof for every receiving case.
* All BIP-374 DLEQ test vectors.
* Attacks: reused memo, other label of the same receiver, other receiver, tampered
  share or proof, RBF replacement, hidden script path, forged share, role swap,
  missing or bad output signatures, edited funding transaction in a bundle,
  incomplete multi-party receipt, unconfirmed transaction.

## Open questions

* A PSBT field for the memo, so BIP-375 signers can produce receipts while signing.
* SPV (merkle) proofs in the bundle, for light verifiers.
* A compact binary or bech32m encoding for QR codes.
* Hardware-wallet support: the DLEQ needs the input secret, as in BIP-375.
* The same construction for BIP-47 payment codes.

## Acknowledgements

Builds directly on BIP-352 (josibake, Ruben Somsen, Sebastian Falbesoner), BIP-374 (Andrew Toth, Ruben
Somsen, Sebastian Falbesoner) and BIP-375 (Andrew Toth, Ava Chow, josibake). The idea
of a payment proof for stealth-style addresses comes from Monero and Zcash (ZIP-311).
