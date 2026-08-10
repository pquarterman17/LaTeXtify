"""Image EXIF/GPS inspection and stripping.

Photographs carry more identifying information than any office document, and
unlike a document nobody thinks to check them. A micrograph or lab photo
dropped into a manuscript can ship:

- **GPS coordinates** -- the precise location the picture was taken
- **Camera and lens serial numbers** -- which link every photo you ever
  published to the same physical body
- **The photographer's name** and the software/OS used
- **An embedded thumbnail** generated at capture time. It is *not* regenerated
  when the image is edited, so a cropped or retouched photo can carry a
  thumbnail still showing the original, uncropped frame.

This matters here specifically because LaTeXtify already copies figures into
the compiled PDF, so anything left in a figure ends up in the submission.

Stripping rebuilds the file from pixel data rather than trying to blank
individual tags, because tag-level edits reliably miss maker-notes and the
IFD1 thumbnail. The ICC colour profile is re-attached by default: dropping it
silently shifts the colours of a scientific figure, which is a fidelity bug
traded for privacy nobody asked for.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, UnidentifiedImageError
from PIL.ExifTags import GPSTAGS, TAGS

from .report import Finding

#: Extensions handled here. Vector formats carry their own metadata and are
#: not raster images, so they are deliberately absent.
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp", ".gif")

#: EXIF tags worth naming individually, with why each matters.
_NOTABLE_TAGS = {
    "Make": ("camera", "medium", "the camera manufacturer"),
    "Model": ("camera", "medium", "the exact camera model used"),
    "BodySerialNumber": (
        "serial-number",
        "high",
        "the camera body's serial number, which links every photo from this "
        "body to the same owner",
    ),
    "LensSerialNumber": ("serial-number", "high", "the lens serial number"),
    "Software": ("software", "low", "the editing software and version"),
    "Artist": ("author", "high", "the photographer's name"),
    "Copyright": ("author", "medium", "a copyright/owner string"),
    "DateTimeOriginal": ("timestamp", "medium", "when the photo was taken"),
    "DateTime": ("timestamp", "low", "when the file was last modified"),
    "ImageDescription": ("description", "low", "a free-text description"),
    "XPAuthor": ("author", "high", "the Windows author field"),
    "XPComment": ("description", "medium", "a Windows comment field"),
}

_GPS_IFD = 34853


def _decode(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-16-le", errors="replace").rstrip("\x00").strip()
    return str(value).strip()


def _gps_summary(exif: Image.Exif) -> str | None:
    """Human-readable coordinates if the image carries a GPS IFD."""
    try:
        gps = exif.get_ifd(_GPS_IFD)
    except (KeyError, OSError, ValueError):
        return None
    if not gps:
        return None
    named = {GPSTAGS.get(key, key): value for key, value in gps.items()}
    lat, lon = named.get("GPSLatitude"), named.get("GPSLongitude")
    if not lat or not lon:
        return "present (partial)"

    def _dms(values: object, ref: object) -> str:
        try:
            deg, minutes, seconds = (float(v) for v in values)  # type: ignore[union-attr]
        except (TypeError, ValueError):
            return "?"
        decimal = deg + minutes / 60 + seconds / 3600
        if str(ref).upper() in ("S", "W"):
            decimal = -decimal
        return f"{decimal:.5f}"

    return f"{_dms(lat, named.get('GPSLatitudeRef'))}, {_dms(lon, named.get('GPSLongitudeRef'))}"


def _open(path: Path) -> Image.Image:
    try:
        return Image.open(path)
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"{path}: not a readable image ({exc})") from exc


def inspect(path: Path) -> tuple[list[Finding], list[str]]:
    """Report the metadata an image carries, without modifying it."""
    findings: list[Finding] = []
    warnings: list[str] = []

    with _open(path) as im:
        exif = im.getexif()

        gps = _gps_summary(exif)
        if gps:
            findings.append(
                Finding(
                    category="gps",
                    severity="high",
                    summary=f"GPS coordinates: {gps}",
                    detail=(
                        "The image records where it was captured. For lab or field "
                        "photographs this can identify a specific building or site."
                    ),
                    location="EXIF/GPSInfo",
                )
            )

        named = {TAGS.get(tag, tag): value for tag, value in exif.items()}
        for tag_name, (category, severity, why) in _NOTABLE_TAGS.items():
            if tag_name not in named:
                continue
            value = _decode(named[tag_name])
            if not value:
                continue
            findings.append(
                Finding(
                    category=category,
                    severity=severity,
                    summary=f"{tag_name}: {value[:60]}",
                    detail=f"EXIF records {why}.",
                    location=f"EXIF/{tag_name}",
                )
            )

        if _has_thumbnail(exif):
            findings.append(
                Finding(
                    category="exif-thumbnail",
                    severity="high",
                    summary="Embedded EXIF thumbnail",
                    detail=(
                        "A preview generated when the photo was captured. It is not "
                        "regenerated on edit, so a cropped or retouched image can "
                        "still carry a thumbnail of the ORIGINAL frame."
                    ),
                    location="EXIF/IFD1",
                )
            )

        if getattr(im, "n_frames", 1) > 1:
            warnings.append(
                f"{path.name} has {im.n_frames} frames; sanitizing keeps only the first."
            )

    return findings, warnings


def _has_thumbnail(exif: Image.Exif) -> bool:
    try:
        return bool(exif.get_ifd(1))
    except (KeyError, OSError, ValueError):
        return False


def sanitize(
    src: Path, dest: Path, *, keep_color_profile: bool = True, **_options: object
) -> tuple[list[Finding], list[str]]:
    """Write a metadata-free copy of ``src`` to ``dest``.

    Rebuilds the image from pixel data, which drops EXIF, GPS, maker notes,
    XMP and the embedded thumbnail together. The ICC profile is carried over
    unless ``keep_color_profile`` is false, so figure colours are unchanged.
    """
    removed, warnings = inspect(src)

    with _open(src) as im:
        fmt = im.format
        icc = im.info.get("icc_profile") if keep_color_profile else None
        frames = getattr(im, "n_frames", 1)
        im.load()
        # Rebuild from raw pixel bytes: this carries no info dict, no EXIF and
        # no maker notes. frombytes (rather than getdata) avoids materializing
        # a per-pixel Python list and is not deprecated.
        clean = Image.frombytes(im.mode, im.size, im.tobytes())
        if im.mode == "P" and im.palette is not None:
            clean.putpalette(im.palette)

        save_kwargs: dict[str, object] = {}
        if icc:
            save_kwargs["icc_profile"] = icc
        dest.parent.mkdir(parents=True, exist_ok=True)
        clean.save(dest, format=fmt, **save_kwargs)

    if frames > 1:
        warnings.append(
            f"Only the first of {frames} frames was written; the cleaned file is a "
            "still image."
        )
    if not removed:
        warnings.append("No embedded metadata was found; the copy is a re-encode.")
    return removed, warnings
