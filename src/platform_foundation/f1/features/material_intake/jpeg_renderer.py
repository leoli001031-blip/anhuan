"""Isolated JPEG decode and EXIF orientation with reproducible bounded output."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys

PILLOW_VERSION = '12.2.0'
RENDERER_VERSION = 'jpeg-orient-rgb-pillow-12.2.0-1'
MAX_SOURCE_BYTES = 20 * 1024 * 1024
MAX_SOURCE_PIXELS = 40_000_000
MAX_SOURCE_EDGE = 10_000
MAX_PIXELS = 8_000_000
MAX_EDGE = 4096
MAX_IMAGE_BYTES = 6 * 1024 * 1024
TIMEOUT_SECONDS = 20.0


class JpegRenderError(ValueError):
    pass


@dataclass(frozen=True, slots=True, repr=False)
class RenderedJpeg:
    source_width: int
    source_height: int
    width: int
    height: int
    exif_orientation: int
    source_sha256: str
    pixel_sha256: str
    image_sha256: str
    image: bytes = field(repr=False)
    renderer_version: str = RENDERER_VERSION

    def __repr__(self):
        return f'RenderedJpeg(width={self.width}, height={self.height}, image=<redacted>)'


def render_jpeg(source: bytes, *, expected_sha256: str, timeout=TIMEOUT_SECONDS):
    if not isinstance(source, bytes) or not 4 <= len(source) <= MAX_SOURCE_BYTES:
        raise JpegRenderError('JPEG_SOURCE_BUDGET_EXCEEDED')
    source_sha = hashlib.sha256(source).hexdigest()
    if source_sha != expected_sha256:
        raise JpegRenderError('JPEG_SOURCE_SHA_MISMATCH')
    if type(timeout) not in {int, float} or not math.isfinite(timeout) or timeout <= 0:
        raise JpegRenderError('JPEG_RENDER_TIMEOUT')
    process = None
    try:
        process = subprocess.Popen([sys.executable, '-I', str(Path(__file__).resolve())],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env={'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'}, close_fds=True, start_new_session=True)
        output, _ = process.communicate(source, timeout=min(timeout, TIMEOUT_SECONDS))
    except subprocess.TimeoutExpired:
        raise JpegRenderError('JPEG_RENDER_TIMEOUT') from None
    except OSError:
        raise JpegRenderError('JPEG_RENDER_UNAVAILABLE') from None
    finally:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
        if process is not None:
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()
    if process.returncode != 0 or len(output) > MAX_IMAGE_BYTES + 2048:
        raise JpegRenderError('JPEG_RENDER_UNAVAILABLE')
    try:
        header, image = output.split(b'\n', 1)
        info = json.loads(header)
        if info.get('error') in {'JPEG_SOURCE_INVALID', 'JPEG_PIXEL_LIMIT', 'JPEG_ORIENTATION_INVALID', 'JPEG_COLOR_PROFILE_UNRESOLVED', 'JPEG_OUTPUT_LIMIT'}:
            raise JpegRenderError(info['error'])
        sw, sh, w, h, orientation = (info[key] for key in ('source_width', 'source_height', 'width', 'height', 'exif_orientation'))
        if any(type(n) is not int for n in (sw, sh, w, h, orientation)) or not 1 <= sw <= MAX_SOURCE_EDGE or not 1 <= sh <= MAX_SOURCE_EDGE or sw * sh > MAX_SOURCE_PIXELS or not 1 <= w <= MAX_EDGE or not 1 <= h <= MAX_EDGE or w * h > MAX_PIXELS or not 1 <= orientation <= 8:
            raise ValueError
        if info['source_sha256'] != source_sha or re.fullmatch(r'[0-9a-f]{64}', info['pixel_sha256']) is None or not 4 <= len(image) <= MAX_IMAGE_BYTES or not image.startswith(b'\xff\xd8\xff') or not image.endswith(b'\xff\xd9'):
            raise ValueError
        return RenderedJpeg(sw, sh, w, h, orientation, source_sha, info['pixel_sha256'], hashlib.sha256(image).hexdigest(), image)
    except JpegRenderError:
        raise
    except (ValueError, KeyError, TypeError):
        raise JpegRenderError('JPEG_RENDER_UNAVAILABLE') from None


def _decode(source):
    import warnings
    from importlib.metadata import version
    from PIL import Image, ImageFile, ImageOps
    if version('pillow') != PILLOW_VERSION:
        raise RuntimeError
    Image.MAX_IMAGE_PIXELS = MAX_SOURCE_PIXELS
    ImageFile.LOAD_TRUNCATED_IMAGES = False
    if not 4 <= len(source) <= MAX_SOURCE_BYTES or not source.startswith(b'\xff\xd8\xff') or not source.rstrip(b'\x00').endswith(b'\xff\xd9'):
        raise JpegRenderError('JPEG_SOURCE_INVALID')
    with warnings.catch_warnings():
        warnings.simplefilter('error')
        with Image.open(io.BytesIO(source), formats=['JPEG']) as original:
            sw, sh = original.size
            if not 1 <= sw <= MAX_SOURCE_EDGE or not 1 <= sh <= MAX_SOURCE_EDGE or sw * sh > MAX_SOURCE_PIXELS:
                raise JpegRenderError('JPEG_PIXEL_LIMIT')
            if original.mode not in {'RGB', 'L'}:
                raise JpegRenderError('JPEG_COLOR_PROFILE_UNRESOLVED')
            orientation = original.getexif().get(274, 1)
            if type(orientation) is not int or not 1 <= orientation <= 8:
                raise JpegRenderError('JPEG_ORIENTATION_INVALID')
            original.load()
            oriented = ImageOps.exif_transpose(original)
            profile = original.info.get('icc_profile')
            if profile:
                try:
                    from PIL import ImageCms
                    if len(profile) > 1024 * 1024:
                        raise ValueError
                    oriented = ImageCms.profileToProfile(oriented,
                        ImageCms.ImageCmsProfile(io.BytesIO(profile)), ImageCms.createProfile('sRGB'), outputMode='RGB')
                except Exception:
                    raise JpegRenderError('JPEG_COLOR_PROFILE_UNRESOLVED') from None
            else:
                oriented = oriented.convert('RGB')
            scale = min(1.0, MAX_EDGE / max(oriented.size), math.sqrt(MAX_PIXELS / (oriented.width * oriented.height)))
            if scale < 1:
                oriented = oriented.resize((max(1, int(oriented.width * scale)), max(1, int(oriented.height * scale))), Image.Resampling.LANCZOS)
            # Clear metadata after applying its orientation semantics. Never
            # forward EXIF/GPS, comments, embedded profiles or XMP to OCR.
            oriented.info.clear()
            output = io.BytesIO()
            oriented.save(output, format='JPEG', quality=95, subsampling=0, optimize=False, progressive=False)
            image = output.getvalue()
            if len(image) > MAX_IMAGE_BYTES:
                raise JpegRenderError('JPEG_OUTPUT_LIMIT')
        # Pixel identity refers to decoding the actual OCR/display JPEG bytes,
        # including the bounded re-encode, not an earlier in-memory bitmap.
        with Image.open(io.BytesIO(image), formats=['JPEG']) as decoded:
            decoded.load()
            pixel_identity = b'anhuan.material.image.rgb.v1\x00' + decoded.width.to_bytes(4, 'big') + decoded.height.to_bytes(4, 'big') + decoded.tobytes()
            header = {'source_width': sw, 'source_height': sh, 'width': decoded.width, 'height': decoded.height,
                'exif_orientation': orientation, 'source_sha256': hashlib.sha256(source).hexdigest(),
                'pixel_sha256': hashlib.sha256(pixel_identity).hexdigest()}
    return header, image


def _worker():
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (15, 16))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    if sys.platform.startswith('linux'):
        resource.setrlimit(resource.RLIMIT_DATA, (768 * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_AS, (1024 * 1024 * 1024,) * 2)
    source = sys.stdin.buffer.read(MAX_SOURCE_BYTES + 1)
    try:
        header, image = _decode(source)
    except JpegRenderError as exc:
        header, image = {'error': str(exc)}, b''
    except Exception:
        header, image = {'error': 'JPEG_SOURCE_INVALID'}, b''
    sys.stdout.buffer.write(json.dumps(header, separators=(',', ':')).encode() + b'\n' + image)


if __name__ == '__main__':
    _worker()
