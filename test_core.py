import io

import pytest

from jobhunter import engine
from jobhunter import profile as prof
from jobhunter.webapp import create_app

CV = """Rakesh Rath - Software Engineer
Experience: 2 years of experience building backend services.
Skills: Java, Spring Boot, Python, REST APIs, MySQL, Docker, AWS, Kafka
Backend engineer, designed microservices. Backend developer using Java and Spring.
"""


def make_job(title, location, desc="", tier="B", posted=None):
    return {"company": "Acme", "id": title, "title": title, "location": location, "url": "https://x.test/1",
            "posted": posted, "desc": desc, "tier": tier}


def test_analyze_cv_finds_skills_and_yoe():
    info = prof.analyze_cv(CV)
    assert "java" in info["skills"]
    assert info["yoe"] == 2
    assert "backend" in info["roles"]


def test_extract_text_txt():
    assert "Java" in prof.extract_text(CV.encode(), "cv.txt")


def test_seniority_junior_excludes_senior():
    excl, boost = prof.seniority_rules(1.5)
    assert "senior" in excl and "sde 2" in boost


def test_normalize_profile_defaults_and_weights():
    p = prof.normalize_profile({"yoe": 2, "skills": "java, python, spring boot, kafka", "country": "India"})
    assert p["skills"]["java"] == 10 and p["max_required_years"] == 4
    assert p["roles"]


def test_unsupported_country():
    with pytest.raises(ValueError):
        prof.normalize_profile({"yoe": 2, "country": "Atlantis"})


def test_expected_lpa_hides_low_tiers_india_only():
    assert "D" not in prof.tiers_for_expectation(40, "India")
    assert prof.tiers_for_expectation(40, "Germany") == prof.TIERS


def test_scorer_title_and_location():
    p = prof.normalize_profile({"yoe": 2, "skills": "java", "roles": ["backend"], "country": "India",
                                "cities": ["Pune"], "strict_cities": True})
    s = engine.Scorer(p)
    assert s.title_ok("Software Engineer - Backend")
    assert not s.title_ok("Senior Staff Software Engineer")
    assert not s.title_ok("Sales Manager")
    assert s.title_ok("Software Engineer, Finance Systems")
    assert s.location_ok("Pune, India")
    assert not s.location_ok("Mumbai, India")


def test_scoring_prefers_matching_skills():
    p = prof.normalize_profile({"yoe": 2, "skills": "java, spring boot", "roles": ["backend"], "country": "India"})
    s = engine.Scorer(p)
    a = make_job("Software Engineer", "Pune, India", "We use Java and Spring Boot microservices")
    b = make_job("Software Engineer", "Pune, India", "We use Go and Rust")
    assert s.score(a) > s.score(b)


@pytest.fixture
def client():
    return create_app().test_client()


def test_index_and_meta(client):
    assert client.get("/").status_code == 200
    meta = client.get("/api/meta").get_json()
    assert "India" in meta["countries"] and meta["companies_total"] > 100


def test_analyze_endpoint(client):
    r = client.post("/api/analyze-cv", data={"cv": (io.BytesIO(CV.encode() * 3), "cv.txt")}, content_type="multipart/form-data")
    assert r.status_code == 200 and "java" in r.get_json()["skills"]


def test_analyze_requires_file(client):
    assert client.post("/api/analyze-cv").status_code == 400


def test_search_rejects_bad_country(client):
    assert client.post("/api/search", json={"yoe": 2, "country": "Atlantis"}).status_code == 400


def test_companies_data_is_valid():
    co = engine.load_companies()
    names = [c["name"] for c in co["companies"]]
    assert len(names) == len(set(names))
    assert all(c["type"] in {"greenhouse", "lever", "ashby", "amazon", "atlassian", "workday", "smartrecruiters"} for c in co["companies"])
