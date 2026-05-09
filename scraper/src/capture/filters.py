from __future__ import annotations


def should_keep_payload(payload_text: str, ignore_resources: list[str]) -> bool:
    """Return False if the raw frame payload mentions any ignored resource.

    This is a coarse, capture-time substring filter. Anything more precise
    requires JSON-decoding every frame, which the prototype skipped for cost
    reasons; we follow that.
    """
    if not ignore_resources:
        return True
    for pattern in ignore_resources:
        if pattern and pattern in payload_text:
            return False
    return True
