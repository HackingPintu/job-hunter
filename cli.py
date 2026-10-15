import argparse
import csv
import json
import sys
import webbrowser
from pathlib import Path

from . import __version__, engine
from . import profile as prof

TEMPLATE = Path(__file__).parent / "templates" / "app.html"


def write_report(result, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(result, ensure_ascii=False).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8").replace("/*__PRELOAD__*/null", payload, 1)
    report = out / "report.html"
    report.write_text(html, encoding="utf-8")
    with open(out / "jobs.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["score", "company", "tier", "title", "location", "min_years", "skills", "posted", "url"])
        for j in result["jobs"]:
            w.writerow([j["score"], j["company"], j["tier"], j["title"], j["location"], j["min_years"] or "",
                        "; ".join(j["skills"]), j["posted"] or "", j["url"]])
    return report


def cmd_run(a):
    raw = prof.load_profile_file(a.profile) if a.profile else {}
    if a.cv:
        p = Path(a.cv)
        info = prof.analyze_cv(prof.extract_text(p.read_bytes(), p.name))
        raw.setdefault("cv_skills", info["skills"])
        if not raw.get("skills"):
            raw["skills"] = info["skills"][:8]
        if raw.get("yoe") is None and info["yoe"]:
            raw["yoe"] = info["yoe"]
        if not raw.get("roles") and info["roles"]:
            raw["roles"] = info["roles"]
    for key, val in (("yoe", a.yoe), ("country", a.country), ("expected_lpa", a.lpa), ("skills", a.skills),
                     ("roles", a.roles), ("cities", a.cities), ("tiers", a.tiers), ("freshness_days", a.days),
                     ("min_score", a.min_score)):
        if val is not None:
            raw[key] = val
    if raw.get("yoe") is None:
        sys.exit("Experience is required: pass --yoe or set yoe in your profile file.")
    only = [x.strip() for x in a.only.split(",")] if a.only else None

    def progress(done, total, msg):
        print(f"\r  {msg[:78]:<78}", end="", flush=True)

    result = engine.run_hunt(raw, progress=progress, refresh=a.refresh, track_new=not a.no_track, only=only)
    print()
    report = write_report(result, a.out)
    s = result["stats"]
    print(f"{s['matches']} matches ({s['hot']} apply-now, {s['new']} new) from {s['sources_ok']}/{s['companies']} sources")
    for j in result["jobs"][: a.top]:
        print(f"  [{j['score']:>3}] {j['company']:<22} {j['title'][:55]:<55} {j['location'][:28]}")
    print(f"Report: {report}")
    if a.notify:
        from .notify import notify
        sent = notify(result)
        print("Alerts sent: " + (", ".join(sent) if sent else "none (nothing new, or no credentials configured)"))
    if a.open:
        webbrowser.open(report.resolve().as_uri())


def cmd_web(a):
    from .webapp import serve
    serve(a.host, a.port, not a.no_browser)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="jobhunter", description="Find and rank job openings from company career sites.")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("web", help="start the web UI")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8000)
    w.add_argument("--no-browser", action="store_true")
    w.set_defaults(fn=cmd_web)

    r = sub.add_parser("run", help="run a search from the command line and write an HTML/CSV report")
    r.add_argument("--profile", help="YAML profile file (see profile.example.yaml)")
    r.add_argument("--cv", help="path to your CV (pdf/docx/txt) to auto-detect skills")
    r.add_argument("--yoe", type=float)
    r.add_argument("--country")
    r.add_argument("--lpa", type=float, help="expected CTC in LPA (India)")
    r.add_argument("--skills", help="comma separated, most important first")
    r.add_argument("--roles", help="comma separated: backend,fullstack,frontend,mobile,data,ml,devops,qa,security,embedded")
    r.add_argument("--cities", help="comma separated")
    r.add_argument("--tiers", help="e.g. A,B,C")
    r.add_argument("--days", type=int, help="max posting age in days")
    r.add_argument("--min-score", type=int)
    r.add_argument("--only", help="comma separated company names (quick test)")
    r.add_argument("--out", default="output")
    r.add_argument("--top", type=int, default=15)
    r.add_argument("--refresh", action="store_true", help="ignore the 3-hour cache")
    r.add_argument("--no-track", action="store_true", help="do not remember jobs between runs")
    r.add_argument("--notify", action="store_true", help="send Telegram/email alert for new or apply-now jobs")
    r.add_argument("--open", action="store_true", help="open the report when done")
    r.set_defaults(fn=cmd_run)

    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
