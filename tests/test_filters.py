"""
Unit tests for utils/filters.py predicates. All are pure functions — no mocking needed.
"""

import pytest

from utils.filters import (
    expand_known_keys,
    job_text,
    passes_anti_keywords,
    passes_discarded_urls,
    passes_excluded_companies,
    passes_experience,
    passes_location,
    passes_seniority,
    passes_since,
    passes_work_mode,
    row_is_known,
    term_hits,
    url_is_known,
)


def make_job(title="", description="", location=""):
    return {
        "Job Title": title,
        "Job Description": description,
        "Job Location": location,
    }


# ── passes_discarded_urls ────────────────────────────────────────────────────


class TestPassesDiscardedUrls:
    def test_empty_discarded_set_always_passes(self):
        assert passes_discarded_urls("https://example.com/job/1", set()) is True

    def test_url_not_in_discarded_set_passes(self):
        discarded = {"https://example.com/job/2"}
        assert passes_discarded_urls("https://example.com/job/1", discarded) is True

    def test_url_in_discarded_set_fails(self):
        discarded = {"https://example.com/job/1"}
        assert passes_discarded_urls("https://example.com/job/1", discarded) is False


# ── passes_location ──────────────────────────────────────────────────────────


class TestPassesLocation:
    def test_no_acceptable_locations_configured_always_passes(self):
        job = make_job(location="Berlin")
        assert passes_location(job, []) is True

    def test_matching_location_passes(self):
        job = make_job(location="Remote - Worldwide")
        assert passes_location(job, ["remote", "mexico"]) is True

    def test_case_insensitive_matching(self):
        job = make_job(location="FULLY REMOTE")
        assert passes_location(job, ["remote"]) is True

    def test_non_matching_location_fails(self):
        job = make_job(location="Berlin, Germany")
        assert passes_location(job, ["remote", "mexico"]) is False

    def test_missing_location_key_passes(self):
        job = {"Job Title": "x", "Job Description": "y"}
        assert passes_location(job, ["remote"]) is True

    def test_none_location_passes(self):
        job = make_job(location=None)
        assert passes_location(job, ["remote"]) is True

    def test_empty_string_location_passes(self):
        job = make_job(location="")
        assert passes_location(job, ["remote"]) is True


# ── passes_anti_keywords ─────────────────────────────────────────────────────


class TestPassesAntiKeywords:
    def test_empty_anti_keywords_always_passes(self):
        job = make_job(title="Senior Staff Engineer")
        assert passes_anti_keywords(job, []) is True

    def test_matching_anti_keyword_fails(self):
        job = make_job(title="Senior Staff Engineer")
        assert passes_anti_keywords(job, ["senior staff"]) is False

    def test_case_insensitive_matching(self):
        job = make_job(title="SENIOR STAFF ENGINEER")
        assert passes_anti_keywords(job, ["senior staff"]) is False

    def test_no_match_passes(self):
        job = make_job(title="Junior Python Developer")
        assert passes_anti_keywords(job, ["senior staff", "clearance required"]) is True

    def test_matches_against_title(self):
        job = make_job(title="Clearance Required Role", description="normal text")
        assert passes_anti_keywords(job, ["clearance required"]) is False

    def test_matches_against_description(self):
        job = make_job(title="Normal Role", description="<p>Clearance Required for this job</p>")
        assert passes_anti_keywords(job, ["clearance required"]) is False


# ── passes_experience ────────────────────────────────────────────────────────


