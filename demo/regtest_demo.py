#!/usr/bin/env python3
"""End-to-end demo on a private regtest chain with Bitcoin Core.

Story: Alice buys a phone (Order #1001) from a shop that takes silent payments.
The shop says it never got paid. Alice sends a receipt to the marketplace,
which checks it against the blockchain.

    BITCOIND=/path/to/bitcoind python3 demo/regtest_demo.py [--save-samples]

Needs Bitcoin Core (tested with 31.1). Everything runs in a temporary data dir.
"""

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from spreceipt.bip352 import (  # noqa: E402
    decode_sp_address, encode_p2tr_address, encode_p2wpkh_address, scan, sender_output_keys,
)
from spreceipt.crypto import G, Scalar, random_scalar, tagged_hash  # noqa: E402
from spreceipt.keys import encode_wif  # noqa: E402
from spreceipt.sources import CoreRPCSource, resolve  # noqa: E402
from spreceipt.tx import OutPoint, Tx, ser_bytes, txid_to_bytes  # noqa: E402

NET = "regtest"
RPC_PORT = 18543


def say(step: str) -> None:
    print(f"\n=== {step}")


def btc(sat: int) -> str:
    return f"{sat / 1e8:.8f}"


class Node:
    def __init__(self, bitcoind: str):
        self.dir = tempfile.mkdtemp(prefix="spreceipt-regtest-")
        self.proc = subprocess.Popen(
            [bitcoind, "-regtest", f"-datadir={self.dir}", "-txindex=1", "-fallbackfee=0.0002",
             f"-rpcport={RPC_PORT}", "-listen=0", "-server=1", "-printtoconsole=0"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.url = f"http://127.0.0.1:{RPC_PORT}"
        self.cookie = os.path.join(self.dir, "regtest", ".cookie")
        for _ in range(120):
            if os.path.exists(self.cookie):
                try:
                    self.rpc = CoreRPCSource(self.url, cookie_file=self.cookie)
                    self.rpc.call("getblockchaininfo")
                    break
                except Exception:
                    pass
            time.sleep(0.5)
        else:
            raise SystemExit("bitcoind did not start")

    def call(self, method, *params):
        return self.rpc.call(method, *params)

    def wallet(self, name):
        return CoreRPCSource(f"{self.url}/wallet/{name}", cookie_file=self.cookie)

    def stop(self):
        try:
            self.call("stop")
            self.proc.wait(timeout=30)
        finally:
            shutil.rmtree(self.dir, ignore_errors=True)


def cli(*args, node: Node, check=True) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "spreceipt", *args]
    shown = " ".join(a if " " not in a else repr(a) for a in args)
    print(f"$ spreceipt {shown}")
    res = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if res.stdout.strip() and args[0] not in ("make", "prove"):
        print(res.stdout.rstrip())
    if res.stderr.strip():
        print(res.stderr.rstrip())
    if check and res.returncode != 0:
        raise SystemExit(f"command failed ({res.returncode})")
    return res


def find_vout(node: Node, txid: str, spk: bytes) -> tuple[int, int]:
    tx = Tx.from_hex(node.call("getrawtransaction", txid))
    for n, o in enumerate(tx.vout):
        if o.script_pubkey == spk:
            return n, o.value
    raise RuntimeError("output not found")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save-samples", action="store_true", help="write the receipts to demo/samples/")
    args = ap.parse_args()
    bitcoind = os.environ.get("BITCOIND") or shutil.which("bitcoind")
    if not bitcoind:
        raise SystemExit("Set BITCOIND to your bitcoind binary (Bitcoin Core 28 or newer).")

    work = pathlib.Path(tempfile.mkdtemp(prefix="spreceipt-demo-"))
    node = Node(bitcoind)
    rpc = ["--rpc", node.url, "--rpc-cookie", node.cookie]
    try:
        say("Start a private regtest chain and mine some coins")
        node.call("createwallet", "miner")
        miner = node.wallet("miner")
        mine_to = miner.call("getnewaddress")
        node.call("generatetoaddress", 101, mine_to)

        say("The shop makes silent-payment keys and one labelled address for Order #1001")
        keys = json.loads(cli("keygen", "--label", "1001", node=node).stdout)
        shop_scan = Scalar.from_bytes_checked(bytes.fromhex(keys["scan_key"]))
        shop_spend = Scalar.from_bytes_checked(bytes.fromhex(keys["spend_key"]))
        order_address = keys["labeled_addresses"]["1001"]

        say("Alice has two coins, at addresses only she holds the keys for")
        alice = [random_scalar(), random_scalar()]
        utxos = []
        for d, amount in zip(alice, ("0.25", "0.20")):
            addr = encode_p2wpkh_address(d * G, NET)
            txid = miner.call("sendtoaddress", addr, amount)
            utxos.append((txid, addr, d))
        node.call("generatetoaddress", 1, mine_to)
        inputs, prevtxs, total = [], [], 0
        for txid, addr, d in utxos:
            info = node.call("validateaddress", addr)
            spk = bytes.fromhex(info["scriptPubKey"])
            vout, value = find_vout(node, txid, spk)
            inputs.append({"txid": txid, "vout": vout})
            prevtxs.append({"txid": txid, "vout": vout, "scriptPubKey": spk.hex(), "amount": btc(value)})
            total += value
            print(f"  coin {txid}:{vout}  {btc(value)} BTC")

        say("Alice pays 0.30 BTC to the shop's silent-payment address")
        _, B_scan, B_m = decode_sp_address(order_address)
        outpoints = [OutPoint(txid_to_bytes(i["txid"]), i["vout"]) for i in inputs]
        sp_output = sender_output_keys([(d, False) for d in alice], outpoints, [(B_scan, B_m)])[0]
        pay_sat, fee_sat = 30_000_000, 20_000
        change_addr = encode_p2wpkh_address(alice[0] * G, NET)
        raw = node.call("createrawtransaction", inputs, [
            {encode_p2tr_address(sp_output, NET): btc(pay_sat)},
            {change_addr: btc(total - pay_sat - fee_sat)},
        ])
        signed = node.call("signrawtransactionwithkey", raw, [encode_wif(d, NET) for d in alice], prevtxs)
        assert signed["complete"], signed
        pay_txid = node.call("sendrawtransaction", signed["hex"])
        node.call("generatetoaddress", 1, mine_to)
        print(f"  payment txid {pay_txid} (confirmed in a block)")
        print("  On the blockchain the shop's output is just a random-looking taproot key.")

        say("Sanity check: the shop's own silent-payment wallet scan finds the money")
        tx, spks, _ = resolve(node.rpc, pay_txid)
        found = scan(shop_scan, shop_spend * G, [1001], tx, spks)
        for m in found:
            print(f"  shop wallet: output {m.vout} = {btc(tx.vout[m.vout].value)} BTC (label {m.label})")

        say("The shop says: 'you never paid'. Alice makes a receipt for Order #1001")
        receipt_file = work / "receipt.json"
        cli("make", "--txid", pay_txid, "--address", order_address,
            "--key", encode_wif(alice[0], NET), "--key", encode_wif(alice[1], NET),
            "--memo", "Order #1001", "--network", NET, *rpc, "-o", str(receipt_file), node=node)
        receipt = json.loads(receipt_file.read_text())
        core = {k: receipt[k] for k in ("spreceipt", "role", "network", "txid", "address", "memo", "shares")}
        print(f"  receipt: {len(json.dumps(core, separators=(',', ':')))} bytes of JSON without the bundle; "
              f"the proof itself is a 33-byte share + a 64-byte BIP-374 proof")

        say("The marketplace checks the receipt against its own node")
        cli("verify", str(receipt_file), *rpc, node=node)

        say("Or offline, from the transactions bundled in the receipt")
        cli("verify", str(receipt_file), "--offline", node=node)

        say("Cheat 1: reuse the same payment for another order")
        bad = dict(receipt, memo="Order #2002")
        bad_file = work / "reused.json"
        bad_file.write_text(json.dumps(bad))
        assert cli("verify", str(bad_file), *rpc, node=node, check=False).returncode == 1

        say("Cheat 2: a fake payment with a hidden way to take the money back")
        # Alice pays an output that a bare 'P = B_m + t*G' check accepts, but that
        # also has a taproot script path spendable by her own key.
        while True:
            r = random_scalar()
            Q = B_m + r * G
            if Q.has_even_y():
                break
        leaf = tagged_hash("TapLeaf", b"\xc0" + ser_bytes(b"\x20" + (alice[0] * G).to_bytes_xonly() + b"\xac"))
        h = Scalar.from_bytes_checked(tagged_hash("TapTweak", Q.to_bytes_xonly() + leaf))
        P = Q + h * G
        change_vout, change_value = find_vout(node, pay_txid, bytes.fromhex(node.call("validateaddress", change_addr)["scriptPubKey"]))
        fake_sat = 10_000_000
        raw = node.call("createrawtransaction", [{"txid": pay_txid, "vout": change_vout}], [
            {encode_p2tr_address(P.to_bytes_xonly(), NET): btc(fake_sat)},
            {change_addr: btc(change_value - fake_sat - fee_sat)},
        ])
        spk = node.call("validateaddress", change_addr)["scriptPubKey"]
        signed = node.call("signrawtransactionwithkey", raw, [encode_wif(alice[0], NET)],
                           [{"txid": pay_txid, "vout": change_vout, "scriptPubKey": spk, "amount": btc(change_value)}])
        fake_txid = node.call("sendrawtransaction", signed["hex"])
        node.call("generatetoaddress", 1, mine_to)
        tweak = r + h
        bare_ok = (B_m + tweak * G).to_bytes_xonly() == P.to_bytes_xonly()
        print(f"  fake txid {fake_txid}")
        print(f"  bare tweak check (BIP-352 out-of-band notice): {'PASS  <- fooled' if bare_ok else 'fail'}")
        tx, spks, _ = resolve(node.rpc, fake_txid)
        print(f"  shop wallet scan finds it: {bool(scan(shop_scan, shop_spend * G, [1001], tx, spks))}")
        res = cli("make", "--txid", fake_txid, "--address", order_address, "--key", encode_wif(alice[0], NET),
                  "--memo", "Order #1001", "--network", NET, *rpc, "-o", str(work / "fake.json"), node=node, check=False)
        assert res.returncode != 0
        print("  -> no receipt can be made for the fake payment")

        say("The other direction: the shop proves to its accountant that the payment is its income")
        proof_file = work / "income-proof.json"
        cli("prove", "--txid", pay_txid, "--scan-key", keys["scan_key"], "--spend-key", keys["spend_key"],
            "--label", "1001", "--memo", "Income statement 2026", "--network", NET, *rpc,
            "-o", str(proof_file), node=node)
        cli("verify", str(proof_file), *rpc, node=node)

        if args.save_samples:
            out = ROOT / "demo" / "samples"
            out.mkdir(exist_ok=True)
            shutil.copy(receipt_file, out / "sender-receipt.json")
            shutil.copy(proof_file, out / "receiver-proof.json")
            print(f"\nSaved sample receipts to {out}")
        say("Done: every honest claim verified and every cheat was rejected")
    finally:
        node.stop()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
