from __future__ import annotations

import base64
import hashlib
import io

from . import providers


def resolve_openai_file_upload(
    *,
    data: str,
    media_type: str,
    filename: str,
    api_key: str | None,
    file_id_cache: dict[str, str],
    file_id_reverse: dict[str, str],
) -> str:
    key = hashlib.sha256(data.encode()).hexdigest()
    if key in file_id_cache:
        return file_id_cache[key]

    raw = base64.b64decode(data)
    client = providers.OpenAI(api_key=api_key)
    response = client.files.create(
        file=(filename, io.BytesIO(raw), media_type),
        purpose="user_data",
    )
    file_id_cache[key] = response.id
    file_id_reverse[response.id] = key
    return response.id


__all__ = ["resolve_openai_file_upload"]
