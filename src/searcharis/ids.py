import hashlib
import json
from collections.abc import Mapping
from typing import Any


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def incident_fingerprint(
    repository: str,
    target_origin: str,
    affected_url: str,
    finding_code: str,
) -> str:
    return _canonical_hash(
        {
            "repository": repository,
            "target_origin": target_origin,
            "affected_url": affected_url,
            "finding_code": finding_code,
        }
    )


def action_key(kind: str, incident_id: str, evidence_hash: str) -> str:
    return _canonical_hash(
        {
            "kind": kind,
            "incident_id": incident_id,
            "evidence_hash": evidence_hash,
        }
    )