class TestPassesExperience:
    def test_no_experience_mention_passes(self):
        job = make_job(description="We are looking for a great engineer.")
        assert passes_experience(job, 4) is True

    def test_none_max_experience_years_is_noop(self):
        job = make_job(description="Requires 10 years of experience")
        assert passes_experience(job, None) is True

    def test_plain_n_years_below_max_passes(self):
        job = make_job(description="Requires 3 years of experience")
        assert passes_experience(job, 4) is True

    def test_plain_n_years_equal_to_max_passes(self):
        job = make_job(description="Requires 3 years of experience")
        assert passes_experience(job, 3) is True

    def test_plain_n_years_above_max_fails(self):
        job = make_job(description="Requires 5 years of experience")
        assert passes_experience(job, 3) is False

    def test_n_plus_years_below_max_passes(self):
        job = make_job(description="3+ years of experience required")
        assert passes_experience(job, 4) is True

    def test_n_plus_years_above_max_fails(self):
        job = make_job(description="5+ years of experience required")
        assert passes_experience(job, 3) is False

    def test_range_uses_minimum_passes_when_min_at_or_below_max(self):
        # "3-5 years" — only the minimum (3) is compared, so it passes at max=3
        # even though the upper bound (5) exceeds it.
        job = make_job(description="3-5 years of experience")
        assert passes_experience(job, 3) is True

    def test_range_fails_when_minimum_exceeds_max(self):
        job = make_job(description="5-7 years of experience")
        assert passes_experience(job, 4) is False

    def test_at_least_n_years_phrasing(self):
        job = make_job(description="at least 3 years of relevant experience")
        assert passes_experience(job, 3) is True
        assert passes_experience(job, 2) is False

    def test_minimum_of_n_years_phrasing(self):
        job = make_job(description="minimum of 3 years of relevant experience")
        assert passes_experience(job, 3) is True
        assert passes_experience(job, 2) is False

    def test_multiple_mentions_one_exceeding_max_fails(self):
        job = make_job(
            description="2 years of Python and 8 years of distributed systems experience"
        )
        assert passes_experience(job, 4) is False

    def test_multiple_mentions_all_within_max_passes(self):
        job = make_job(
            description="2 years of Python and 3 years of distributed systems experience"
        )
        assert passes_experience(job, 4) is True

    def test_experience_mention_in_title_is_considered(self):
        job = make_job(title="Engineer with 10+ years experience", description="")
        assert passes_experience(job, 4) is False


# ── job_text ──────────────────────────────────────────────────────────────────


class TestJobText:
    def test_combines_title_and_stripped_description(self):
        job = make_job(title="Solidity Dev", description="<p>Loves <b>DeFi</b></p>")
        text = job_text(job)
        assert "Solidity Dev" in text
        assert "Loves" in text
        assert "DeFi" in text
        assert "<p>" not in text
        assert "<b>" not in text

    def test_missing_fields_default_to_empty_string(self):
        assert job_text({}) == " "

    def test_none_fields_default_to_empty_string(self):
        job = {"Job Title": None, "Job Description": None}
        assert job_text(job) == " "

    def test_used_consistently_by_predicates(self):
        # anti_keywords and experience both key off job_text — a keyword hidden inside
        # HTML tags' attributes shouldn't leak in, but plain text content should.
        job = make_job(
            title="Role",
            description='<div class="senior">Requires clearance required for access</div>',
        )
        assert passes_anti_keywords(job, ["clearance required"]) is False
        # "senior" only appears in the class attribute, which is stripped along with the tag
        assert passes_anti_keywords(job, ["class=\"senior\""]) is True


# ── improve-discovery-filters ────────────────────────────────────────────────

def _legacy_anti(job, anti_keywords):
    """Verbatim pre-change implementation, kept as the oracle for the default mode."""
    if not anti_keywords:
        return True
    text = job_text(job).lower()
    return not any(kw.lower() in text for kw in anti_keywords)


def _legacy_match(text, keywords):
    lowered = text.lower()
    return [kw for kw in keywords if kw.lower() in lowered]


CORPUS_TEXTS = [
    ("Go Engineer", "<p>Work with MongoDB and Go. 3+ years</p>"),
    ("Smart Contracts Dev", "<p>Solidity, smart  contracts, Zürich</p>"),
    ("Category Manager", "<div>category things; clearance required</div>"),
    ("", ""), ("Rust", None),
]
CORPUS_TERMS = [["go"], ["smart contracts", "solidity"], ["clearance required", "rust"], ["zurich"], ["PYTHON"], []]


