"""Encrypted JPEG storage without decrypted temporary files."""

from __future__ import annotations

import os
import tempfile
from io import BytesIO
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from PIL import Image, ImageOps, UnidentifiedImageError


class PhotoError(RuntimeError):
    pass


def safe_photo_name(value: str | None) -> str | None:
    if value in (None, "", "Null"):
        return None
    name = str(value)
    if Path(name).name != name or not name.lower().endswith(".jpg"):
        raise PhotoError("Ungültige Fotoreferenz")
    return name


def sanitize_jpeg(raw: bytes, *, max_bytes: int, max_dimension: int = 1600) -> bytes:
    if len(raw) > max_bytes:
        raise PhotoError("Das Foto ist zu groß")
    try:
        with Image.open(BytesIO(raw)) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
            output = BytesIO()
            image.save(output, format="JPEG", quality=85, optimize=True)
            return output.getvalue()
    except (UnidentifiedImageError, OSError) as exc:
        raise PhotoError("Das Foto ist kein gültiges Bild") from exc


class PhotoStore:
    def __init__(self, directory: Path, cipher: Fernet):
        self.directory = Path(directory)
        self.cipher = cipher

    def save(self, name: str, content: bytes) -> Path:
        name = safe_photo_name(name)
        if name is None:
            raise PhotoError("Fotoreferenz fehlt")
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / f"{name}.enc"
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=self.directory
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            with temporary.open("wb") as stream:
                stream.write(self.cipher.encrypt(content))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            try:
                target.chmod(0o600)
            except OSError:
                pass
            return target
        finally:
            temporary.unlink(missing_ok=True)

    def load(self, name: str | None) -> bytes | None:
        name = safe_photo_name(name)
        if name is None:
            return None
        path = self.directory / f"{name}.enc"
        if not path.is_file():
            return None
        try:
            return self.cipher.decrypt(path.read_bytes())
        except (OSError, InvalidToken) as exc:
            raise PhotoError("Foto ist nicht lesbar") from exc

    def delete(self, name: str | None) -> bool:
        name = safe_photo_name(name)
        if name is None:
            return False
        path = self.directory / f"{name}.enc"
        if not path.exists():
            return False
        path.unlink()
        return True
