"""Small, dependency-free helpers used across the codebase."""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._\-]+")
_ID_RE = re.compile(r"^[A-Za-z0-9._\-]{1,128}$")
_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return utcnow().isoformat(timespec="seconds")


def sanitize_filename(name: str, fallback: str = "file") -> str:
    """Return a filesystem-safe filename, stripping path separators and control chars.

    Never used to build paths from untrusted input beyond the filename component.
    """
    name = name.replace("\\", "/").split("/")[-1]  # strip any directory component
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ascii", "ignore").decode("ascii")
    name = _SAFE_NAME_RE.sub("_", name).strip("._")
    if not name:
        name = fallback
    if len(name) > 120:
        stem, dot, ext = name.rpartition(".")
        name = (stem[: 120 - len(ext) - 1] + "." + ext) if dot else stem[:120]
    return name


def validate_id(value: str, name: str = "id") -> str:
    """Validate an internal identifier (video_id, frame_id, job_id, upload_id).

    Internal ids are generated as hex or prefixed hex + separators; anything
    else (e.g. path fragments) is rejected before it can reach the filesystem.
    """
    if not isinstance(value, str) or not _ID_RE.match(value):
        from .exceptions import ValidationError

        raise ValidationError(f"invalid {name}")
    return value


def format_hms(seconds: float) -> str:
    """Format seconds as HH:MM:SS (or MM:SS for short media)."""
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def human_size(num: float) -> str:
    """Format a byte count into a human-readable string."""
    num = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(num)} {unit}"
            return f"{num:.2f} {unit}"
        num /= 1024.0
    return f"{num:.2f} TB"


def parse_fraction(value: str | None) -> float | None:
    """Parse an ffmpeg-style rate ('30000/1001', '25', '0/0') into a float."""
    if not value or value in ("0/0", "N/A"):
        return None
    try:
        if "/" in value:
            num, den = value.split("/", 1)
            den_f = float(den)
            return float(num) / den_f if den_f else None
        return float(value)
    except (ValueError, ZeroDivisionError):
        return None


def parse_duration(value: str | None) -> float | None:
    """Parse an ffmpeg duration string ('01:32:14.12' or '123.45') into seconds."""
    if not value or value in ("N/A",):
        return None
    try:
        if ":" in value:
            parts = value.split(":")
            if len(parts) == 3:
                h, m, s = parts
                return int(h) * 3600 + int(m) * 60 + float(s)
            if len(parts) == 2:
                m, s = parts
                return int(m) * 60 + float(s)
        return float(value)
    except (ValueError, TypeError):
        return None


def ensure_within(base: Path, *parts: str) -> Path:
    """Join path parts and assert the result stays inside ``base`` (traversal guard).

    Uses resolved paths so symlinks that escape the root are detected too.
    """
    base = base.resolve()
    candidate = base.joinpath(*parts).resolve()
    if not candidate.is_relative_to(base):
        raise ValueError("path traversal detected")
    return candidate


# ---------------------------------------------------------------------------
# Date-range handling
#
# Stored timestamps are ISO-8601 UTC (e.g. "2026-08-17T12:34:56+00:00").
# The frontend sends date-only values ("2026-08-17"). A date-only *upper* bound
# is interpreted as the END of that day via an exclusive next-midnight bound,
# never as midnight (which would silently exclude the rest of the day).
# ---------------------------------------------------------------------------
def _has_time_component(value: str) -> bool:
    return "T" in value or " " in value or ":" in value


def date_range_condition(
    date_from: str | None, date_to: str | None, column: str = "uploaded_at"
) -> tuple[str, list[str]]:
    """Build a SQL WHERE fragment for an ISO date range against ``column``.

    Returns (clause_without_WHERE, params). Empty clause -> ("", []).
    """
    clauses: list[str] = []
    params: list[str] = []

    if date_from:
        if _DATE_ONLY_RE.match(date_from):
            params.append(f"{date_from}T00:00:00+00:00")
        else:
            params.append(date_from)
        clauses.append(f"{column} >= ?")

    if date_to:
        if _DATE_ONLY_RE.match(date_to):
            next_day = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
            params.append(next_day.strftime("%Y-%m-%dT00:00:00+00:00"))
            clauses.append(f"{column} < ?")  # exclusive upper bound
        else:
            params.append(date_to)
            clauses.append(f"{column} <= ?")

    return (" AND ".join(clauses), params)


def validate_date_bound(value: str | None, name: str = "date") -> str | None:
    """Validate + normalize a date/time bound.

    Canonical policy: storage is UTC; the API accepts ISO-8601. Date-only
    values (``YYYY-MM-DD``) are interpreted as a UTC calendar day. Datetime
    values MUST be timezone-aware (naive datetimes are rejected) and are
    normalized to UTC ISO-8601 before querying.
    """
    if value is None or value == "":
        return value
    if _DATE_ONLY_RE.match(value):
        try:
            datetime.strptime(value, "%Y-%m-%d")
            return value
        except ValueError:
            pass
    else:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            dt = None
        if dt is not None:
            if dt.tzinfo is None or dt.utcoffset() is None:
                from .exceptions import ValidationError

                raise ValidationError(
                    f"{name} must be timezone-aware (naive datetimes are "
                    f"rejected): {value!r}"
                )
            return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    from .exceptions import ValidationError

    raise ValidationError(f"invalid {name}: {value!r}")
