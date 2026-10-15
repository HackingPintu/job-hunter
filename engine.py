"""Fetches openings from public career-site APIs, filters and ranks them for a profile."""
import concurrent.futures as cf
import datetime as dt
import hashlib
import html
import os
import pickle
import re
import sqlite3
import time
import urllib.parse
from functools import lru_cache
from pathlib import Path

import requests
import yaml

from . import profile as prof

PKG = Path(__file__).parent
HOME = Path(os.getenv("JOBHUNTER_HOME", Path.cwd() / ".jobhunter"))
UA = {"User-Agent": "Mozilla/5.0 (JobHunter open-source job search tool)", "Accept": "application/json"}
TIMEOUT = 40
CACHE_TTL = 3 * 3600
TIER_BONUS = {"A": 8, "B": 6, "C": 4, "D": 0}
TIER_LABEL = {"A": "Dream", "B": "Stretch", "C": "Target", "D": "Safe"}

session = requests.Session()
session.headers.update(UA)


@lru_cache(maxsize=1)
def load_companies():
    return yaml.safe_load((PKG / "data" / "companies.yaml").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ helpers
def strip_html(s):
    s = html.unescape(s or "")
    s = re.sub(r"<(br|/p|/li|/div|/h\d)[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"[ \t]+", " ", s).strip()


def parse_date(v):
    if not v:
        return None
    if isinstance(v, (int, float)):
        return dt.datetime.fromtimestamp(v / 1000 if v > 1e11 else v, dt.timezone.utc)
    v = str(v).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %I:%M %p", "%Y-%m-%d", "%B %d, %Y"):
        try:
            d = dt.datetime.strptime(v, fmt)
            return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            pass
    try:
        d = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def workday_posted(txt):
    t = (txt or "").lower()
    now = dt.datetime.now(dt.timezone.utc)
    if "today" in t:
        return now
    if "yesterday" in t:
        return now - dt.timedelta(days=1)
    m = re.search(r"(\d+)\+?\s*day", t)
    if m:
        return now - dt.timedelta(days=int(m.group(1)))
    m = re.search(r"(\d+)\+?\s*month", t)
    if m:
        return now - dt.timedelta(days=30 * int(m.group(1)))
    return None


def job(company, jid, title, location, url, posted=None, desc=""):
    return {"company": company, "id": str(jid), "title": (title or "").strip(),
            "location": (location or "").strip(), "url": url, "posted": posted, "desc": desc or ""}


# ------------------------------------------------------------------ source adapters
# Each adapter takes (company_cfg, ctx) where ctx = {"country": {...}, "country_name": str, "terms": [...]}
def fetch_greenhouse(c, ctx):
    r = session.get(f"https://boards-api.greenhouse.io/v1/boards/{c['token']}/jobs?content=true", timeout=TIMEOUT)
    r.raise_for_status()
    return [job(c["name"], j["id"], j["title"], (j.get("location") or {}).get("name"), j["absolute_url"],
                parse_date(j.get("updated_at")), strip_html(j.get("content")))
            for j in r.json().get("jobs", [])]


def fetch_lever(c, ctx):
    r = session.get(f"https://api.lever.co/v0/postings/{c['token']}?mode=json", timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json():
        cat = j.get("categories") or {}
        loc = ", ".join(filter(None, [cat.get("location")] + (cat.get("allLocations") or [])))
        out.append(job(c["name"], j["id"], j["text"], loc, j["hostedUrl"], parse_date(j.get("createdAt")),
                       j.get("descriptionPlain", "") + "\n" + "\n".join(
                           (x.get("text", "") + " " + strip_html(x.get("content", ""))) for x in j.get("lists", []))))
    return out


def fetch_ashby(c, ctx):
    r = session.get(f"https://api.ashbyhq.com/posting-api/job-board/{c['token']}", timeout=TIMEOUT)
    r.raise_for_status()
    return [job(c["name"], j["id"], j["title"], j.get("location"), j.get("jobUrl"),
                parse_date(j.get("publishedAt")), j.get("descriptionPlain", ""))
            for j in r.json().get("jobs", []) if j.get("isListed", True)]


def fetch_amazon(c, ctx):
    out, seen = [], set()
    for q in (c.get("queries") or ctx["terms"])[:4]:
        for offset in range(0, 300, 100):
            r = session.get("https://www.amazon.jobs/en/search.json", timeout=TIMEOUT, params={
                "base_query": q, "loc_query": ctx["country_name"], "country": ctx["country"]["code3"],
                "result_limit": 100, "offset": offset, "sort": "recent"})
            r.raise_for_status()
            jobs = r.json().get("jobs", [])
            for j in jobs:
                if j["id_icims"] in seen:
                    continue
                seen.add(j["id_icims"])
                desc = " ".join([j.get("description", ""), j.get("basic_qualifications", ""),
                                 j.get("preferred_qualifications", "")])
                out.append(job(c["name"], j["id_icims"], j["title"], j.get("normalized_location"),
                               "https://www.amazon.jobs" + j["job_path"], parse_date(j.get("posted_date")),
                               strip_html(desc)))
            if len(jobs) < 100:
                break
    return out


def fetch_atlassian(c, ctx):
    r = session.get("https://www.atlassian.com/endpoint/careers/listings", timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json():
        p = j.get("portalJobPost") or {}
        desc = " ".join([j.get("overview", ""), j.get("responsibilities", ""), j.get("qualifications", "")])
        out.append(job(c["name"], j["id"], j["title"], " | ".join(j.get("locations", [])),
                       p.get("portalUrl") or f"https://www.atlassian.com/company/careers/details/{j['id']}",
                       parse_date(p.get("updatedDate")), strip_html(desc)))
    return out


def fetch_workday(c, ctx):
    base = f"https://{c['tenant']}.{c['dc']}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{c['tenant']}/{c['site']}"
    out, seen = [], set()
    for q in (c.get("queries") or ctx["terms"])[:4]:
        for offset in range(0, 100, 20):
            r = session.post(f"{api}/jobs", timeout=TIMEOUT, json={
                "appliedFacets": {}, "limit": 20, "offset": offset, "searchText": f"{q} {ctx['country_name']}"})
            r.raise_for_status()
            posts = r.json().get("jobPostings", [])
            for p in posts:
                path = p.get("externalPath", "")
                if path in seen:
                    continue
                seen.add(path)
                j = job(c["name"], path, p.get("title"), p.get("locationsText"),
                        f"{base}/{c['site']}{path}", workday_posted(p.get("postedOn")), "")
                j["_detail"] = ("workday", api, path)
                out.append(j)
            if len(posts) < 20:
                break
    return out


def fetch_smartrecruiters(c, ctx):
    out, offset = [], 0
    while offset < 500:
        r = session.get(f"https://api.smartrecruiters.com/v1/companies/{c['token']}/postings", timeout=TIMEOUT,
                        params={"limit": 100, "offset": offset, "country": ctx["country"]["code2"]})
        r.raise_for_status()
        data = r.json()
        for p in data.get("content", []):
            loc = p.get("location", {})
            j = job(c["name"], p["id"], p["name"], f"{loc.get('city', '')}, {loc.get('country', '').upper()}",
                    f"https://jobs.smartrecruiters.com/{c['token']}/{p['id']}", parse_date(p.get("releasedDate")), "")
            j["_detail"] = ("sr", p["ref"])
            out.append(j)
        offset += 100
        if offset >= data.get("totalFound", 0):
            break
    return out


def fetch_detail(spec):
    """Returns (description, extra_location, started_date) for sources whose list view has no description."""
    if spec[0] == "workday":
        r = session.get(f"{spec[1]}{spec[2]}", timeout=TIMEOUT)
        r.raise_for_status()
        info = r.json().get("jobPostingInfo", {})
        loc = " | ".join([info.get("location", "")] + info.get("additionalLocations", []) +
                         [info.get("country", {}).get("descriptor", "")])
        return strip_html(info.get("jobDescription", "")), loc, parse_date(info.get("startDate"))
    r = session.get(spec[1], timeout=TIMEOUT)
    r.raise_for_status()
    sec = r.json().get("jobAd", {}).get("sections", {})
    return " ".join(strip_html(s.get("text", "")) for s in sec.values()), "", None


FETCHERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby, "amazon": fetch_amazon,
            "atlassian": fetch_atlassian, "workday": fetch_workday, "smartrecruiters": fetch_smartrecruiters}


# ------------------------------------------------------------------ collection (with cache)
def _cache_path(country, terms):
    key = hashlib.md5((country + "|" + "|".join(sorted(terms))).encode()).hexdigest()[:12]
    return HOME / "cache" / f"{key}.pkl"


def collect(companies, ctx, progress=None, refresh=False):
    cp = _cache_path(ctx["country_name"], ctx["terms"])
    full = len(companies) == len(load_companies()["companies"])
    if full and not refresh and cp.exists() and time.time() - cp.stat().st_mtime < CACHE_TTL:
        try:
            data = pickle.loads(cp.read_bytes())
            if progress:
                progress(len(companies), len(companies), "Using cached company data (under 3 hours old)")
            return data["jobs"], data["health"], True
        except Exception:
            pass
    jobs, health, done = [], [], 0

    def run(c):
        try:
            res = FETCHERS[c["type"]](c, ctx)
            for j in res:
                j["tier"] = c.get("tier", "C")
            return c["name"], True, res
        except Exception as e:
            return c["name"], False, str(e)[:150]
    with cf.ThreadPoolExecutor(12) as ex:
        futs = [ex.submit(run, c) for c in companies]
        for f in cf.as_completed(futs):
            name, ok, res = f.result()
            done += 1
            if ok:
                jobs += res
                health.append((name, True, f"{len(res)} jobs fetched"))
            else:
                health.append((name, False, res))
            if progress:
                progress(done, len(companies), f"Scanned {done}/{len(companies)} companies (latest: {name})")
    if full:
        try:
            cp.parent.mkdir(parents=True, exist_ok=True)
            tmp = cp.with_suffix(".tmp")
            tmp.write_bytes(pickle.dumps({"jobs": jobs, "health": health}))
            tmp.replace(cp)
        except Exception:
            pass
    return jobs, health, False


# ------------------------------------------------------------------ filtering and scoring
class Scorer:
    def __init__(self, p):
        self.p = p
        reg = prof.regions()["countries"][p["country"]]
        self.reg = reg
        self.inc = [prof.term_re(t) for t in p["title_include"]]
        self.exc = [prof.term_re(t) for t in p["title_exclude"]]
        self.boost = [(prof.term_re(k), v) for k, v in p["title_boost"].items()]
        self.skills = [(k, prof.skill_regexes(k), w) for k, w in p["skills"].items()]
        self.must = [prof.skill_regexes(k) for k in p["must_have"]]
        self.avoid = [prof.skill_regexes(k) for k in p["avoid"]]
        self.country_re = [prof.term_re(n) for n in reg["names"]] + [prof.term_re(reg["code3"].lower())]
        self.city_res = {c: [prof.term_re(a) for a in al] for c, al in reg["cities"].items()}
        self.sel = p["cities"]
        self.min_gap = p["max_required_years"]

    def title_ok(self, title):
        t = title.lower()
        m = re.search(r"(\d+)\s*(?:-|–|to)\s*\d+\s*(?:years|yrs)", t)
        if m and int(m.group(1)) > self.p["max_required_years"]:
            return False
        if any(r.search(t) for r in self.exc):
            return False
        return any(r.search(t) for r in self.inc)

    def location_ok(self, loc):
        l = (loc or "").lower()
        if not l or re.search(r"\d+\s+locations", l):
            return True
        if self.p["remote_anywhere"] and "remote" in l:
            return True
        country_hit = any(r.search(l) for r in self.country_re)
        city_hit = {c for c, rs in self.city_res.items() if any(r.search(l) for r in rs)}
        if self.sel and self.p["strict_cities"]:
            if city_hit & set(self.sel):
                return True
            return country_hit and not city_hit
        return country_hit or bool(city_hit)

    @staticmethod
    def min_years(desc):
        d = desc.lower()
        vals = []
        for m in re.finditer(r"(\d{1,2})\s*(?:\+|plus)?\s*(?:(?:-|–|to)\s*\d{1,2}\s*)?\+?\s*(?:years?|yrs?)\b", d):
            ctx = d[max(0, m.start() - 80): m.end() + 80]
            if "experience" in ctx or "exp" in ctx:
                vals.append(int(m.group(1)))
        return min(vals) if vals else None

    def score(self, j):
        p, d, tl = self.p, j["desc"].lower(), j["title"].lower()
        s, why = 0, []
        tb = max([v for r, v in self.boost if r.search(tl)] or [0])
        if tb:
            s += tb
            why.append("title fits your level/role")
        hits = [(k, w) for k, rs, w in self.skills if any(r.search(d) or r.search(tl) for r in rs)]
        sk = min(sum(w for _, w in hits), p["skill_score_cap"])
        s += sk
        years = self.min_years(j["desc"])
        j["min_years"] = years
        gap = None if years is None else years - p["yoe"]
        if gap is None:
            ys = 5
        elif gap > 1:
            ys = 3
        elif gap > 0:
            ys = 12
        elif gap >= -2:
            ys = 20
        else:
            ys = 8
        s += ys
        if years is not None:
            why.append(f"asks {years}+ yrs (you: {p['yoe']:g})")
        s += TIER_BONUS.get(j["tier"], 0)
        loc = j["location"].lower()
        if p["cities"] and not p["strict_cities"] and any(r.search(loc) for c in p["cities"] for r in self.city_res[c]):
            s += 6
            why.append("in a preferred city")
        if j["posted"]:
            age = (dt.datetime.now(dt.timezone.utc) - j["posted"]).days
            s += 12 if age <= 3 else 8 if age <= 7 else 4 if age <= 14 else 0
        s -= 8 * sum(1 for rs in self.avoid if any(r.search(d) for r in rs))
        j["skills_hit"] = [k for k, _ in hits]
        if hits:
            why.append("matches " + ", ".join(k for k, _ in hits[:6]))
        j["why"] = why
        return max(0, min(100, s + 15))

    def has_must(self, j):
        if not self.must:
            return True
        text = (j["title"] + " " + j["desc"]).lower()
        return any(any(r.search(text) for r in rs) for rs in self.must)


def filter_jobs(jobs, scorer, p):
    kept, now = [], dt.datetime.now(dt.timezone.utc)
    for j in jobs:
        if j["tier"] not in p["tiers"] or not scorer.title_ok(j["title"]) or not scorer.location_ok(j["location"]):
            continue
        if j["posted"] and (now - j["posted"]).days > p["freshness_days_max"]:
            continue
        kept.append(j)
    return kept


_DETAIL_CACHE = {}


def enrich(jobs, scorer, progress=None):
    todo = [j for j in jobs if j.get("_detail")]
    done = [0]

    def run(j):
        try:
            if j["_detail"] not in _DETAIL_CACHE:
                _DETAIL_CACHE[j["_detail"]] = fetch_detail(j["_detail"])
            desc, loc, started = _DETAIL_CACHE[j["_detail"]]
            j["desc"] = desc
            if loc.strip(" |"):
                j["location"] = (j["location"] + " | " + loc) if j["location"] else loc
            j["posted"] = j["posted"] or started
        except Exception:
            pass
        done[0] += 1
        if progress and done[0] % 10 == 0:
            progress(len(todo), len(todo), f"Reading job descriptions {done[0]}/{len(todo)}...")
    with cf.ThreadPoolExecutor(24) as ex:
        list(ex.map(run, todo))
    return [j for j in jobs if scorer.location_ok(j["location"])]


def dedupe_and_cap(jobs, cap):
    best = {}
    for j in sorted(jobs, key=lambda x: -x["score"]):
        k = (j["company"], re.sub(r"[\s\-_]*\d+$", "", j["title"].lower()).strip(), j["location"].split("|")[0].strip().lower())
        best.setdefault(k, j)
    per, out = {}, []
    for j in sorted(best.values(), key=lambda x: -x["score"]):
        per[j["company"]] = per.get(j["company"], 0) + 1
        if per[j["company"]] <= cap:
            out.append(j)
    return out


# ------------------------------------------------------------------ "new since last run" tracking (CLI)
def mark_new(jobs):
    HOME.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(HOME / "jobs.db")
    con.execute("""CREATE TABLE IF NOT EXISTS jobs(key TEXT PRIMARY KEY, company TEXT, title TEXT, url TEXT,
                   first_seen TEXT, last_seen TEXT, score INT)""")
    today = dt.date.today().isoformat()
    for j in jobs:
        key = f"{j['company']}|{j['id']}"
        row = con.execute("SELECT first_seen FROM jobs WHERE key=?", (key,)).fetchone()
        j["is_new"] = row is None
        con.execute("INSERT INTO jobs(key,company,title,url,first_seen,last_seen,score) VALUES(?,?,?,?,?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET last_seen=excluded.last_seen, score=excluded.score",
                    (key, j["company"], j["title"], j["url"], today, today, j["score"]))
    con.commit()
    con.close()


# ------------------------------------------------------------------ search links for everything else
LINKEDIN_EXP = lambda y: "1%2C2" if y < 1 else "2%2C3" if y < 3 else "3%2C4" if y < 7 else "4%2C5"
INDEED = {"India": "in.indeed.com", "United States": "www.indeed.com", "United Kingdom": "uk.indeed.com",
          "Germany": "de.indeed.com", "Netherlands": "nl.indeed.com", "Canada": "ca.indeed.com",
          "Singapore": "sg.indeed.com", "United Arab Emirates": "ae.indeed.com", "Australia": "au.indeed.com",
          "Ireland": "ie.indeed.com"}


def build_links(p):
    country, terms = p["country"], p["search_terms"]
    exp = LINKEDIN_EXP(p["yoe"])
    where = p["cities"][0] if len(p["cities"]) == 1 else country
    boards = []
    for t in terms[:3]:
        q = urllib.parse.quote_plus(t)
        loc = urllib.parse.quote_plus(where)
        row = {"query": t, "links": [
            ("LinkedIn (24h)", f"https://www.linkedin.com/jobs/search/?keywords={q}&location={loc}&f_TPR=r86400&f_E={exp}&sortBy=DD"),
            ("LinkedIn (week)", f"https://www.linkedin.com/jobs/search/?keywords={q}&location={loc}&f_TPR=r604800&f_E={exp}&sortBy=DD"),
            ("Indeed", f"https://{INDEED.get(country, 'www.indeed.com')}/jobs?q={q}&l={loc}&fromage=1&sort=date")]}
        if country == "India":
            slug = re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")
            row["links"].append(("Naukri", f"https://www.naukri.com/{slug}-jobs?experience={max(0, int(p['yoe']))}&jobAge=1"))
        boards.append(row)
    extra = [("Wellfound", "https://wellfound.com/jobs"), ("Hacker News: Who is hiring", "https://news.ycombinator.com/jobs")]
    if country == "India":
        extra += [("Cutshort", "https://cutshort.io/jobs"), ("Instahyre", "https://www.instahyre.com/search-jobs/")]
    return boards, extra


def manual_links(p):
    out, q = [], urllib.parse.quote_plus
    where = q(p["country"])
    exp = LINKEDIN_EXP(p["yoe"])
    for m in load_companies()["manual_boards"]:
        if m.get("tier", "C") not in p["tiers"]:
            continue
        if m.get("url") and p["country"] == "India":
            url = m["url"]
        else:
            url = (f"https://www.linkedin.com/jobs/search/?keywords={q(p['search_terms'][0] + ' ' + m['name'])}"
                   f"&location={where}&f_TPR=r604800&f_E={exp}&sortBy=DD")
        out.append({"name": m["name"], "tier": m.get("tier", "C"), "url": url})
    return out


# ------------------------------------------------------------------ the one entry point
def run_hunt(raw_profile, progress=None, refresh=False, track_new=False, only=None):
    p = prof.normalize_profile(raw_profile)
    cdef = prof.regions()["countries"][p["country"]]
    ctx = {"country": cdef, "country_name": p["country"], "terms": p["search_terms"]}
    companies = load_companies()["companies"]
    if only:
        names = {n.strip().lower() for n in only}
        companies = [c for c in companies if c["name"].lower() in names]
    jobs, health, cached = collect(companies, ctx, progress, refresh)
    if progress:
        progress(len(companies), len(companies), "Reading job descriptions and ranking for your profile (can take 1-2 minutes)...")
    scorer = Scorer(p)
    short = filter_jobs(jobs, scorer, p)
    short = enrich(short, scorer, progress)
    kept = []
    for j in short:
        j["score"] = scorer.score(j)
        if j["min_years"] is not None and j["min_years"] > p["max_required_years"]:
            continue
        if not scorer.has_must(j) or j["score"] < p["min_score_to_show"]:
            continue
        kept.append(j)
    kept = dedupe_and_cap(kept, p["max_per_company"])
    if track_new:
        mark_new(kept)
    else:
        for j in kept:
            j["is_new"] = False
    now = dt.datetime.now(dt.timezone.utc)
    out = []
    for j in sorted(kept, key=lambda x: (-x["score"], x["company"])):
        locs = " | ".join(dict.fromkeys(x.strip() for x in j["location"].split("|") if x.strip()))[:110]
        age = (now - j["posted"]).days if j["posted"] else None
        out.append({"score": j["score"], "company": j["company"], "tier": j["tier"], "title": j["title"],
                    "location": locs, "url": j["url"], "min_years": j.get("min_years"),
                    "skills": j["skills_hit"], "why": j["why"], "posted": j["posted"].date().isoformat() if j["posted"] else None,
                    "age_days": age, "is_new": j.get("is_new", False), "fresh": age is not None and age <= 3,
                    "hot": j["score"] >= p["apply_now_score"]})
    boards, extra = build_links(p)
    return {
        "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "profile": {k: p[k] for k in ("yoe", "country", "cities", "roles", "user_skills", "tiers", "expected_lpa", "search_terms", "apply_now_score")},
        "stats": {"fetched": len(jobs), "companies": len(companies), "sources_ok": sum(1 for h in health if h[1]),
                  "matches": len(out), "hot": sum(1 for j in out if j["hot"]), "new": sum(1 for j in out if j["is_new"]),
                  "cached": cached},
        "jobs": out, "health": [{"name": n, "ok": ok, "info": i} for n, ok, i in sorted(health)],
        "manual": manual_links(p), "boards": boards, "extra_boards": extra,
        "tier_label": TIER_LABEL,
    }
