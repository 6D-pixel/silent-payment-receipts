"""Command line: spreceipt keygen | address | make | prove | combine | verify."""

import argparse
import json
import sys

from . import combine, make_receiver_proof, make_sender_receipt, verify_receipt
from .bip352 import encode_sp_address, labeled_spend_key
from .crypto import G, random_scalar
from .keys import parse_private_key
from .prove import ReceiptError
from .receipt import NETWORKS, Receipt, ReceiptFormatError
from .sources import BundleSource, CoreRPCSource, EsploraSource, SourceError, make_bundle, resolve

PRIVACY_NOTE = ("Share this receipt only with the person who needs it (the shop, auditor or arbiter). "
                "It reveals your shared secret with this receiver, which can expose other payments "
                "you made to them from the same keys.")


def add_source_args(p: argparse.ArgumentParser, offline_ok: bool = False) -> None:
    g = p.add_argument_group("where to get transactions")
    g.add_argument("--rpc", metavar="URL", help="Bitcoin Core RPC, e.g. http://127.0.0.1:18443")
    g.add_argument("--rpc-cookie", metavar="FILE", help="Bitcoin Core .cookie file")
    g.add_argument("--rpc-user")
    g.add_argument("--rpc-password")
    g.add_argument("--esplora", metavar="URL", help="Esplora API base, e.g. https://mempool.space/api")
    g.add_argument("--tx-hex", help="raw transaction (offline)")
    g.add_argument("--prevout-tx", action="append", default=[], metavar="HEX",
                   help="raw funding transaction (offline, repeat for each)")
    if offline_ok:
        g.add_argument("--offline", action="store_true",
                       help="use the transactions bundled in the receipt; confirmations are NOT checked")


def get_source(args, receipt: Receipt | None = None):
    if args.rpc:
        return CoreRPCSource(args.rpc, args.rpc_user, args.rpc_password, args.rpc_cookie)
    if args.esplora:
        return EsploraSource(args.esplora)
    if args.tx_hex:
        return BundleSource({"tx": args.tx_hex, "prevout_txs": args.prevout_tx})
    if getattr(args, "offline", False):
        if not receipt or not receipt.bundle:
            raise SystemExit("error: this receipt has no bundle; use --rpc or --esplora")
        return BundleSource(receipt.bundle)
    raise SystemExit("error: give a source: --rpc, --esplora, --tx-hex" + (" or --offline" if hasattr(args, "offline") else ""))


def write_out(text: str, path: str | None) -> None:
    if path:
        with open(path, "w") as f:
            f.write(text + "\n")
        print(f"wrote {path}", file=sys.stderr)
    else:
        print(text)


def cmd_keygen(args) -> int:
    b_scan, b_spend = random_scalar(), random_scalar()
    B_scan, B_spend = b_scan * G, b_spend * G
    out = {"scan_key": b_scan.to_bytes().hex(), "spend_key": b_spend.to_bytes().hex(),
           "address": encode_sp_address(B_scan, B_spend, args.network)}
    if args.label:
        out["labeled_addresses"] = {str(m): encode_sp_address(B_scan, labeled_spend_key(b_scan, B_spend, m), args.network)
                                    for m in args.label}
    print(json.dumps(out, indent=2))
    return 0


def cmd_address(args) -> int:
    b_scan, b_spend = parse_private_key(args.scan_key), parse_private_key(args.spend_key)
    print(encode_sp_address(b_scan * G, labeled_spend_key(b_scan, b_spend * G, args.label), args.network))
    return 0


def cmd_make(args) -> int:
    source = get_source(args)
    tx, spks, _ = resolve(source, args.txid)
    bundle = None if args.no_bundle else make_bundle(source, args.txid)
    key_texts = list(args.key)
    if args.key_file:
        with open(args.key_file) as f:
            key_texts += [line for line in f.read().split() if line]
    if not key_texts:
        raise SystemExit("error: give at least one --key or --key-file")
    keys = [parse_private_key(k) for k in key_texts]
    receipt = make_sender_receipt(tx, spks, args.address, args.memo, keys, args.network, bundle)
    write_out(receipt.to_json(), args.output)
    print(PRIVACY_NOTE, file=sys.stderr)
    return 0


