"""
Pure helpers behind the normalized job row (capability `job-result-schema`): canonical
URLs, stable ids, dedupe key, work-mode inference, HTML to text, and `build_row`, the
single place that adds the normalized fields next to the legacy keys. No network access.
"""

import hashlib
import html
import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit

SCHEMA_VERSION = "1"

_TAG_RE = re.compile(r"<[^>]+>")
_PUNCT_RE = re.compile(r"[^\w\s]|_", re.UNICODE)
_WS_RE = re.compile(r"\s+")
_TRACKING_PARAMS = {"ref", "gh_src", "source", "lever-source", "lever-origin", "fbclid", "gclid"}


def norm_text(s) -> str:
    """NFKD, accents stripped, casefolded, punctuation to space, whitespace collapsed."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    return _WS_RE.sub(" ", _PUNCT_RE.sub(" ", s)).strip()


def html_to_text(html_str) -> str | None:
    """Tags replaced by spaces, entities decoded, whitespace collapsed; None if no input."""
    if not html_str:
        return None
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", html_str))).strip()


def dedupe_key(company, title, location) -> str:
    return "|".join(norm_text(p) for p in (company, title, location))


def stable_id(canonical: str | None) -> str | None:
    """Fallback source_job_id: sha1 hex of the canonical URL."""
    return hashlib.sha1(canonical.encode()).hexdigest() if canonical else None


def to_iso_utc(value) -> str | None:
    """ISO string / epoch (s or ms) / datetime -> ISO 8601 UTC string, or None."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
            n = float(value)
            dt = datetime.fromtimestamp(n / 1000 if n > 1e11 else n, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (ValueError, OverflowError, OSError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Work mode ────────────────────────────────────────────────────────────────

_HYBRID = re.compile(r"\bhybrid\b")
_REMOTE = re.compile(r"\b(remote|remotely|anywhere|work from home|wfh|distributed)\b")
_ONSITE = re.compile(r"\b(on site|onsite|in office|in person)\b")


def infer_work_mode(*texts) -> str:
    """remote|hybrid|onsite|unknown from location/title/tags text (word matching)."""
    text = norm_text(" ".join(str(t) for t in texts if t))
    for mode, pattern in (("hybrid", _HYBRID), ("remote", _REMOTE), ("onsite", _ONSITE)):
        if pattern.search(text):
            return mode
    return "unknown"


# ── URLs ─────────────────────────────────────────────────────────────────────

_ATS_HOSTS = {"greenhouse.io": "greenhouse", "lever.co": "lever", "ashbyhq.com": "ashby"}


def _host_is(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def _generic(parts, host: str) -> str:
    query = sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not (k.startswith(("utm_", "mc_")) or k in _TRACKING_PARAMS)
    )
    port = parts.port
    if port and (parts.scheme, port) not in (("http", 80), ("https", 443)):
        host = f"{host}:{port}"
    path = parts.path.rstrip("/") or "/"
    return f"{parts.scheme.lower()}://{host}{path}" + (f"?{urlencode(query)}" if query else "")


def identify_url(url: str | None) -> dict:
    """
    {source, source_job_id, canonical_url} derived from the URL alone (no network).
    `source` is set only for known hosts (linkedin, wellfound, web3career, findweb3,
    greenhouse, lever, ashby); unknown hosts only get a generic canonical form.
    """
    none = {"source": None, "source_job_id": None, "canonical_url": None}
    if not url or not isinstance(url, str):
        return none
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower()
    except ValueError:
        return none
    if parts.scheme.lower() not in ("http", "https") or not host:
        return none
    segs = [s for s in parts.path.split("/") if s]

    if _host_is(host, "linkedin.com"):
        m = re.search(r"/jobs/view/(?:[^/?#]*-)?(\d+)(?:/|$)", parts.path)
        if m:
            return {"source": "linkedin", "source_job_id": m.group(1),
                    "canonical_url": f"https://www.linkedin.com/jobs/view/{m.group(1)}/"}
    elif _host_is(host, "wellfound.com"):
        m = re.search(r"/jobs/(\d+)", parts.path)
        if m:
            return {"source": "wellfound", "source_job_id": m.group(1),
                    "canonical_url": f"https://wellfound.com/jobs/{m.group(1)}"}
    elif _host_is(host, "greenhouse.io") and len(segs) >= 3 and segs[1] == "jobs":
        return {"source": "greenhouse", "source_job_id": segs[2],
                "canonical_url": f"https://boards.greenhouse.io/{segs[0]}/jobs/{segs[2]}"}
    elif host == "jobs.lever.co" and len(segs) >= 2:
        return {"source": "lever", "source_job_id": segs[1],
                "canonical_url": f"https://jobs.lever.co/{segs[0]}/{segs[1]}"}
    elif host == "jobs.ashbyhq.com" and len(segs) >= 2:
        return {"source": "ashby", "source_job_id": segs[1],
                "canonical_url": f"https://jobs.ashbyhq.com/{segs[0]}/{segs[1]}"}

    bare = host[4:] if host.startswith("www.") else host
    canonical = _generic(parts, bare if bare in ("web3.career", "findweb3.com") else host)
    source, job_id = None, None
    if bare == "web3.career":
        source = "web3career"
        m = re.search(r"/(\d+)$", canonical.split("?")[0])
        job_id = m.group(1) if m else None
    elif bare == "findweb3.com":
        source = "findweb3"
        job_id = segs[-1] if len(segs) >= 2 and segs[0] == "job" else None
    return {"source": source, "source_job_id": job_id, "canonical_url": canonical}


def canonical_url(url: str | None) -> str | None:
    return identify_url(url)["canonical_url"]


def _ats_of(*urls) -> str | None:
    for url in urls:
        host = (urlsplit(url).hostname or "").lower() if url else ""
        for domain, ats in _ATS_HOSTS.items():
            if _host_is(host, domain):
                return ats
    return None


# ── Row ──────────────────────────────────────────────────────────────────────

def build_row(
    legacy: dict,
    source: str,
    *,
    tags=(),
    source_job_id=None,
    canonical=None,
    apply_url=None,
    posted_at=None,
    work_mode=None,
    ats_type=None,
    id_fallback=False,
) -> dict:
    """
    Legacy keys (unchanged values; `Tags` = ", ".join(tags), `Notes` = "") plus the
    normalized keys. `legacy` needs Job Title/Company Name/Job Description/Job Location/
    Compensation/Job Url. Unknown values are None (never omitted). `id_fallback` makes
    source_job_id the sha1 of canonical_url when the source gave none.
    """
    canonical = canonical_url(canonical or legacy.get("Job Url"))
    clean = lambda v: (str(v).strip() or None) if v is not None else None  # noqa: E731
    company, title, location = (
        clean(legacy.get(k)) for k in ("Company Name", "Job Title", "Job Location")
    )
    description = legacy.get("Job Description")
    tags = list(tags)
    sid = source_job_id if source_job_id not in (None, "") else (
        stable_id(canonical) if id_fallback else None
    )
    return {
        "Job Title": legacy.get("Job Title"),
        "Company Name": legacy.get("Company Name"),
        "Job Description": description,
        "Job Location": legacy.get("Job Location"),
        "Compensation": legacy.get("Compensation"),
        "Job Url": legacy.get("Job Url"),
        "Tags": ", ".join(tags),
        "Notes": "",
        "source": source,
        "source_job_id": None if sid is None else str(sid),
        "canonical_url": canonical,
        "apply_url": apply_url or None,
        "company": company,
        "title": title,
        "location": location,
        "work_mode": work_mode or infer_work_mode(location, title, " ".join(tags)),
        "posted_at": to_iso_utc(posted_at),
        "description_html": description,
        "description_text": html_to_text(description),
        "salary": clean(legacy.get("Compensation")),
        "ats_type": ats_type or _ats_of(apply_url, canonical),
        "tags": tags,
        "dedupe_key": dedupe_key(company, title, location),
    }


def set_tags(row: dict, tags) -> dict:
    """Record the matched keywords on a row (legacy csv `Tags` + list `tags`)."""
    row["tags"] = list(tags)
    row["Tags"] = ", ".join(row["tags"])
    return row