class TestTermHitsLegacyEquivalence:
    @pytest.mark.parametrize("title,desc", CORPUS_TEXTS)
    @pytest.mark.parametrize("terms", CORPUS_TERMS)
    def test_default_mode_equals_old_code(self, title, desc, terms):
        job = {"Job Title": title, "Job Description": desc}
        assert passes_anti_keywords(job, terms) == _legacy_anti(job, terms)
        assert term_hits(job, terms) == _legacy_match(job_text(job), terms)
        assert passes_anti_keywords(job, terms, "substring") == _legacy_anti(job, terms)


class TestTermHitsWordMode:
    def job(self, text, title="T"):
        return {"Job Title": title, "Job Description": f"<p>{text}</p>"}

    def test_substring_default_excludes_on_mongodb(self):
        assert not passes_anti_keywords(self.job("we use mongodb"), ["go"])

    def test_word_mode_avoids_false_positive(self):
        assert passes_anti_keywords(self.job("we use mongodb"), ["go"], "word")
        assert not passes_anti_keywords(self.job("we use Go daily"), ["go"], "word")

    def test_phrase_case_punctuation_counts_once(self):
        job = self.job("Senior SMART  Contracts, Solidity")
        assert term_hits(job, ["smart contracts", "smart contracts"], "word") == ["smart contracts"]

    def test_accents(self):
        assert not passes_anti_keywords(self.job("Based in Zürich"), ["zurich"], "word")

    def test_empty_terms_ignored(self):
        assert term_hits(self.job("x"), ["", "  "], "word") == []
        assert passes_anti_keywords(self.job("x"), [""])

    def test_ranking_counts_unique_keywords(self):
        job = self.job("python python python rust", title="Dev")
        assert len(term_hits(job, ["python", "rust", "go"])) == 2

    def test_same_rule_for_any_source_row(self):
        text = "Python and mongodb"
        rows = [{"Job Title": "A", "Job Description": text, "source": s} for s in ("findweb3", "web3career", "lever")]
        assert len({tuple(term_hits(r, ["go", "python"], "word")) for r in rows}) == 1


class TestNewPredicates:
    def test_since(self):
        since = "2026-10-01T00:00:00Z"
        old, new, undated = ({"posted_at": d} for d in ("2026-09-15T00:00:00Z", "2026-10-10T00:00:00Z", None))
        assert [passes_since(j, since) for j in (old, new, undated)] == [False, True, True]
        assert passes_since(old, None)

    def test_work_mode(self):
        modes = ["remote"]
        assert [passes_work_mode({"work_mode": m}, modes) for m in ("onsite", "remote", "unknown")] == [False, True, True]
        assert passes_work_mode({"work_mode": "onsite"}, [])

    def test_seniority_whole_word_title_only(self):
        assert not passes_seniority({"Job Title": "Senior Engineer"}, ["senior"])
        assert passes_seniority({"Job Title": "Seniority Analyst"}, ["senior"])
        assert passes_seniority({"Job Title": "Dev", "Job Description": "work with senior staff"}, ["senior"])
        assert not passes_seniority({"Job Title": "Sr. Staff-Engineer"}, ["staff"])

    def test_excluded_companies_equality(self):
        assert not passes_excluded_companies({"Company Name": "acme inc"}, ["ACME, Inc."])
        assert passes_excluded_companies({"Company Name": "Acme Labs"}, ["acme"])
        assert passes_excluded_companies({"Company Name": None}, ["acme"])

    def test_known_keys_forms(self):
        known = expand_known_keys(["https://web3.career/x/9?utm_source=a", "findweb3:rec1", "acme|dev|remote"])
        assert url_is_known("https://web3.career/x/9/", known)
        assert url_is_known("https://findweb3.com/job/rec1", known)
        assert not url_is_known("https://findweb3.com/job/rec2", known)
        assert row_is_known({"dedupe_key": "acme|dev|remote"}, known)
        assert row_is_known({"source": "findweb3", "source_job_id": "rec1"}, known)
        assert not row_is_known({"dedupe_key": "||"}, expand_known_keys(["||"]))
        assert not row_is_known({"canonical_url": "x"}, set())
