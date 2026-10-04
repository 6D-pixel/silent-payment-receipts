"""A small in-memory chain for the browser demo.

It stores real, serialised Bitcoin transactions and checks that every input
exists, is unspent and carries a valid P2WPKH signature. It has no network,
proof of work or block headers: a block is a height counter. It offers the
same get_tx / confirmations methods as spreceipt.sources, so the receipt code
reads it exactly as it reads a Bitcoin Core node.
"""

import struct

from spreceipt.tx import OutPoint, Tx, TxIn, TxOut, is_p2wpkh, txid_to_hex

from .wallet import check_p2wpkh


class ChainError(ValueError):
    pass


class SimChain:
    def __init__(self, start_height: int = 100):
        self.tip = start_height
        self.txs: dict[str, Tx] = {}
        self.height_of: dict[str, int] = {}
        self.mempool: dict[str, Tx] = {}
        self.utxos: dict[tuple[str, int], TxOut] = {}
        self._faucet_count = 0

    # The source interface spreceipt.sources.resolve() uses.
    def get_tx(self, txid: str) -> Tx:
        tx = self.txs.get(txid) or self.mempool.get(txid)
        if tx is None:
            raise ChainError(f"transaction {txid} is not on this chain")
        return tx

    def confirmations(self, txid: str) -> int | None:
        if txid in self.height_of:
            return self.tip - self.height_of[txid] + 1
        if txid in self.mempool:
            return 0
        raise ChainError(f"transaction {txid} is not on this chain")

    def mine(self) -> int:
        self.tip += 1
        for txid, tx in self.mempool.items():
            self.txs[txid] = tx
            self.height_of[txid] = self.tip
        self.mempool.clear()
        return self.tip

    def faucet(self, script_pubkey: bytes, value: int) -> OutPoint:
        """New coins from nowhere, like a coinbase, confirmed in a new block."""
        self._faucet_count += 1
        tx = Tx(vin=[TxIn(OutPoint(bytes(32), 0xFFFFFFFF), struct.pack("<I", self._faucet_count) + b"\x51")],
                vout=[TxOut(value, script_pubkey)])
        self.mempool[tx.txid_hex] = tx
        self.utxos[(tx.txid_hex, 0)] = tx.vout[0]
        self.mine()
        return OutPoint(tx.txid, 0)

    def broadcast(self, tx: Tx) -> str:
        spent, total_in = [], 0
        for n, txin in enumerate(tx.vin):
            key = (txid_to_hex(txin.prevout.txid), txin.prevout.vout)
            if key in spent:
                raise ChainError(f"input {n} spends the same coin twice")
            coin = self.utxos.get(key)
            if coin is None:
                raise ChainError(f"input {n} spends a coin that does not exist or is already spent")
            if not is_p2wpkh(coin.script_pubkey):
                raise ChainError(f"input {n}: this demo chain only accepts P2WPKH coins")
            problem = check_p2wpkh(tx, n, coin.script_pubkey, coin.value)
            if problem:
                raise ChainError(f"input {n}: {problem}")
            spent.append(key)
            total_in += coin.value
        if sum(o.value for o in tx.vout) > total_in:
            raise ChainError("outputs are worth more than the inputs")
        for key in spent:
            del self.utxos[key]
        txid = tx.txid_hex
        self.mempool[txid] = tx
        for n, out in enumerate(tx.vout):
            self.utxos[(txid, n)] = out
        return txid
