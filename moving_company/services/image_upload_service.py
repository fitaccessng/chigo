import os
import secrets
from pathlib import Path
from uuid import uuid4

from flask import current_app
from werkzeug.datastructures import FileStorage

SUPPORTED_IMAGE_TYPES = {
    'image/jpeg': '.jpg',
    'image/png': '.png',
    'image/webp': '.webp',
}
SUPPORTED_EXTENSIONS = frozenset(SUPPORTED_IMAGE_TYPES.values())
IMAGE_SIGNATURES = {
    '.jpg': (b'\xff\xd8\xff',),
    '.png': (b'\x89PNG\r\n\x1a\n',),
    '.webp': (b'RIFF', b'WEBP'),
}


def validate_image_file(file_storage: FileStorage, max_size: int | None = None) -> tuple[str, str, int]:
    if not file_storage or not file_storage.filename:
        raise ValueError('Choose an image to upload.')
    filename = os.path.basename(file_storage.filename).strip()
    if not filename or filename in {'.', '..'}:
        raise ValueError('The selected filename is invalid.')
    mime_type = file_storage.content_type or ''
    extension = Path(filename).suffix.casefold()
    if mime_type not in SUPPORTED_IMAGE_TYPES or extension not in SUPPORTED_EXTENSIONS:
        raise ValueError('Only JPG, PNG, and WebP images are supported.')
    data = file_storage.stream.read()
    if not data:
        raise ValueError('The selected image is empty.')
    if max_size is not None and len(data) > max_size:
        raise ValueError(f'Images must be no larger than {max_size // (1024 * 1024)} MB.')
    if not image_matches_signature(data, extension):
        raise ValueError('The selected file does not contain a valid supported image.')
    file_storage.stream.seek(0)
    return filename, mime_type, len(data)


def image_matches_signature(data: bytes, extension: str) -> bool:
    signatures = IMAGE_SIGNATURES[extension]
    if extension == '.jpg':
        return data.startswith(signatures[0])
    if extension == '.png':
        return data.startswith(signatures[0])
    return data.startswith(signatures[0]) and signatures[1] in data[:12]


def save_image(file_storage: FileStorage, subdirectory: str, max_size: int | None = None) -> dict:
    filename, mime_type, file_size = validate_image_file(file_storage, max_size)
    extension = Path(filename).suffix.casefold()
    safe_name = f'{uuid4().hex}_{secrets.token_hex(8)}{extension}'
    destination = Path(current_app.config['UPLOAD_FOLDER']) / subdirectory
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / safe_name
    file_storage.save(str(path))
    relative_path = f'{subdirectory}/{safe_name}'
    return {
        'file_url': relative_path,
        'original_filename': filename,
        'mime_type': mime_type,
        'file_size': file_size,
        'storage_path': str(path),
    }


def delete_image(storage_path: str | None, file_url: str | None = None) -> None:
    if not storage_path and not file_url:
        return
    upload_root = Path(current_app.config['UPLOAD_FOLDER']).resolve()
    if storage_path:
        target = Path(storage_path)
        if not target.is_absolute():
            target = upload_root / target
    else:
        target = upload_root / file_url
    try:
        target.resolve().relative_to(upload_root)
    except (ValueError, OSError):
        return
    if target.is_file():
        target.unlink()
