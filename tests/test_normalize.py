"""utils/normalize.py: pure helpers, no network."""

import pytest

from utils.normalize import (
    build_row, canonical_url, dedupe_key, html_to_text, identify_url, infer_work_mode,
    norm_text, stable_id, to_iso_utc,
)

LEGACY_KEYS = {"Job Title", "Company Name", "Job Description", "Job Location",
               "Compensation", "Job Url", "Tags", "Notes"}
NORMALIZED_KEYS = {"source", "source_job_id", "canonical_url", "apply_url", "company", "title",
                   "location", "work_mode", "posted_at", "description_html", "description_text",
                   "salary", "ats_type", "tags", "dedupe_key"}


@pytest.mark.parametrize("url,expected", [
    ("HTTPS://Web3.Career/job-x/123/?utm_source=a&ref=b#apply", "https://web3.career/job-x/123"),
    ("https://web3.career:443/job-x/123", "https://web3.career/job-x/123"),
    ("http://example.com:8080/a/?b=2&a=1&fbclid=z&mc_cid=1&gclid=2", "http://example.com:8080/a?a=1&b=2"),
    ("https://example.com/", "https://example.com/"),
    ("https://job-boards.greenhouse.io/stripe/jobs/42?gh_src=x", "https://boards.greenhouse.io/stripe/jobs/42"),
    ("https://job-boards.eu.greenhouse.io/stripe/jobs/42", "https://boards.greenhouse.io/stripe/jobs/42"),
    ("https://boards.greenhouse.io/stripe/jobs/42/", "https://boards.greenhouse.io/stripe/jobs/42"),
    ("https://jobs.lever.co/netflix/abc-123/apply?lever-source=x", "https://jobs.lever.co/netflix/abc-123"),
    ("https://jobs.ashbyhq.com/Ramp/uuid-1/application?utm_source=a", "https://jobs.ashbyhq.com/Ramp/uuid-1"),
    ("https://de.linkedin.com/comm/jobs/view/3912345678/?trackingId=x&refId=y",
     "https://www.linkedin.com/jobs/view/3912345678/"),
    ("https://www.linkedin.com/jobs/view/senior-engineer-at-acme-3912345678?x=1",
     "https://www.linkedin.com/jobs/view/3912345678/"),
    ("https://wellfound.com/jobs/1234567-senior-engineer?utm_source=a", "https://wellfound.com/jobs/1234567"),
    ("https://www.findweb3.com/job/recABC/", "https://findweb3.com/job/recABC"),
    ("https://www.web3.career/staff-eng-acme/153659?ref=x", "https://web3.career/staff-eng-acme/153659"),
    ("https://job-boards.greenhouse.io/stripe/jobs/7?gh_src=q", "https://boards.greenhouse.io/stripe/jobs/7"),
])
def test_canonical_url(url, expected):
    assert canonical_url(url) == expected
    assert canonical_url(expected) == expected  # idempotent


@pytest.mark.parametrize("bad", [None, "", "not a url", "ftp://x.com/a", 5])
def test_canonical_url_rejects_garbage(bad):
    assert canonical_url(bad) is None


def test_identify_url_ids():
    assert identify_url("https://de.linkedin.com/comm/jobs/view/3912345678/?trackingId=x")["source_job_id"] == "3912345678"
    assert identify_url("https://wellfound.com/jobs/1234567-x")["source_job_id"] == "1234567"
    w3 = identify_url("https://web3.career/staff-engineer/153659?utm_source=a")
    assert (w3["source"], w3["source_job_id"]) == ("web3career", "153659")
    fw = identify_url("https://findweb3.com/job/recoUnkn8Eb9kNPcK")
    assert (fw["source"], fw["source_job_id"]) == ("findweb3", "recoUnkn8Eb9kNPcK")
    assert identify_url("https://jobs.lever.co/n/abc-1/apply")["source_job_id"] == "abc-1"
    assert identify_url("https://boards.greenhouse.io/s/jobs/9")["source_job_id"] == "9"
    assert identify_url("https://example.com/x")["source_job_id"] is None
    assert identify_url("https://findweb3.com/jobs/python")["source_job_id"] is None


def test_stable_id_is_sha1_of_canonical():
    assert stable_id("https://a.b/c") == stable_id("https://a.b/c")
    assert len(stable_id("https://a.b/c")) == 40 and stable_id(None) is None


def test_dedupe_key_ignores_cosmetics():
    a = dedupe_key("ACME, Inc.", "Sr. Engineer", "Zürich")
    b = dedupe_key("acme  inc", "sr engineer!", "zurich")
    assert a == b == "acme inc|sr engineer|zurich"
    assert dedupe_key(None, "T", None) == "|t|"


def test_html_to_text():
    assert html_to_text("<p>Build &amp; ship</p><ul><li>Rust</li></ul>") == "Build & ship Rust"
    assert html_to_text(None) is None and html_to_text("") is None


@pytest.mark.parametrize("texts,mode", [
    (("Fully Remote",), "remote"), (("Berlin (On-site)",), "onsite"),
    (("Remote / Hybrid",), "hybrid"), (("Berlin",), "unknown"),
    (("Remoteness index",), "unknown"), (("Anywhere", None), "remote"), ((), "unknown"),
])
def test_infer_work_mode(texts, mode):
    assert infer_work_mode(*texts) == mode


def test_to_iso_utc():
    assert to_iso_utc("2026-10-16T18:43:24-04:00") == "2026-10-16T22:43:24Z"
    assert to_iso_utc(1786469891368) == to_iso_utc("1786469891") == "2026-08-11T17:38:11Z"
    assert to_iso_utc("2026-10-15T09:56:11.370Z") == "2026-10-15T09:56:11Z"
    assert to_iso_utc("2026-10-15") == "2026-10-15T00:00:00Z"
    assert to_iso_utc("garbage") is None and to_iso_utc(None) is None


def test_norm_text():
    assert norm_text("  Zürich, SMART  Contracts! ") == "zurich smart contracts"


def legacy(**kw):
    d = {"Job Title": "Engineer", "Company Name": "Acme", "Job Description": "<p>Hi</p>",
         "Job Location": "Remote", "Compensation": None, "Job Url": "https://web3.career/eng-acme/1?ref=x"}
    d.update(kw)
    return d


def test_build_row_has_all_keys_and_unchanged_legacy_values():
    row = build_row(legacy(), "web3career", tags=["python"], source_job_id="1")
    assert set(row) == LEGACY_KEYS | NORMALIZED_KEYS
    assert row["Job Url"] == "https://web3.career/eng-acme/1?ref=x"  # legacy value untouched
    assert row["canonical_url"] == "https://web3.career/eng-acme/1"
    assert row["Tags"] == "python" and row["tags"] == ["python"] and row["Notes"] == ""
    assert row["work_mode"] == "remote" and row["posted_at"] is None and row["salary"] is None
    assert row["apply_url"] is None and row["ats_type"] is None
    assert row["description_text"] == "Hi" and row["description_html"] == "<p>Hi</p>"


def test_build_row_unknowns_are_null_not_missing():
    row = build_row(legacy(**{"Company Name": None, "Job Description": None, "Job Location": None}), "x")
    assert row["company"] is None and row["description_text"] is None and row["tags"] == []
    assert row["source_job_id"] is None and row["work_mode"] == "unknown"


def test_build_row_id_fallback_and_ats_type():
    row = build_row(legacy(), "web3career", id_fallback=True,
                    apply_url="https://job-boards.greenhouse.io/s/jobs/9")
    assert row["source_job_id"] == stable_id(row["canonical_url"])
    assert row["ats_type"] == "greenhouse"
