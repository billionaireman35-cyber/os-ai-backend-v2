"""
One-time script to generate a standalone treasury wallet (not tied to any
user account) for staking yield payouts. Run this once, save the printed
address and encrypted key into Render's environment variables, then DELETE
this script and never commit it or its output to git.

Usage: python3 generate_treasury_wallet.py
You will be prompted for a password to encrypt the private key with -
choose a strong one and store it securely (e.g. a password manager) -
you'll need it for the backend to actually sign payout transactions.
"""
import getpass
from eth_utils import keccak, to_checksum_address
from ecdsa import SigningKey, SECP256k1
from app.services.wallet_service import _encrypt_private_key

def main():
    password = getpass.getpass("Enter a password to encrypt the treasury wallet's private key: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords did not match. Aborting.")
        return
    if len(password) < 12:
        print("Use a longer password (12+ chars) - this protects real treasury funds.")
        return

    sk = SigningKey.generate(curve=SECP256k1)
    private_key_hex = sk.to_string().hex()
    public_key_bytes = sk.get_verifying_key().to_string()
    address = to_checksum_address("0x" + keccak(public_key_bytes).hex()[-40:])
    encrypted_key = _encrypt_private_key(private_key_hex, password)

    print("\n" + "=" * 70)
    print("TREASURY WALLET GENERATED")
    print("=" * 70)
    print(f"\nAddress (fund this with CLOSE for staking yield payouts):\n{address}")
    print(f"\nEncrypted private key (save as STAKING_TREASURY_ENCRYPTED_KEY in Render):\n{encrypted_key}")
    print(f"\nPassword (save as STAKING_TREASURY_PASSWORD in Render, and in a password manager):\n{password}")
    print("\n" + "=" * 70)
    print("IMPORTANT: Delete this script now. Never commit this output to git.")
    print("=" * 70)

if __name__ == "__main__":
    main()
