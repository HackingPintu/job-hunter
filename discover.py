"""Probe public ATS APIs for candidate companies and merge the ones that respond into jobhunter/data/companies.yaml.

Usage:  python tools/discover.py [--candidates tools/candidates.txt] [--dry-run]
Candidate file format, one per line:  tier|Company name|slug1,slug2
"""
import argparse
import concurrent.futures as cf
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "jobhunter" / "data" / "companies.yaml"
H = {"User-Agent": "Mozilla/5.0 (JobHunter discovery)"}

PROBES = {
    "greenhouse": lambda s: f"https://boards-api.greenhouse.io/v1/boards/{s}/jobs",
    "lever": lambda s: f"https://api.lever.co/v0/postings/{s}?mode=json&limit=1",
    "ashby": lambda s: f"https://api.ashbyhq.com/posting-api/job-board/{s}",
    "smartrecruiters": lambda s: f"https://api.smartrecruiters.com/v1/companies/{s}/postings?limit=1",
}


def has_jobs(kind, data):
    if kind == "greenhouse":
        return bool(data.get("jobs"))
    if kind == "lever":
        return isinstance(data, list) and len(data) > 0
    if kind == "ashby":
        return bool(data.get("jobs"))
    return int(data.get("totalFound", 0)) > 0


def probe(tier, name, slugs):
    for slug in slugs:
        for kind, url in PROBES.items():
            try:
                r = requests.get(url(slug), headers=H, timeout=12)
                if r.status_code == 200 and has_jobs(kind, r.json()):
                    return {"name": name, "tier": tier, "type": kind, "token": slug}
            except Exception:
                continue
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", default=str(ROOT / "tools" / "candidates.txt"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    cand = []
    for line in Path(a.candidates).read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            tier, name, slugs = line.split("|")
            cand.append((tier.strip(), name.strip(), [s.strip() for s in slugs.split(",") if s.strip()]))

    doc = yaml.safe_load(DATA.read_text(encoding="utf-8"))
    known = {c["name"].lower() for c in doc["companies"]}
    todo = [c for c in cand if c[1].lower() not in known]
    print(f"Probing {len(todo)} new candidates ({len(cand) - len(todo)} already present)...")

    with cf.ThreadPoolExecutor(16) as ex:
        found = [r for r in ex.map(lambda c: probe(*c), todo) if r]
    for f in found:
        print(f"  + {f['name']} ({f['type']}:{f['token']})")
    print(f"{len(found)} new companies with live API, {len(todo) - len(found)} without.")

    if found and not a.dry_run:
        doc["companies"].extend(found)
        DATA.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=200), encoding="utf-8")
        print(f"Updated {DATA}")


if __name__ == "__main__":
    main()
