"""Authenticated approval signatures for the live-session gate.

The approval artifact binds a human decision to one reviewed handoff by hash and
expiry. Hash binding alone is *integrity* evidence: it detects a bundle whose
parts stop agreeing, which covers accidental corruption, a stale replay, and a
non-collusive edit. It is not *authenticity* evidence. An adversary who can write
to the artifact store can rewrite the handoff, recompute its digest, rewrite the
approval that points at it, and append a matching journal entry, because every
one of those values is an unkeyed function of data the adversary controls.

This module closes that gap with an Ed25519 detached signature over the
security-relevant approval fields. The private key lives outside the artifact
store (a file the approver controls, or an HSM/agent in a real deployment), so
rewriting the artifact is no longer sufficient: the attacker must also produce a
signature that verifies under a public key the verifier already trusts.

Two properties matter for the claim this supports:

* ``verify_approval_signature`` requires the caller to supply the trusted public
  keys. It deliberately does **not** trust the key embedded in the artifact --
  that would let an adversary re-sign with a key of their own and verify
  successfully, which is the usual way this construction is gotten wrong.
* The signed payload is a canonical serialization of an explicit field list, so
  the signature commits to the approver, the bound request hash, the expiry, and
  the notional/symbol limits together. Changing any of them invalidates it.

The resulting claim is bounded and checkable: the pipeline is forgery-resistant
against an adversary who does not hold the approver's private key.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SIGNATURE_FIELD = "approval_signature"
SIGNATURE_ALGORITHM = "ed25519"

#: Fields the signature commits to. Ordered for readability only; the canonical
#: payload sorts keys, so this list defines coverage, not byte order.
SIGNED_FIELDS: tuple[str, ...] = (
    "approval_id",
    "approval_status",
    "approved_by",
    "approved_at",
    "expires_at",
    "request_artifact_hash",
    "account_mode",
    "max_notional",
    "max_quantity",
    "allowed_symbols",
    "allowed_order_types",
    "approval_reason",
)


class ApprovalSigningError(RuntimeError):
    """Raised when a signature cannot be produced or a key cannot be read."""


def _require_backend() -> Any:
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError as exc:  # pragma: no cover - exercised only without the dep
        raise ApprovalSigningError(
            "Ed25519 approval signing requires the 'cryptography' package"
        ) from exc
    return ed25519


def canonical_signing_payload(artifact: dict[str, Any]) -> bytes:
    """Serialize the signed subset of an approval artifact deterministically.

    Missing fields are represented as ``null`` rather than dropped, so an
    adversary cannot strip a field to change the signed meaning of the artifact.
    """

    payload = {field: artifact.get(field) for field in SIGNED_FIELDS}
    payload["_signed_fields"] = list(SIGNED_FIELDS)
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")


def generate_approver_keypair(private_key_path: Path | str) -> str:
    """Create an Ed25519 approver key and return its public key hex.

    The private key is written with owner-only permissions where the platform
    supports it. It must live outside the artifact store it authorizes.
    """

    ed25519 = _require_backend()
    path = Path(private_key_path)
    if path.exists():
        raise ApprovalSigningError(f"refusing to overwrite an existing key: {path}")
    private_key = ed25519.Ed25519PrivateKey.generate()
    raw = private_key.private_bytes_raw()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    try:
        path.chmod(0o600)
    except (OSError, NotImplementedError):  # pragma: no cover - platform dependent
        pass
    return private_key.public_key().public_bytes_raw().hex()


def load_private_key(private_key_path: Path | str) -> Any:
    ed25519 = _require_backend()
    path = Path(private_key_path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ApprovalSigningError(f"cannot read approver key: {path}") from exc
    if len(raw) != 32:
        raise ApprovalSigningError(
            f"approver key must be 32 raw Ed25519 bytes; {path} has {len(raw)}"
        )
    return ed25519.Ed25519PrivateKey.from_private_bytes(raw)


def public_key_hex(private_key_path: Path | str) -> str:
    return load_private_key(private_key_path).public_key().public_bytes_raw().hex()


def sign_approval_artifact(
    artifact: dict[str, Any], private_key_path: Path | str
) -> dict[str, Any]:
    """Return a copy of ``artifact`` carrying a detached Ed25519 signature."""

    private_key = load_private_key(private_key_path)
    signed = {key: value for key, value in artifact.items() if key != SIGNATURE_FIELD}
    signature = private_key.sign(canonical_signing_payload(signed))
    signed[SIGNATURE_FIELD] = {
        "algorithm": SIGNATURE_ALGORITHM,
        "signed_fields": list(SIGNED_FIELDS),
        "public_key": private_key.public_key().public_bytes_raw().hex(),
        "signature": signature.hex(),
    }
    return signed


def verify_approval_signature(
    artifact: dict[str, Any], trusted_public_keys: list[str] | tuple[str, ...] | set[str]
) -> list[str]:
    """Verify an approval signature against an explicitly trusted key set.

    ``trusted_public_keys`` holds hex-encoded Ed25519 public keys the verifier
    already trusts, obtained out of band. The key recorded inside the artifact is
    only used to select among them: an artifact signed by an untrusted key fails
    even though its own signature is internally consistent, which is the whole
    point of the check.

    Returns a list of error strings; empty means the approval is authentic.
    """

    trusted = {key.strip().lower() for key in trusted_public_keys if isinstance(key, str)}
    if not trusted:
        return ["no trusted approver public keys were supplied"]

    block = artifact.get(SIGNATURE_FIELD)
    if not isinstance(block, dict):
        return ["approval artifact carries no signature block"]

    algorithm = str(block.get("algorithm", ""))
    if algorithm != SIGNATURE_ALGORITHM:
        return [f"unsupported approval signature algorithm: {algorithm or '(missing)'}"]

    declared_fields = block.get("signed_fields")
    if list(declared_fields or []) != list(SIGNED_FIELDS):
        return ["approval signature does not cover the required field set"]

    recorded_key = str(block.get("public_key", "")).strip().lower()
    if not recorded_key:
        return ["approval signature block records no public key"]
    if recorded_key not in trusted:
        return ["approval was signed by a key that is not in the trusted approver set"]

    signature_hex = str(block.get("signature", ""))
    try:
        signature = bytes.fromhex(signature_hex)
    except ValueError:
        return ["approval signature is not valid hex"]

    ed25519 = _require_backend()
    try:
        public_key = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(recorded_key))
    except ValueError:
        return ["approval signature block records an unusable public key"]

    payload = canonical_signing_payload(
        {key: value for key, value in artifact.items() if key != SIGNATURE_FIELD}
    )
    try:
        public_key.verify(signature, payload)
    except Exception:  # cryptography raises InvalidSignature
        return ["approval signature does not verify over the signed fields"]
    return []


def verify_approval_artifact_file(
    path: Path | str, trusted_public_keys: list[str] | tuple[str, ...] | set[str]
) -> list[str]:
    """Read an approval artifact from disk and verify its signature."""

    file_path = Path(path)
    try:
        artifact = json.loads(file_path.read_text(encoding="utf-8"))
    except OSError:
        return [f"cannot read approval artifact: {file_path}"]
    except json.JSONDecodeError as exc:
        return [f"approval artifact is not valid JSON: {exc}"]
    if not isinstance(artifact, dict):
        return ["approval artifact is not a JSON object"]
    return verify_approval_signature(artifact, trusted_public_keys)
