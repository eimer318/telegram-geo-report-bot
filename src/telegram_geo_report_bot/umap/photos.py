"""Optional in-memory photo preparation for uMap popups."""

from __future__ import annotations

import base64
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError


def photo_data_uri(photo_store, photo_name: str, *, maximum: int = 300) -> str:
    raw = photo_store.load(photo_name)
    if not raw:
        raise ValueError("Ein freigegebenes Foto ist nicht verfügbar")
    try:
        with Image.open(BytesIO(raw)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((maximum, maximum), Image.Resampling.LANCZOS)
            output = BytesIO()
            image.save(output, format="JPEG", quality=50, optimize=True, exif=b"")
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError("Ein freigegebenes Foto ist nicht lesbar") from exc
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"
