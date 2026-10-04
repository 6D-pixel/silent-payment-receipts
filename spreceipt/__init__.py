"""Silent Payment Receipts: prove that a BIP-352 silent payment was made.

A prototype. Pure-Python cryptography, not constant time: do not use it with real money.
"""

from .prove import ReceiptError, combine, make_receiver_proof, make_sender_receipt
from .receipt import Receipt, ReceiptFormatError
from .verify import VerifyResult, verify_receipt

__all__ = [
    "Receipt", "ReceiptError", "ReceiptFormatError", "VerifyResult",
    "combine", "make_receiver_proof", "make_sender_receipt", "verify_receipt",
]
__version__ = "0.1.0"
