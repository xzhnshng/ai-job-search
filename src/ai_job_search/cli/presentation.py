from __future__ import annotations

import json

from ai_job_search.contracts.envelope import Envelope


def render(envelope: Envelope, *, as_json: bool) -> str:
    payload = envelope.to_dict()
    if as_json:
        return json.dumps(payload, sort_keys=True)
    if envelope.error:
        return f"Error [{envelope.error.code}]: {envelope.error.message}"
    lines = [envelope.command]
    if envelope.data:
        lines.extend(f"{key}: {value}" for key, value in envelope.data.items())
    for warning in envelope.warnings:
        lines.append(f"warning: {warning}")
    return "\n".join(lines)
