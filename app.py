import fcntl
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from bson import ObjectId
from bson.errors import InvalidId
from flask import Flask, abort, jsonify, render_template, request
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

from tools import (
    enabled_resolvers,
    enabled_tools,
    enabled_probers,
    enabled_crawlers,
    resolvers_status,
    tools_status,
    probers_status,
)
from tools.runner import extract_scope_domains, run_discovery
from tools.fallparams import FallparamsTool

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")
DB_NAME = os.environ.get("DB_NAME", "bugbounty")

AUTO_RESUME_JOBS = os.environ.get("AUTO_RESUME_JOBS", "0") == "1"
RESUME_MAX_AGE_HOURS = int(os.environ.get("RESUME_MAX_AGE_HOURS", "24"))

app = Flask(__name__)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

client = MongoClient(MONGO_URI, tz_aware=True)
db = client[DB_NAME]
projects = db.projects


def now():
    return datetime.now(timezone.utc)


def as_utc(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# ── collections / indexes ────────────────────────────────────────────
projects.create_index("name", unique=True)
projects.create_index("cron_enabled")

scan_jobs    = db.scan_jobs
subdomains   = db.subdomains
dns_jobs     = db.dns_jobs
resolved     = db.resolved
httpx_jobs   = db.httpx_jobs
http_results = db.http_results
http_changes = db.http_changes
crawl_jobs   = db.crawl_jobs
crawl_results= db.crawl_results
param_jobs   = db.param_jobs
param_results= db.param_results

scan_jobs.create_index("project_id")
scan_jobs.create_index([("project_id", 1), ("status", 1)])
subdomains.create_index([("project_id", 1), ("hostname", 1)], unique=True)
subdomains.create_index("project_id")

dns_jobs.create_index("project_id")
dns_jobs.create_index([("project_id", 1), ("status", 1)])
resolved.create_index([("project_id", 1), ("hostname", 1)], unique=True)
resolved.create_index("project_id")

httpx_jobs.create_index("project_id")
httpx_jobs.create_index([("project_id", 1), ("status", 1)])
http_results.create_index([("project_id", 1), ("hostname", 1), ("scan_date", 1)], unique=True)
http_results.create_index("project_id")
http_results.create_index("scan_date")

http_changes.create_index("project_id")
http_changes.create_index([("project_id", 1), ("changed_at", -1)])

crawl_jobs.create_index("project_id")
crawl_jobs.create_index([("project_id", 1), ("status", 1)])
crawl_results.create_index([("project_id", 1), ("url", 1)], unique=True)
crawl_results.create_index("project_id")
crawl_results.create_index("hostname")
crawl_results.create_index("extension")

param_jobs.create_index("project_id")
param_jobs.create_index([("project_id", 1), ("status", 1)])
param_results.create_index([("project_id", 1), ("word", 1)], unique=True)
param_results.create_index("project_id")


def parse_scope(raw):
    lines = raw if isinstance(raw, list) else str(raw or "").splitlines()
    out, seen = [], set()
    for line in lines:
        item = line.strip()
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def project_to_dict(doc):
    return {
        "id": str(doc["_id"]),
        "name": doc.get("name", ""),
        "platform": doc.get("platform", ""),
        "program_url": doc.get("program_url", ""),
        "scope": doc.get("scope", []),
        "notes": doc.get("notes", ""),
        "created_at": doc["created_at"].isoformat() if doc.get("created_at") else None,
        "updated_at": doc["updated_at"].isoformat() if doc.get("updated_at") else None,
        "cron_enabled": doc.get("cron_enabled", False),
        "cron_interval": doc.get("cron_interval", ""),
        "cron_tools": doc.get("cron_tools", []),
        "cron_last_run": doc["cron_last_run"].isoformat() if doc.get("cron_last_run") else None,
        "cron_next_run": doc["cron_next_run"].isoformat() if doc.get("cron_next_run") else None,
    }


def get_project_or_none(pid):
    try:
        oid = ObjectId(pid)
    except (InvalidId, TypeError):
        return None
    return projects.find_one({"_id": oid})


def clean_payload(data):
    name = (data.get("name") or "").strip()
    if not name:
        return None, "Project name is required."
    return {
        "name": name,
        "platform": (data.get("platform") or "").strip(),
        "program_url": (data.get("program_url") or "").strip(),
        "scope": parse_scope(data.get("scope")),
        "notes": (data.get("notes") or "").strip(),
    }, None


# ---------------------------------------------------------------- pages

@app.get("/")
def index():
    docs = list(projects.find().sort("created_at", -1))
    return render_template("index.html", projects=[project_to_dict(d) for d in docs])


@app.get("/projects/<pid>")
def project_page(pid):
    doc = get_project_or_none(pid)
    if not doc:
        abort(404)
    return render_template("project.html", project=project_to_dict(doc))


# ---------------------------------------------------------------- API: projects

@app.post("/api/projects")
def create_project():
    payload, err = clean_payload(request.get_json(silent=True) or {})
    if err:
        return jsonify({"error": err}), 400
    payload["created_at"] = payload["updated_at"] = now()
    payload["cron_enabled"] = False
    try:
        res = projects.insert_one(payload)
    except DuplicateKeyError:
        return jsonify({"error": "A project with that name already exists."}), 409
    return jsonify(project_to_dict(projects.find_one({"_id": res.inserted_id}))), 201


@app.put("/api/projects/<pid>")
def update_project(pid):
    doc = get_project_or_none(pid)
    if not doc:
        return jsonify({"error": "Project not found."}), 404
    payload, err = clean_payload(request.get_json(silent=True) or {})
    if err:
        return jsonify({"error": err}), 400
    payload["updated_at"] = now()
    try:
        projects.update_one({"_id": doc["_id"]}, {"$set": payload})
    except DuplicateKeyError:
        return jsonify({"error": "A project with that name already exists."}), 409
    return jsonify(project_to_dict(projects.find_one({"_id": doc["_id"]})))


@app.delete("/api/projects/<pid>")
def delete_project(pid):
    doc = get_project_or_none(pid)
    if not doc:
        return jsonify({"error": "Project not found."}), 404
    projects.delete_one({"_id": doc["_id"]})
    subdomains.delete_many({"project_id": doc["_id"]})
    resolved.delete_many({"project_id": doc["_id"]})
    scan_jobs.delete_many({"project_id": doc["_id"]})
    dns_jobs.delete_many({"project_id": doc["_id"]})
    httpx_jobs.delete_many({"project_id": doc["_id"]})
    http_results.delete_many({"project_id": doc["_id"]})
    http_changes.delete_many({"project_id": doc["_id"]})
    crawl_jobs.delete_many({"project_id": doc["_id"]})
    crawl_results.delete_many({"project_id": doc["_id"]})
    param_jobs.delete_many({"project_id": doc["_id"]})
    param_results.delete_many({"project_id": doc["_id"]})
    return jsonify({"ok": True})


@app.put("/api/projects/<pid>/cron")
def api_update_cron(pid):
    doc = get_project_or_none(pid)
    if not doc:
        return jsonify({"error": "Project not found."}), 404

    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled", False))
    interval = data.get("interval", "")
    tools = data.get("tools", [])

    update_doc = {
        "cron_enabled": enabled,
        "cron_interval": interval,
        "cron_tools": tools,
        "cancel_cron_requested": False,
        "updated_at": now(),
    }

    if enabled and interval:
        intervals = {"6h": 6, "12h": 12, "daily": 24, "weekly": 168}
        hours = intervals.get(interval, 24)
        next_run = now() + timedelta(hours=hours)
        update_doc["cron_next_run"] = next_run
    else:
        update_doc["cron_next_run"] = None

    projects.update_one({"_id": doc["_id"]}, {"$set": update_doc})
    return jsonify(project_to_dict(projects.find_one({"_id": doc["_id"]})))


@app.delete("/api/projects/<pid>/cron")
def api_delete_cron(pid):
    doc = get_project_or_none(pid)
    if not doc:
        return jsonify({"error": "Project not found."}), 404

    projects.update_one(
        {"_id": doc["_id"]},
        {"$set": {
            "cron_enabled": False,
            "cron_next_run": None,
            "cancel_cron_requested": True,
            "updated_at": now()
        }}
    )
    return jsonify({"ok": True})


# ---------------------------------------------------------------- API: tools / resolvers / probers

@app.get("/api/tools")
def api_tools():
    return jsonify({"tools": tools_status()})

@app.get("/api/resolvers")
def api_resolvers():
    return jsonify({"resolvers": resolvers_status()})

@app.get("/api/probers")
def api_probers():
    return jsonify({"probers": probers_status()})


# ---------------------------------------------------------------- API: subdomains

@app.get("/api/projects/<pid>/subdomains")
def api_list_subdomains(pid):
    doc = get_project_or_none(pid)
    if not doc:
        return jsonify({"error": "Project not found."}), 404

    docs = list(subdomains.find({"project_id": doc["_id"]}).sort("hostname", 1))
    running = scan_jobs.find_one({"project_id": doc["_id"], "status": "running"})

    return jsonify({
        "count": len(docs),
        "subdomains": [{
            "id": str(d["_id"]),
            "hostname": d["hostname"],
            "sources": d.get("sources", []),
            "first_seen": d["first_seen"].isoformat() if d.get("first_seen") else None,
            "last_seen":  d["last_seen"].isoformat()  if d.get("last_seen")  else None,
        } for d in docs],
        "running_job_id": str(running["_id"]) if running else None,
    })


@app.delete("/api/projects/<pid>/subdomains")
def api_delete_subdomains(pid):
    doc = get_project_or_none(pid)
    if not doc:
        return jsonify({"error": "Project not found."}), 404

    body = request.get_json(silent=True) or {}

    if body.get("all"):
        res = subdomains.delete_many({"project_id": doc["_id"]})
        resolved.delete_many({"project_id": doc["_id"]})
        http_results.delete_many({"project_id": doc["_id"]})
        http_changes.delete_many({"project_id": doc["_id"]})
        crawl_results.delete_many({"project_id": doc["_id"]})
        return jsonify({"deleted": res.deleted_count, "all": True})

    raw_ids = body.get("ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        return jsonify({"error": "No IDs provided."}), 400

    oids = []
    for raw in raw_ids:
        try:
            oids.append(ObjectId(str(raw)))
        except (InvalidId, TypeError):
            continue
    if not oids:
        return jsonify({"error": "No valid IDs in request."}), 400

    victims = list(subdomains.find(
        {"project_id": doc["_id"], "_id": {"$in": oids}},
        {"hostname": 1},
    ))
    victim_hosts = [v["hostname"] for v in victims]

    res = subdomains.delete_many({"project_id": doc["_id"], "_id": {"$in": oids}})

    if victim_hosts:
        resolved.delete_many({"project_id": doc["_id"], "hostname": {"$in": victim_hosts}})
        http_results.delete_many({"project_id": doc["_id"], "hostname": {"$in": victim_hosts}})
        http_changes.delete_many({"project_id": doc["_id"], "hostname": {"$in": victim_hosts}})
        crawl_results.delete_many({"project_id": doc["_id"], "hostname": {"$in": victim_hosts}})

    return jsonify({"deleted": res.deleted_count})


# ---------------------------------------------------------------- API: discovery

@app.post("/api/projects/<pid>/discovery")
def api_start_discovery(pid):
    doc = get_project_or_none(pid)
    if not doc:
        return jsonify({"error": "Project not found."}), 404

    existing = scan_jobs.find_one({"project_id": doc["_id"], "status": "running"})
    if existing:
        return jsonify({"error": "A discovery job is already running.",
                        "job_id": str(existing["_id"])}), 409

    domains = extract_scope_domains(doc.get("scope", []))
    if not domains:
        return jsonify({"error": "No valid domains in project scope."}), 400

    available = enabled_tools()
    if not available:
        return jsonify({"error": "No discovery tools available."}), 400

    body = request.get_json(silent=True) or {}
    if "tools" in body:
        requested = body.get("tools") or []
        available_names = {t.name for t in available}
        unknown = [n for n in requested if n not in available_names]
        if unknown:
            return jsonify({"error": f"Unknown or unavailable tools: {', '.join(unknown)}"}), 400
        tools_now = [t for t in available if t.name in requested]
        if not tools_now:
            return jsonify({"error": "No tools selected."}), 400
    else:
        tools_now = available

    job_doc = {
        "project_id": doc["_id"],
        "status": "running",
        "started_at": now(),
        "finished_at": None,
        "domains": domains,
        "total_domains": len(domains),
        "processed_domains": 0,
        "current_domain": None,
        "found_count": 0,
        "new_count": 0,
        "tools": [t.name for t in tools_now],
        "error": None,
        "errors": [],
    }
    job_id = scan_jobs.insert_one(job_doc).inserted_id

    threading.Thread(
        target=_run_discovery_job,
        args=(job_id, doc["_id"], domains, [t.name for t in tools_now]),
        daemon=True,
    ).start()

    return jsonify({"job_id": str(job_id)}), 202


@app.get("/api/projects/<pid>/discovery/jobs/<job_id>")
def api_job_status(pid, job_id):
    doc = get_project_or_none(pid)
    if not doc:
        return jsonify({"error": "Project not found."}), 404
    try:
        jid = ObjectId(job_id)
    except (InvalidId, TypeError):
        return jsonify({"error": "Invalid job id."}), 400

    job = scan_jobs.find_one({"_id": jid, "project_id": doc["_id"]})
    if not job:
        return jsonify({"error": "Job not found."}), 404

    return jsonify({
        "job_id": str(job["_id"]),
        "status": job["status"],
        "started_at": job["started_at"].isoformat() if job.get("started_at") else None,
        "finished_at": job["finished_at"].isoformat() if job.get("finished_at") else None,
        "domains": job.get("domains", []),
        "total_domains": job.get("total_domains", 0),
        "processed_domains": job.get("processed_domains", 0),
        "current_domain": job.get("current_domain"),
        "found_count": job.get("found_count", 0),
        "new_count": job.get("new_count", 0),
        "tools": job.get("tools", []),
        "error": job.get("error"),
        "errors": job.get("errors", []),
    })


def _run_discovery_job(job_id, project_id, domains, tool_names):
    job = scan_jobs.find_one({"_id": job_id}) or {}
    start_idx = int(job.get("processed_domains", 0))
    errors = list(job.get("errors", []))
    new_count = int(job.get("new_count", 0))

    remaining = domains[start_idx:]
    if not remaining:
        scan_jobs.update_one({"_id": job_id}, {"$set": {
            "status": "done",
            "finished_at": now(),
            "current_domain": None,
            "processed_domains": len(domains),
            "errors": errors,
        }})
        return

    processed = start_idx

    def on_host_found(host, tool_name):
        nonlocal new_count
        ts = now()
        r = subdomains.update_one(
            {"project_id": project_id, "hostname": host},
            {
                "$set": {"last_seen": ts, "last_scan_job_id": job_id},
                "$setOnInsert": {
                    "project_id": project_id,
                    "hostname": host,
                    "first_seen": ts,
                },
                "$addToSet": {"sources": tool_name},
            },
            upsert=True,
        )
        if r.upserted_id is not None:
            new_count += 1

    def on_start(domain):
        log.info("Discovery job %s: scanning %s", job_id, domain)
        scan_jobs.update_one({"_id": job_id}, {"$set": {"current_domain": domain}})

    def on_done(domain, hosts):
        nonlocal processed
        processed += 1
        found_total = subdomains.count_documents({"project_id": project_id})
        scan_jobs.update_one({"_id": job_id}, {"$set": {
            "processed_domains": processed,
            "found_count": found_total,
            "new_count": new_count,
            "current_domain": None,
            "errors": errors,
        }})

    try:
        run_discovery(
            domains=remaining,
            tools=tool_names,
            on_domain_start=on_start,
            on_domain_done=on_done,
            on_host_found=on_host_found,
        )
        scan_jobs.update_one({"_id": job_id}, {"$set": {
            "status": "done",
            "finished_at": now(),
            "current_domain": None,
            "errors": errors,
        }})
    except Exception as exc:
        log.exception("Discovery job %s failed", job_id)
        scan_jobs.update_one({"_id": job_id}, {"$set": {
            "status": "failed",
            "finished_at": now(),
            "error": str(exc),
            "errors": errors,
        }})


# ---------------------------------------------------------------- API: DNS resolution

@app.get("/api/projects/<pid>/resolved")
def api_list_resolved(pid):
    doc = get_project_or_none(pid)
    if not doc:
        return jsonify({"error": "Project not found."}), 404

    docs = list(resolved.find({"project_id": doc["_id"]}).sort("hostname", 1))
    running = dns_jobs.find_one({"project_id": doc["_id"], "status": "running"})

    return jsonify({
        "count": len(docs),
        "resolved": [{
            "id": str(d["_id"]),
            "hostname": d["hostname"],
            "a":     d.get("a", []),
            "aaaa":  d.get("aaaa", []),
            "cname": d.get("cname", []),
            "sources": d.get("subdomain_sources", []),
            "first_resolved_at": d["first_resolved_at"].isoformat() if d.get("first_resolved_at") else None,
            "last_resolved_at":  d["last_resolved_at"].isoformat()  if d.get("last_resolved_at")  else None,
        } for d in docs],
        "running_job_id": str(running["_id"]) if running else None,
    })


@app.post("/api/projects/<pid>/dns-resolve")
def api_start_dns_resolve(pid):
    doc = get_project_or_none(pid)
    if not doc:
        return jsonify({"error": "Project not found."}), 404

    existing = dns_jobs.find_one({"project_id": doc["_id"], "status": "running"})
    if existing:
        return jsonify({"error": "A DNS resolution job is already running.",
                        "job_id": str(existing["_id"])}), 409

    sub_docs = list(subdomains.find(
        {"project_id": doc["_id"]},
        {"hostname": 1, "sources": 1},
    ))
    if not sub_docs:
        return jsonify({"error": "No subdomains to resolve — run subdomain discovery first."}), 400

    resolvers = enabled_resolvers()
    if not resolvers:
        return jsonify({"error": "dnsx not found."}), 400

    job_doc = {
        "project_id": doc["_id"],
        "status": "running",
        "started_at": now(),
        "finished_at": None,
        "total_hosts": len(sub_docs),
        "resolved": 0,
        "failed": 0,
        "new_count": 0,
        "updated_count": 0,
        "resolver": resolvers[0].name,
        "error": None,
    }
    job_id = dns_jobs.insert_one(job_doc).inserted_id

    hostnames = [d["hostname"] for d in sub_docs]
    sources_map = {d["hostname"]: d.get("sources", []) for d in sub_docs}

    threading.Thread(
        target=_run_dns_job,
        args=(job_id, doc["_id"], hostnames, sources_map),
        daemon=True,
    ).start()

    return jsonify({"job_id": str(job_id)}), 202


@app.get("/api/projects/<pid>/dns-resolve/jobs/<job_id>")
def api_dns_job_status(pid, job_id):
    doc = get_project_or_none(pid)
    if not doc:
        return jsonify({"error": "Project not found."}), 404
    try:
        jid = ObjectId(job_id)
    except (InvalidId, TypeError):
        return jsonify({"error": "Invalid job id."}), 400

    job = dns_jobs.find_one({"_id": jid, "project_id": doc["_id"]})
    if not job:
        return jsonify({"error": "Job not found."}), 404

    return jsonify({
        "job_id": str(job["_id"]),
        "status": job["status"],
        "started_at": job["started_at"].isoformat() if job.get("started_at") else None,
        "finished_at": job["finished_at"].isoformat() if job.get("finished_at") else None,
        "total_hosts": job.get("total_hosts", 0),
        "resolved": job.get("resolved", 0),
        "failed": job.get("failed", 0),
        "new_count": job.get("new_count", 0),
        "updated_count": job.get("updated_count", 0),
        "error": job.get("error"),
    })


@app.delete("/api/projects/<pid>/resolved")
def api_delete_resolved(pid):
    doc = get_project_or_none(pid)
    if not doc:
        return jsonify({"error": "Project not found."}), 404

    body = request.get_json(silent=True) or {}

    if body.get("all"):
        res = resolved.delete_many({"project_id": doc["_id"]})
        return jsonify({"deleted": res.deleted_count, "all": True})

    raw_ids = body.get("ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        return jsonify({"error": "No IDs provided."}), 400

    oids = []
    for raw in raw_ids:
        try:
            oids.append(ObjectId(str(raw)))
        except (InvalidId, TypeError):
            continue
    if not oids:
        return jsonify({"error": "No valid IDs in request."}), 400

    res = resolved.delete_many({"project_id": doc["_id"], "_id": {"$in": oids}})
    return jsonify({"deleted": res.deleted_count})


def _run_dns_job(job_id, project_id, hostnames, sources_map):
    def update(**fields):
        dns_jobs.update_one({"_id": job_id}, {"$set": fields})

    try:
        resolvers = enabled_resolvers()
        if not resolvers:
            raise RuntimeError("No resolver available at job start")
        resolver = resolvers[0]

        results = resolver.resolve(hostnames, timeout=600)

        ts = now()
        new_count = 0
        updated_count = 0

        for host, info in results.items():
            r = resolved.update_one(
                {"project_id": project_id, "hostname": host},
                {
                    "$set": {
                        "a":     info["a"],
                        "aaaa":  info["aaaa"],
                        "cname": info["cname"],
                        "subdomain_sources": sources_map.get(host, []),
                        "last_resolved_at": ts,
                        "last_dns_job_id": job_id,
                    },
                    "$setOnInsert": {
                        "project_id": project_id,
                        "hostname": host,
                        "first_resolved_at": ts,
                    },
                },
                upsert=True,
            )
            if r.upserted_id is not None:
                new_count += 1
            else:
                updated_count += 1

        update(
            status="done",
            finished_at=now(),
            resolved=len(results),
            failed=len(hostnames) - len(results),
            new_count=new_count,
            updated_count=updated_count,
        )
    except Exception as exc:
        log.exception("DNS job %s failed", job_id)
        update(status="failed", finished_at=now(), error=str(exc))


# ---------------------------------------------------------------- API: HTTP Discovery

@app.get("/api/projects/<pid>/httpx")
def api_list_httpx(pid):
    doc = get_project_or_none(pid)
    if not doc:
        return jsonify({"error": "Project not found."}), 404

    docs = list(http_results.find({"project_id": doc["_id"]}).sort("created_at", -1))
    running = httpx_jobs.find_one({"project_id": doc["_id"], "status": "running"})

    dates = list(http_results.distinct("scan_date", {"project_id": doc["_id"]}))
    dates.sort(reverse=True)

    return jsonify({
        "count": len(docs),
        "results": [{
            "id": str(d["_id"]),
            "hostname": d["hostname"],
            "url": d.get("url", ""),
            "status_code": d.get("status_code", 0),
            "title": d.get("title", ""),
            "tech": d.get("tech", []),
            "server": d.get("server", ""),
            "ip": d.get("ip", ""),
            "cname": d.get("cname", ""),
            "scan_date": d.get("scan_date", ""),
            "scan_job_id": str(d.get("scan_job_id", "")),
        } for d in docs],
        "running_job_id": str(running["_id"]) if running else None,
        "scan_dates": dates,
    })


@app.post("/api/projects/<pid>/httpx")
def api_start_httpx(pid):
    doc = get_project_or_none(pid)
    if not doc:
        return jsonify({"error": "Project not found."}), 404

    existing = httpx_jobs.find_one({"project_id": doc["_id"], "status": "running"})
    if existing:
        return jsonify({"error": "An HTTP probing job is already running.",
                        "job_id": str(existing["_id"])}), 409

    resolved_docs = list(resolved.find({"project_id": doc["_id"]}, {"hostname": 1}))
    if not resolved_docs:
        return jsonify({"error": "No resolved subdomains — run DNS resolution first."}), 400

    hostnames = [d["hostname"] for d in resolved_docs]

    probers = enabled_probers()
    if not probers:
        return jsonify({"error": "httpx not found."}), 400

    scan_date = now().strftime("%Y-%m-%d")
    job_doc = {
        "project_id": doc["_id"],
        "status": "running",
        "started_at": now(),
        "finished_at": None,
        "total_hosts": len(hostnames),
        "probed": 0,
        "failed": 0,
        "new_count": 0,
        "updated_count": 0,
        "prober": probers[0].name,
        "scan_date": scan_date,
        "error": None,
    }
    job_id = httpx_jobs.insert_one(job_doc).inserted_id

    threading.Thread(
        target=_run_httpx_job,
        args=(job_id, doc["_id"], hostnames, scan_date),
        daemon=True,
    ).start()

    return jsonify({"job_id": str(job_id)}), 202


@app.get("/api/projects/<pid>/httpx/jobs/<job_id>")
def api_httpx_job_status(pid, job_id):
    doc = get_project_or_none(pid)
    if not doc:
        return jsonify({"error": "Project not found."}), 404
    try:
        jid = ObjectId(job_id)
    except (InvalidId, TypeError):
        return jsonify({"error": "Invalid job id."}), 400

    job = httpx_jobs.find_one({"_id": jid, "project_id": doc["_id"]})
    if not job:
        return jsonify({"error": "Job not found."}), 404

    return jsonify({
        "job_id": str(job["_id"]),
        "status": job["status"],
        "started_at": job["started_at"].isoformat() if job.get("started_at") else None,
        "finished_at": job["finished_at"].isoformat() if job.get("finished_at") else None,
        "total_hosts": job.get("total_hosts", 0),
        "probed": job.get("probed", 0),
        "failed": job.get("failed", 0),
        "new_count": job.get("new_count", 0),
        "updated_count": job.get("updated_count", 0),
        "error": job.get("error"),
    })


@app.delete("/api/projects/<pid>/httpx")
def api_delete_httpx(pid):
    doc = get_project_or_none(pid)
    if not doc:
        return jsonify({"error": "Project not found."}), 404

    body = request.get_json(silent=True) or {}

    if body.get("all"):
        res = http_results.delete_many({"project_id": doc["_id"]})
        return jsonify({"deleted": res.deleted_count, "all": True})

    raw_ids = body.get("ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        return jsonify({"error": "No IDs provided."}), 400

    oids = []
    for raw in raw_ids:
        try:
            oids.append(ObjectId(str(raw)))
        except (InvalidId, TypeError):
            continue
    if not oids:
        return jsonify({"error": "No valid IDs in request."}), 400

    res = http_results.delete_many({"project_id": doc["_id"], "_id": {"$in": oids}})
    return jsonify({"deleted": res.deleted_count})


@app.get("/api/projects/<pid>/httpx/changes")
def api_list_httpx_changes(pid):
    doc = get_project_or_none(pid)
    if not doc:
        return jsonify({"error": "Project not found."}), 404

    docs = list(http_changes.find({"project_id": doc["_id"]}).sort("changed_at", -1).limit(500))
    return jsonify({
        "count": len(docs),
        "changes": [{
            "id": str(d["_id"]),
            "hostname": d["hostname"],
            "field": d["field"],
            "old_value": d["old_value"],
            "new_value": d["new_value"],
            "changed_at": d["changed_at"].isoformat() if d.get("changed_at") else None,
        } for d in docs]
    })


@app.delete("/api/projects/<pid>/httpx/changes")
def api_delete_httpx_changes(pid):
    doc = get_project_or_none(pid)
    if not doc:
        return jsonify({"error": "Project not found."}), 404
    res = http_changes.delete_many({"project_id": doc["_id"]})
    return jsonify({"deleted": res.deleted_count, "all": True})


def _run_httpx_job(job_id, project_id, hostnames, scan_date):
    def update(**fields):
        httpx_jobs.update_one({"_id": job_id}, {"$set": fields})

    try:
        probers = enabled_probers()
        if not probers:
            raise RuntimeError("No prober available at job start")
        prober = probers[0]

        log.info("HTTP job %s: probing %d hostnames", job_id, len(hostnames))
        results = prober.probe(hostnames, timeout=600)

        ts = now()
        new_count = 0
        updated_count = 0

        for r in results:
            r_doc = {
                "project_id": project_id,
                "hostname": r["hostname"],
                "url": r.get("url", ""),
                "status_code": r.get("status_code", 0),
                "title": r.get("title", ""),
                "tech": r.get("tech", []),
                "server": r.get("server", ""),
                "ip": r.get("ip", ""),
                "cname": r.get("cname", ""),
                "scan_date": scan_date,
                "scan_job_id": job_id,
                "created_at": ts,
            }
            
            prev = http_results.find_one(
                {"project_id": project_id, "hostname": r["hostname"]},
                sort=[("scan_date", -1), ("created_at", -1)]
            )
            
            if prev:
                prev_status = prev.get("status_code", 0)
                new_status = r.get("status_code", 0)
                if prev_status != new_status:
                    http_changes.insert_one({
                        "project_id": project_id, "hostname": r["hostname"],
                        "field": "status", "old_value": str(prev_status), "new_value": str(new_status),
                        "changed_at": ts, "scan_job_id": job_id
                    })
                
                prev_title = prev.get("title") or ""
                new_title = r.get("title") or ""
                if prev_title != new_title:
                    http_changes.insert_one({
                        "project_id": project_id, "hostname": r["hostname"],
                        "field": "title", "old_value": prev_title, "new_value": new_title,
                        "changed_at": ts, "scan_job_id": job_id
                    })
                
                prev_tech = set(prev.get("tech") or [])
                new_tech = set(r.get("tech") or [])
                if prev_tech != new_tech:
                    http_changes.insert_one({
                        "project_id": project_id, "hostname": r["hostname"],
                        "field": "tech", "old_value": ", ".join(sorted(prev_tech)), "new_value": ", ".join(sorted(new_tech)),
                        "changed_at": ts, "scan_job_id": job_id
                    })

            res = http_results.update_one(
                {"project_id": project_id, "hostname": r["hostname"], "scan_date": scan_date},
                {"$set": r_doc}, upsert=True
            )
            if res.upserted_id is not None:
                new_count += 1
            else:
                updated_count += 1

        update(
            status="done", finished_at=now(), probed=len(results),
            failed=len(hostnames) - len(results), new_count=new_count, updated_count=updated_count
        )
    except Exception as exc:
        log.exception("HTTP job %s failed", job_id)
        update(status="failed", finished_at=now(), error=str(exc))


# ---------------------------------------------------------------- API: Crawl

@app.get("/api/projects/<pid>/crawl")
def api_list_crawl(pid):
    doc = get_project_or_none(pid)
    if not doc:
        return jsonify({"error": "Project not found."}), 404

    docs = list(crawl_results.find({"project_id": doc["_id"]}).sort("url", 1).limit(3000))
    running = crawl_jobs.find_one({"project_id": doc["_id"], "status": "running"})
    
    domains = list(crawl_results.distinct("hostname", {"project_id": doc["_id"]}))
    domains.sort()

    return jsonify({
        "count": len(docs),
        "results": [{
            "id": str(d["_id"]),
            "url": d["url"],
            "hostname": d.get("hostname", ""),
            "extension": d.get("extension", ""),
            "source": d.get("source", ""),
        } for d in docs],
        "running_job_id": str(running["_id"]) if running else None,
        "domains": domains
    })

@app.post("/api/projects/<pid>/crawl")
def api_start_crawl(pid):
    doc = get_project_or_none(pid)
    if not doc:
        return jsonify({"error": "Project not found."}), 404

    existing = crawl_jobs.find_one({"project_id": doc["_id"], "status": "running"})
    if existing:
        return jsonify({"error": "A crawl job is already running.",
                        "job_id": str(existing["_id"])}), 409

    body = request.get_json(silent=True) or {}
    urls = body.get("urls", [])
    if not urls:
        return jsonify({"error": "No URLs provided to crawl."}), 400

    crawlers = enabled_crawlers()
    if not crawlers:
        return jsonify({"error": "waybackurls and katana not found."}), 400

    katana_opts = body.get("katana_opts", {})
    custom_headers = body.get("custom_headers", [])

    job_doc = {
        "project_id": doc["_id"],
        "status": "running",
        "started_at": now(),
        "finished_at": None,
        "total_urls": len(urls),
        "processed": 0,
        "found_count": 0,
        "katana_opts": katana_opts,
        "custom_headers": custom_headers,
        "error": None,
    }
    job_id = crawl_jobs.insert_one(job_doc).inserted_id

    threading.Thread(
        target=_run_crawl_job,
        args=(job_id, doc["_id"], urls, katana_opts, custom_headers),
        daemon=True,
    ).start()

    return jsonify({"job_id": str(job_id)}), 202

@app.get("/api/projects/<pid>/crawl/jobs/<job_id>")
def api_crawl_job_status(pid, job_id):
    doc = get_project_or_none(pid)
    if not doc:
        return jsonify({"error": "Project not found."}), 404
    try:
        jid = ObjectId(job_id)
    except (InvalidId, TypeError):
        return jsonify({"error": "Invalid job id."}), 400

    job = crawl_jobs.find_one({"_id": jid, "project_id": doc["_id"]})
    if not job:
        return jsonify({"error": "Job not found."}), 404

    return jsonify({
        "job_id": str(job["_id"]),
        "status": job["status"],
        "started_at": job["started_at"].isoformat() if job.get("started_at") else None,
        "finished_at": job["finished_at"].isoformat() if job.get("finished_at") else None,
        "total_urls": job.get("total_urls", 0),
        "processed": job.get("processed", 0),
        "found_count": job.get("found_count", 0),
        "error": job.get("error"),
    })

@app.delete("/api/projects/<pid>/crawl")
def api_delete_crawl(pid):
    doc = get_project_or_none(pid)
    if not doc:
        return jsonify({"error": "Project not found."}), 404

    body = request.get_json(silent=True) or {}

    if body.get("all"):
        res = crawl_results.delete_many({"project_id": doc["_id"]})
        return jsonify({"deleted": res.deleted_count, "all": True})

    raw_ids = body.get("ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        return jsonify({"error": "No IDs provided."}), 400

    oids = []
    for raw in raw_ids:
        try:
            oids.append(ObjectId(str(raw)))
        except (InvalidId, TypeError):
            continue
    if not oids:
        return jsonify({"error": "No valid IDs in request."}), 400

    res = crawl_results.delete_many({"project_id": doc["_id"], "_id": {"$in": oids}})
    return jsonify({"deleted": res.deleted_count})

def _run_crawl_job(job_id, project_id, urls, katana_opts, custom_headers):
    def update(**fields):
        crawl_jobs.update_one({"_id": job_id}, {"$set": fields})

    try:
        crawlers = enabled_crawlers()
        if not crawlers:
            raise RuntimeError("No crawlers available")

        ts = now()
        found_urls = set()
        update_counter = 0
        
        def handle_result(r):
            nonlocal update_counter
            found_urls.add(r["url"])
            parsed = urlparse(r["url"])
            hostname = parsed.hostname or ""
            ext = os.path.splitext(parsed.path)[1].lstrip(".").lower()
            
            if hostname:
                crawl_results.update_one(
                    {"project_id": project_id, "url": r["url"]},
                    {
                        "$set": {
                            "last_seen": ts,
                            "source": r["source"]
                        },
                        "$setOnInsert": {
                            "project_id": project_id,
                            "url": r["url"],
                            "hostname": hostname,
                            "extension": ext,
                            "first_seen": ts,
                        }
                    },
                    upsert=True
                )
                update_counter += 1
                if update_counter % 10 == 0:
                    update(found_count=len(found_urls))

        for crawler in crawlers:
            log.info("Crawl job %s: running %s", job_id, crawler.name)
            try:
                crawler.crawl(
                    urls, 
                    timeout=600, 
                    on_result=handle_result, 
                    katana_opts=katana_opts, 
                    custom_headers=custom_headers
                )
            except Exception as e:
                log.error("Crawler %s failed: %s", crawler.name, e)
        
        update(
            status="done",
            finished_at=now(),
            found_count=len(found_urls),
        )
    except Exception as exc:
        log.exception("Crawl job %s failed", job_id)
        update(status="failed", finished_at=now(), error=str(exc))


# ---------------------------------------------------------------- API: Parameter Discovery

@app.post("/api/projects/<pid>/params/scan")
def api_start_params_scan(pid):
    doc = get_project_or_none(pid)
    if not doc:
        return jsonify({"error": "Project not found."}), 404

    existing = param_jobs.find_one({"project_id": doc["_id"], "status": "running"})
    if existing:
        return jsonify({"error": "A parameter scan is already running.", "job_id": str(existing["_id"])}), 409

    body = request.get_json(silent=True) or {}
    urls = body.get("urls", [])
    if not urls:
        return jsonify({"error": "No URLs provided to analyze."}), 400

    job_doc = {
        "project_id": doc["_id"],
        "status": "running",
        "started_at": now(),
        "finished_at": None,
        "total_urls": len(urls),
        "found_count": 0,
        "error": None,
    }
    job_id = param_jobs.insert_one(job_doc).inserted_id

    threading.Thread(
        target=_run_params_job,
        args=(job_id, doc["_id"], urls),
        daemon=True,
    ).start()

    return jsonify({"job_id": str(job_id)}), 202

@app.get("/api/projects/<pid>/params/jobs/<job_id>")
def api_params_job_status(pid, job_id):
    doc = get_project_or_none(pid)
    if not doc:
        return jsonify({"error": "Project not found."}), 404
    try:
        jid = ObjectId(job_id)
    except (InvalidId, TypeError):
        return jsonify({"error": "Invalid job id."}), 400

    job = param_jobs.find_one({"_id": jid, "project_id": doc["_id"]})
    if not job:
        return jsonify({"error": "Job not found."}), 404

    return jsonify({
        "job_id": str(job["_id"]),
        "status": job["status"],
        "started_at": job["started_at"].isoformat() if job.get("started_at") else None,
        "finished_at": job["finished_at"].isoformat() if job.get("finished_at") else None,
        "total_urls": job.get("total_urls", 0),
        "found_count": job.get("found_count", 0),
        "error": job.get("error"),
    })

@app.get("/api/projects/<pid>/params")
def api_list_params(pid):
    doc = get_project_or_none(pid)
    if not doc:
        return jsonify({"error": "Project not found."}), 404
    
    q = request.args.get("q", "")
    query = {"project_id": doc["_id"]}
    if q:
        query["word"] = {"$regex": q, "$options": "i"}
        
    docs = list(param_results.find(query).sort("word", 1))
    return jsonify({
        "count": len(docs),
        "params": [d["word"] for d in docs]
    })

@app.post("/api/projects/<pid>/params")
def api_add_param(pid):
    doc = get_project_or_none(pid)
    if not doc:
        return jsonify({"error": "Project not found."}), 404
    
    body = request.get_json(silent=True) or {}
    word = (body.get("word") or "").strip()
    if not word:
        return jsonify({"error": "Parameter word is required."}), 400
        
    try:
        param_results.update_one(
            {"project_id": doc["_id"], "word": word},
            {"$setOnInsert": {"project_id": doc["_id"], "word": word, "created_at": now()}},
            upsert=True
        )
    except DuplicateKeyError:
        return jsonify({"error": "Parameter already exists."}), 409
        
    return jsonify({"ok": True, "word": word}), 201

@app.delete("/api/projects/<pid>/params")
def api_delete_params(pid):
    doc = get_project_or_none(pid)
    if not doc:
        return jsonify({"error": "Project not found."}), 404

    body = request.get_json(silent=True) or {}
    
    if body.get("all"):
        res = param_results.delete_many({"project_id": doc["_id"]})
        return jsonify({"deleted": res.deleted_count, "all": True})

    words = body.get("words", [])
    if not isinstance(words, list) or not words:
        return jsonify({"error": "No words provided."}), 400

    res = param_results.delete_many({"project_id": doc["_id"], "word": {"$in": words}})
    return jsonify({"deleted": res.deleted_count})

def _run_params_job(job_id, project_id, urls):
    def update(**fields):
        param_jobs.update_one({"_id": job_id}, {"$set": fields})

    try:
        tool = FallparamsTool()
        log.info("Params job %s: running fallparams on %d URLs", job_id, len(urls))
        words = tool.discover(urls, timeout=1200)
        
        ts = now()
        new_count = 0
        
        for word in words:
            r = param_results.update_one(
                {"project_id": project_id, "word": word},
                {"$setOnInsert": {"project_id": project_id, "word": word, "created_at": ts}},
                upsert=True
            )
            if r.upserted_id is not None:
                new_count += 1

        update(
            status="done", 
            finished_at=now(), 
            found_count=new_count,
        )
    except Exception as exc:
        log.exception("Params job %s failed", job_id)
        update(status="failed", finished_at=now(), error=str(exc))


# ---------------------------------------------------------------- Cron Pipeline & Scheduler

def _run_cron_pipeline(project_id, tools):
    log.info("Cron Pipeline: Starting for project %s", project_id)

    def is_cancelled():
        p = projects.find_one({"_id": project_id})
        return not p or p.get("cancel_cron_requested", False)

    # 1. Discovery
    try:
        if not is_cancelled() and not scan_jobs.find_one({"project_id": project_id, "status": "running"}):
            doc = projects.find_one({"_id": project_id})
            domains = extract_scope_domains(doc.get("scope", []))
            if domains and tools:
                job_id = scan_jobs.insert_one({
                    "project_id": project_id, "status": "running", "started_at": now(),
                    "finished_at": None, "domains": domains, "total_domains": len(domains),
                    "processed_domains": 0, "current_domain": None, "found_count": 0,
                    "new_count": 0, "tools": tools, "error": None, "errors": []
                }).inserted_id
                _run_discovery_job(job_id, project_id, domains, tools)
    except Exception as e:
        log.error("Cron Discovery failed: %s", e)

    if is_cancelled():
        log.info("Cron Pipeline: Cancelled by user before DNS.")
        return

    # 2. DNS
    try:
        if not is_cancelled() and not dns_jobs.find_one({"project_id": project_id, "status": "running"}):
            sub_docs = list(subdomains.find({"project_id": project_id}, {"hostname": 1, "sources": 1}))
            if sub_docs:
                hostnames = [d["hostname"] for d in sub_docs]
                sources_map = {d["hostname"]: d.get("sources", []) for d in sub_docs}
                job_id = dns_jobs.insert_one({
                    "project_id": project_id, "status": "running", "started_at": now(),
                    "finished_at": None, "total_hosts": len(sub_docs), "resolved": 0,
                    "failed": 0, "new_count": 0, "updated_count": 0, "resolver": "dnsx", "error": None
                }).inserted_id
                _run_dns_job(job_id, project_id, hostnames, sources_map)
    except Exception as e:
        log.error("Cron DNS failed: %s", e)

    if is_cancelled():
        log.info("Cron Pipeline: Cancelled by user before HTTP.")
        return

    # 3. HTTP
    try:
        if not is_cancelled() and not httpx_jobs.find_one({"project_id": project_id, "status": "running"}):
            resolved_docs = list(resolved.find({"project_id": project_id}, {"hostname": 1}))
            if resolved_docs:
                hostnames = [d["hostname"] for d in resolved_docs]
                scan_date = now().strftime("%Y-%m-%d")
                job_id = httpx_jobs.insert_one({
                    "project_id": project_id, "status": "running", "started_at": now(),
                    "finished_at": None, "total_hosts": len(hostnames), "probed": 0,
                    "failed": 0, "new_count": 0, "updated_count": 0, "prober": "httpx",
                    "scan_date": scan_date, "error": None
                }).inserted_id
                _run_httpx_job(job_id, project_id, hostnames, scan_date)
    except Exception as e:
        log.error("Cron HTTP failed: %s", e)

    log.info("Cron Pipeline: Finished for project %s", project_id)


def _cron_scheduler_loop():
    while True:
        time.sleep(60)
        try:
            current_time = now()
            due_projects = list(projects.find({
                "cron_enabled": True,
                "cron_next_run": {"$lte": current_time, "$ne": None}
            }))
            
            for p in due_projects:
                pid = p["_id"]
                tools = p.get("cron_tools", [])
                interval = p.get("cron_interval", "daily")
                
                intervals = {"6h": 6, "12h": 12, "daily": 24, "weekly": 168}
                hours = intervals.get(interval, 24)
                next_run = current_time + timedelta(hours=hours)
                
                projects.update_one(
                    {"_id": pid}, 
                    {"$set": {"cron_last_run": current_time, "cron_next_run": next_run}}
                )
                
                threading.Thread(
                    target=_run_cron_pipeline, 
                    args=(pid, tools), 
                    daemon=True
                ).start()
                
        except Exception as e:
            log.error("Cron scheduler error: %s", e)


# ---------------------------------------------------------------- startup recovery

def _mark_interrupted_jobs():
    msg = "Interrupted by server restart."
    r1 = scan_jobs.update_many({"status": "running"}, {"$set": {"status": "failed", "finished_at": now(), "error": msg}})
    r2 = dns_jobs.update_many({"status": "running"}, {"$set": {"status": "failed", "finished_at": now(), "error": msg}})
    r3 = httpx_jobs.update_many({"status": "running"}, {"$set": {"status": "failed", "finished_at": now(), "error": msg}})
    r4 = crawl_jobs.update_many({"status": "running"}, {"$set": {"status": "failed", "finished_at": now(), "error": msg}})
    r5 = param_jobs.update_many({"status": "running"}, {"$set": {"status": "failed", "finished_at": now(), "error": msg}})
    if r1.modified_count or r2.modified_count or r3.modified_count or r4.modified_count or r5.modified_count:
        log.info("Marked interrupted jobs as failed")


def _resume_running_jobs():
    cutoff = now() - timedelta(hours=RESUME_MAX_AGE_HOURS)
    n_disc = n_dns = 0

    for job in scan_jobs.find({"status": "running"}):
        started = as_utc(job.get("started_at"))
        if started and started < cutoff:
            scan_jobs.update_one({"_id": job["_id"]}, {"$set": {"status": "failed", "finished_at": now(), "error": "Not resumed: older than max age"}})
            continue
        domains = job.get("domains") or []
        tools = job.get("tools") or None
        if not domains:
            scan_jobs.update_one({"_id": job["_id"]}, {"$set": {"status": "failed", "finished_at": now(), "error": "Cannot resume: no domains"}})
            continue
        log.info("Resuming discovery job %s", job["_id"])
        threading.Thread(target=_run_discovery_job, args=(job["_id"], job["project_id"], domains, tools), daemon=True).start()
        n_disc += 1

    for job in dns_jobs.find({"status": "running"}):
        started = as_utc(job.get("started_at"))
        if started and started < cutoff:
            dns_jobs.update_one({"_id": job["_id"]}, {"$set": {"status": "failed", "finished_at": now(), "error": "Not resumed: older than max age"}})
            continue
        sub_docs = list(subdomains.find({"project_id": job["project_id"]}, {"hostname": 1, "sources": 1}))
        if not sub_docs:
            dns_jobs.update_one({"_id": job["_id"]}, {"$set": {"status": "failed", "finished_at": now(), "error": "No subdomains to resolve"}})
            continue
        hostnames = [d["hostname"] for d in sub_docs]
        sources_map = {d["hostname"]: d.get("sources", []) for d in sub_docs}
        log.info("Resuming DNS job %s", job["_id"])
        threading.Thread(target=_run_dns_job, args=(job["_id"], job["project_id"], hostnames, sources_map), daemon=True).start()
        n_dns += 1

    for job in httpx_jobs.find({"status": "running"}):
        httpx_jobs.update_one({"_id": job["_id"]}, {"$set": {"status": "failed", "finished_at": now(), "error": "Not resumed: server restarted"}})

    for job in crawl_jobs.find({"status": "running"}):
        crawl_jobs.update_one({"_id": job["_id"]}, {"$set": {"status": "failed", "finished_at": now(), "error": "Not resumed: server restarted"}})

    for job in param_jobs.find({"status": "running"}):
        param_jobs.update_one({"_id": job["_id"]}, {"$set": {"status": "failed", "finished_at": now(), "error": "Not resumed: server restarted"}})

    if n_disc or n_dns:
        log.info("Resumed %d discovery + %d DNS job(s)", n_disc, n_dns)


def _startup_recovery():
    if AUTO_RESUME_JOBS:
        log.info("AUTO_RESUME_JOBS=1 → resuming interrupted jobs")
        _resume_running_jobs()
    else:
        _mark_interrupted_jobs()


# ---------------------------------------------------------------- instance lock

LOCK_FILE = os.path.join(os.environ.get("TMPDIR", "/tmp"), "bbtool-app.lock")
_lock_fh = None


def _acquire_lock():
    global _lock_fh
    _lock_fh = open(LOCK_FILE, "w")
    try:
        fcntl.flock(_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(f"✗ Another instance of app.py is running (lock held on {LOCK_FILE}).")
    _lock_fh.write(str(os.getpid()))
    _lock_fh.flush()
    log.info("Instance lock acquired (%s, pid=%d)", LOCK_FILE, os.getpid())


@app.errorhandler(404)
def not_found(_):
    return render_template("404.html"), 404


if __name__ == "__main__":
    _acquire_lock()
    _startup_recovery()
    
    threading.Thread(target=_cron_scheduler_loop, daemon=True).start()
    
    port = int(os.environ.get("PORT", 8000))
    app.run(debug=True, use_reloader=False, port=port)