def cmd_prove(args) -> int:
    source = get_source(args)
    tx, spks, _ = resolve(source, args.txid)
    bundle = None if args.no_bundle else make_bundle(source, args.txid)
    proof = make_receiver_proof(tx, spks, parse_private_key(args.scan_key), parse_private_key(args.spend_key),
                                args.label, args.memo, args.network, bundle)
    write_out(proof.to_json(), args.output)
    return 0


def cmd_combine(args) -> int:
    receipts = [Receipt.from_json(open(p).read()) for p in args.files]
    merged = combine(receipts)
    if merged.bundle:
        tx, spks, _ = resolve(BundleSource(merged.bundle), merged.txid)
        merged = combine(receipts, tx, spks)
    write_out(merged.to_json(), args.output)
    return 0


def cmd_verify(args) -> int:
    receipt = Receipt.from_json(open(args.file).read())
    source = get_source(args, receipt)
    tx, spks, confirmations = resolve(source, receipt.txid)
    result = verify_receipt(receipt, tx, spks, confirmations, args.min_conf)
    print(result.summary())
    return 0 if result.valid else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="spreceipt", description="Receipts for BIP-352 silent payments (prototype).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("keygen", help="new silent-payment receiver keys (for testing)")
    p.add_argument("--network", choices=NETWORKS, default="regtest")
    p.add_argument("--label", type=int, action="append", help="also print this labelled address (repeat)")
    p.set_defaults(fn=cmd_keygen)

    p = sub.add_parser("address", help="silent-payment address for keys, optionally labelled")
    p.add_argument("--scan-key", required=True)
    p.add_argument("--spend-key", required=True)
    p.add_argument("--label", type=int)
    p.add_argument("--network", choices=NETWORKS, default="regtest")
    p.set_defaults(fn=cmd_address)

    p = sub.add_parser("make", help="sender: make a receipt for a payment you made")
    p.add_argument("--txid", required=True)
    p.add_argument("--address", required=True, help="the silent-payment address you paid")
    p.add_argument("--key", action="append", default=[], help="private key of an input you spent (hex or WIF, repeat)")
    p.add_argument("--key-file", help="file with one private key per line (safer than --key: stays out of shell history)")
    p.add_argument("--memo", required=True, help="order or invoice id this payment is for")
    p.add_argument("--network", choices=NETWORKS, default="regtest")
    p.add_argument("--no-bundle", action="store_true", help="leave the raw transactions out")
    p.add_argument("-o", "--output")
    add_source_args(p)
    p.set_defaults(fn=cmd_make)

    p = sub.add_parser("prove", help="receiver: prove outputs of a transaction are yours")
    p.add_argument("--txid", required=True)
    p.add_argument("--scan-key", required=True)
    p.add_argument("--spend-key", required=True)
    p.add_argument("--label", type=int)
    p.add_argument("--memo", required=True)
    p.add_argument("--network", choices=NETWORKS, default="regtest")
    p.add_argument("--no-bundle", action="store_true")
    p.add_argument("-o", "--output")
    add_source_args(p)
    p.set_defaults(fn=cmd_prove)

    p = sub.add_parser("combine", help="merge partial sender receipts from several parties")
    p.add_argument("files", nargs="+")
    p.add_argument("-o", "--output")
    p.set_defaults(fn=cmd_combine)

    p = sub.add_parser("verify", help="check a receipt")
    p.add_argument("file")
    p.add_argument("--min-conf", type=int, default=1)
    add_source_args(p, offline_ok=True)
    p.set_defaults(fn=cmd_verify)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except (ReceiptError, ReceiptFormatError, SourceError, ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
