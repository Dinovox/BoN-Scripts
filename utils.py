"""
Helpers partagés pour interagir avec le réseau BoN (MultiversX).
"""
import base64
import sys
from pathlib import Path

from multiversx_sdk import ProxyNetworkProvider, UserSigner, Address

import config


def get_provider(gateway_url: str = config.GATEWAY_URL) -> ProxyNetworkProvider:
    return ProxyNetworkProvider(gateway_url)


def load_signer(pem_path: str) -> UserSigner:
    path = Path(pem_path).expanduser()
    if not path.exists():
        print(f"[ERROR] Wallet PEM introuvable : {path}", file=sys.stderr)
        sys.exit(1)
    return UserSigner.from_pem_file(path)


def get_address(signer: UserSigner) -> Address:
    return signer.get_pubkey().to_address(hrp="erd")


def get_account_nonce(provider: ProxyNetworkProvider, address: Address) -> int:
    account = provider.get_account(address)
    return account.nonce


# ---------------------------------------------------------------------------
# Encodage des arguments pour les calls de smart contracts
# ---------------------------------------------------------------------------

def encode_biguint(n: int) -> str:
    """Encode un entier en hex minimal pour BigUint MultiversX (0 → '', 4 → '04')."""
    if n == 0:
        return ""
    hex_str = hex(n)[2:]
    if len(hex_str) % 2:
        hex_str = "0" + hex_str
    return hex_str


def encode_u64(n: int) -> str:
    """Encode un entier en hex 8 octets big-endian pour u64 MultiversX."""
    return n.to_bytes(8, "big").hex()


def encode_address(bech32: str) -> str:
    """Encode une adresse bech32 en hex 32 octets."""
    return Address.new_from_bech32(bech32).to_hex()


def build_call_data(function: str, *args: str) -> str:
    """
    Construit la data field d'un call SC.
    Chaque arg est une chaîne hex (déjà encodée).
    Exemple : build_call_data("vote", "04", "00") → "vote@04@00"
    """
    parts = [function] + [a for a in args if a != ""]
    # Pour un arg BigUint 0 (chaîne vide), on garde le séparateur @
    all_parts = [function] + list(args)
    return "@".join(all_parts)


# ---------------------------------------------------------------------------
# Décodage des résultats de vmQuery
# ---------------------------------------------------------------------------

def decode_return_value(raw) -> dict:
    """
    Décode une valeur de retour vmQuery.
    raw peut être bytes ou une chaîne base64.
    Retourne : {hex, int (si possible), str (si printable)}
    """
    if isinstance(raw, str):
        try:
            data = base64.b64decode(raw)
        except Exception:
            data = raw.encode()
    else:
        data = bytes(raw)

    result = {"hex": data.hex(), "bytes": len(data)}
    try:
        result["int"] = int.from_bytes(data, "big")
    except Exception:
        pass
    try:
        decoded = data.decode("utf-8")
        if decoded.isprintable():
            result["str"] = decoded
    except Exception:
        pass
    return result
