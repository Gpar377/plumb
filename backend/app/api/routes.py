from __future__ import annotations

import csv
import datetime as dt
import io
import json
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import compiler, receipts, seed_data, serialize, services
from ..auth import can, can_view_cpr, current_user, require, require_author, require_view
from ..config import settings
from ..db import get_db
from ..engine import ml, runner
from ..engine.core import today
from ..llm import gateway
from ..models import AICall, ContentPR, CPRVersion, Fact, GuidelineImport, PolicyVersion, Rule, Setting, User, now

router = APIRouter(prefix="/api")

# ------------------------------------------------------------------ meta


@router.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "llm": gateway.status(), "ml": ml.status()}


@router.get("/meta")
def meta(db: Session = Depends(get_db)) -> dict[str, Any]:
    pv = runner.current_policy(db)
    demo = sorted(p.name for p in settings.demo_dir.glob("*") if p.suffix in {".md", ".pdf", ".docx", ".csv", ".txt"}) if settings.demo_dir.exists() else []
    return {"llm": gateway.status(), "ml": ml.status(), "today": today().isoformat(), "policy_version": pv.number,
            "users": [serialize.user(u) for u in db.scalars(select(User))], "samples": seed_data.SAMPLES, "demo_files": demo,
            "channels": services.CHANNELS, "author_types": services.AUTHOR_TYPES}


@router.get("/me")
def me(u: User = Depends(current_user)) -> dict[str, Any]:
    return {**serialize.user(u), "permissions": serialize.permissions(u)}


@router.get("/demo/{name}")
def demo_file(name: str):
    p = (settings.demo_dir / name).resolve()
    if p.parent != settings.demo_dir.resolve() or not p.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(p, filename=p.name)


# ------------------------------------------------------------------ content PRs


def _get(db: Session, key: str, u: User) -> ContentPR:
    c = db.scalar(select(ContentPR).where(ContentPR.key == key))
    if c is None:
        raise HTTPException(404, "Content PR not found.")
    require_view(u, c)
    return c


def _start_run(bg: BackgroundTasks, db: Session, v: CPRVersion) -> None:
    v.run_status, v.run_progress, v.run_error = "pending", {"step": "queued"}, ""
    db.commit()
    bg.add_task(runner.run_version, v.id)


@router.get("/home")
def home(u: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    users = {x.id: x for x in db.scalars(select(User))}
    allc = db.scalars(select(ContentPR).order_by(ContentPR.updated_at.desc())).all()
    visible = [c for c in allc if can_view_cpr(u, c)]
    from ..models import Review

    def approved_by_me(c: ContentPR) -> bool:
        v = services.latest(c)
        return db.scalar(select(Review).where(Review.version_id == v.id, Review.role == u.role, Review.action == "approve")) is not None

    awaiting = [c for c in visible if c.status == "in_review" and u.role in services.required_roles(c)]
    awaiting = [c for c in awaiting if not approved_by_me(c)]
    return {"counts": {"drafts": sum(1 for c in visible if c.status in {"draft", "changes_requested"}),
                       "in_review": sum(1 for c in visible if c.status == "in_review"),
                       "approved": sum(1 for c in visible if c.status == "approved"), "awaiting_me": len(awaiting)},
            "awaiting_me": [serialize.cpr_brief(c, users) for c in awaiting],
            "recent": [serialize.cpr_brief(c, users) for c in visible[:12]]}


@router.get("/cprs")
def list_cprs(status: Optional[str] = None, u: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    users = {x.id: x for x in db.scalars(select(User))}
    q = select(ContentPR).order_by(ContentPR.updated_at.desc())
    if status:
        q = q.where(ContentPR.status.in_(status.split(",")))
    return [serialize.cpr_brief(c, users) for c in db.scalars(q) if can_view_cpr(u, c)]


class CreateCPR(BaseModel):
    title: str = ""
    channel: str = "instagram"
    author_type: Optional[str] = None
    text: str
    confidential: bool = False


@router.post("/cprs")
def create(body: CreateCPR, bg: BackgroundTasks, u: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require(u, "cpr.create")
    c = services.create_cpr(db, u, title=body.title, channel=body.channel, author_type=body.author_type or u.author_type,
                            text=body.text, confidential=body.confidential)
    _start_run(bg, db, services.latest(c))
    return {"key": c.key}


@router.post("/cprs/upload")
async def create_upload(bg: BackgroundTasks, file: UploadFile = File(...), title: str = Form(""), channel: str = Form("web"),
                        author_type: str = Form(""), confidential: bool = Form(False), u: User = Depends(current_user),
                        db: Session = Depends(get_db)) -> dict[str, Any]:
    require(u, "cpr.create")
    data = await file.read()
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, "File too large (8 MB max).")
    name = file.filename or "upload.txt"
    ext = Path(name).suffix.lower()
    if ext not in {".pdf", ".docx", ".md", ".txt"}:
        raise HTTPException(400, "Upload a PDF, DOCX, Markdown or text file. (Images arrive in Round 3.)")
    try:
        text = compiler.extract_text(name, data)
    except Exception as e:
        raise HTTPException(400, f"Couldn't read that file: {e}")
    if not text.strip():
        raise HTTPException(400, "No text found in that file (scanned PDFs need OCR, planned for Round 3).")
    c = services.create_cpr(db, u, title=title or Path(name).stem, channel=channel, author_type=author_type or u.author_type,
                            text=text, source=ext.lstrip("."), filename=name, confidential=confidential)
    _start_run(bg, db, services.latest(c))
    return {"key": c.key}


@router.get("/cprs/{key}")
def detail(key: str, u: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return serialize.cpr_detail(db, _get(db, key, u), u)


@router.get("/cprs/{key}/status")
def run_status(key: str, u: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    c = _get(db, key, u)
    v = services.latest(c)
    return {"version": v.number, "run_status": v.run_status, "progress": v.run_progress or {}, "error": v.run_error}


@router.get("/cprs/{key}/versions/{n}")
def version(key: str, n: int, u: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    c = _get(db, key, u)
    v = next((x for x in c.versions if x.number == n), None)
    if v is None:
        raise HTTPException(404, "No such version.")
    users = {x.id: x for x in db.scalars(select(User))}
    return serialize.version_full(db, v, users)


@router.post("/cprs/{key}/run")
def rerun(key: str, bg: BackgroundTasks, u: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    c = _get(db, key, u)
    if c.status == "approved":
        raise HTTPException(409, "Approved content is locked.")
    v = services.latest(c)
    if v.run_status in {"pending", "running"}:
        raise HTTPException(409, "Checks are already running.")
    _start_run(bg, db, v)
    return {"ok": True}


class IntentBody(BaseModel):
    goal: str = ""
    audience: str = ""
    cta: str = ""
    key_message: str = ""
    must_keep: list[str] = Field(default_factory=list)


@router.put("/cprs/{key}/intent")
def put_intent(key: str, body: IntentBody, bg: BackgroundTasks, u: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    c = _get(db, key, u)
    require_author(u, c)
    services.update_intent(db, c, u, body.model_dump())
    v = services.latest(c)
    if v.run_status not in {"pending", "running"}:
        _start_run(bg, db, v)  # fixes are re-verified against the confirmed intent
    return {"ok": True}


class TextBody(BaseModel):
    text: str


@router.post("/cprs/{key}/versions")
def save_version(key: str, body: TextBody, bg: BackgroundTasks, u: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    c = _get(db, key, u)
    require_author(u, c)
    v = services.new_version(db, c, u, body.text, "edit")
    _start_run(bg, db, v)
    return {"version": v.number}


class FixItem(BaseModel):
    finding_id: int
    replacement: Optional[str] = None


class FixesBody(BaseModel):
    items: list[FixItem]


@router.post("/cprs/{key}/fixes")
def fixes(key: str, body: FixesBody, bg: BackgroundTasks, u: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    c = _get(db, key, u)
    require_author(u, c)
    v = services.apply_fixes(db, c, u, [i.model_dump() for i in body.items])
    _start_run(bg, db, v)
    return {"version": v.number}


class ReasonBody(BaseModel):
    reason: str = ""


@router.post("/cprs/{key}/findings/{fid}/dispute")
def dispute(key: str, fid: int, body: ReasonBody, u: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    c = _get(db, key, u)
    require_author(u, c)
    services.dispute(db, c, u, fid, body.reason)
    return {"ok": True}


class ResolveBody(BaseModel):
    action: str
    reason: str = ""


@router.post("/cprs/{key}/findings/{fid}/resolve")
def resolve(key: str, fid: int, body: ResolveBody, u: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    c = _get(db, key, u)
    services.resolve(db, c, u, fid, body.action, body.reason)
    return {"ok": True}


@router.post("/cprs/{key}/submit")
def submit(key: str, u: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    c = _get(db, key, u)
    require_author(u, c)
    services.submit(db, c, u)
    return {"ok": True}


class ReviewBody(BaseModel):
    action: str
    note: str = ""


@router.post("/cprs/{key}/review")
def review(key: str, body: ReviewBody, u: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    c = _get(db, key, u)
    services.review(db, c, u, body.action, body.note)
    return {"ok": True}


class CommentBody(BaseModel):
    body: str
    finding_id: Optional[int] = None


@router.post("/cprs/{key}/comments")
def add_comment(key: str, body: CommentBody, u: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    c = _get(db, key, u)
    services.comment(db, c, u, body.body, body.finding_id)
    return {"ok": True}


@router.get("/cprs/{key}/receipt")
def receipt(key: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    c = _get(db, key, u)
    if not c.receipt:
        raise HTTPException(404, "No receipt yet — receipts are issued on approval.")
    return JSONResponse(c.receipt, headers={"Content-Disposition": f'attachment; filename="{c.receipt["receipt_id"]}.json"'})


@router.post("/receipts/verify")
def verify_receipt(body: dict[str, Any]) -> dict[str, Any]:
    ok = receipts.verify(body)
    return {"valid": ok, "message": "Signature valid — this receipt was issued by this Plumb workspace and has not been altered." if ok
            else "Signature does not match — the receipt was modified or issued elsewhere."}


# ------------------------------------------------------------------ policy


@router.get("/policy")
def policy(u: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    versions = db.scalars(select(PolicyVersion).order_by(PolicyVersion.number.desc())).all()
    out_versions = []
    for i, pv in enumerate(versions):
        prev = versions[i + 1].rules if i + 1 < len(versions) else []
        out_versions.append({"number": pv.number, "note": pv.note, "published_by": pv.published_by, "published_at": serialize.iso(pv.published_at),
                             "is_current": pv.is_current, "rule_count": len(pv.rules), "diff": compiler.diff(prev, pv.rules)})
    rules = db.scalars(select(Rule).order_by(Rule.type, Rule.id)).all()
    imports = db.scalars(select(GuidelineImport).order_by(GuidelineImport.id.desc())).all()
    current = next((v for v in versions if v.is_current), None)
    pending = any(r.status == "proposed" for r in rules)
    snapshot = [{"key": r.key, "type": r.type, "title": r.title, "severity": r.severity, "params": r.params, "section": r.section,
                 "source_quote": r.source_quote} for r in rules if r.status == "approved"]
    d = compiler.diff(current.rules if current else [], snapshot)
    unpublished = d["added"] + d["removed"] + d["changed"]
    return {"versions": out_versions, "rules": [serialize.rule(r) for r in rules],
            "imports": [{"id": i.id, "filename": i.filename, "status": i.status, "proposed": i.proposed, "dropped_uncited": i.dropped_uncited,
                         "duplicates": i.duplicates, "error": i.error, "created_at": serialize.iso(i.created_at)} for i in imports],
            "pending_review": pending, "unpublished_changes": unpublished, "can_edit": can(u, "policy.edit")}


@router.post("/policy/import")
async def import_guideline(bg: BackgroundTasks, file: Optional[UploadFile] = File(None), text: str = Form(""), u: User = Depends(current_user),
                           db: Session = Depends(get_db)) -> dict[str, Any]:
    require(u, "policy.edit")
    if file is not None and file.filename:
        data = await file.read()
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(413, "File too large (8 MB max).")
        name = file.filename
        try:
            doc = compiler.extract_text(name, data)
        except Exception as e:
            raise HTTPException(400, f"Couldn't read that file: {e}")
    else:
        name, doc = "pasted-guidelines.txt", text
    if len(doc.strip()) < 40:
        raise HTTPException(400, "That guideline document looks empty.")
    imp = GuidelineImport(filename=name, text=doc[:60000], created_by=u.id)
    db.add(imp)
    db.commit()
    bg.add_task(compiler.compile_import, imp.id)
    return {"id": imp.id}


@router.get("/policy/imports/{iid}")
def get_import(iid: int, u: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    imp = db.get(GuidelineImport, iid)
    if imp is None:
        raise HTTPException(404, "Not found")
    rules = db.scalars(select(Rule).where(Rule.import_id == iid)).all()
    return {"id": imp.id, "filename": imp.filename, "status": imp.status, "proposed": imp.proposed, "dropped_uncited": imp.dropped_uncited,
            "duplicates": imp.duplicates, "error": imp.error, "rules": [serialize.rule(r) for r in rules]}


class RulePatch(BaseModel):
    status: Optional[str] = None
    severity: Optional[str] = None
    title: Optional[str] = None
    params: Optional[dict[str, Any]] = None


@router.patch("/policy/rules/{rid}")
def patch_rule(rid: int, body: RulePatch, u: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require(u, "policy.edit")
    r = db.get(Rule, rid)
    if r is None:
        raise HTTPException(404, "Rule not found")
    if body.status:
        if body.status not in {"approved", "rejected", "retired", "proposed"}:
            raise HTTPException(400, "Bad status")
        r.status, r.decided_by, r.decided_at = body.status, u.id, now()
    if body.severity:
        if body.severity not in {"block", "warn", "suggest"}:
            raise HTTPException(400, "Bad severity")
        if r.type == "voice" and body.severity == "block":
            raise HTTPException(400, "Voice rules can warn at most — tone alone never blocks publication.")
        r.severity = body.severity
    if body.title:
        r.title = body.title[:200]
    if body.params is not None:
        r.params = body.params
    db.commit()
    return serialize.rule(r)


class NewRule(BaseModel):
    type: str
    title: str
    severity: str = "warn"
    section: str = ""
    source_quote: str
    params: dict[str, Any]


@router.post("/policy/rules")
def new_rule(body: NewRule, u: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require(u, "policy.edit")
    if body.type not in compiler.TYPES:
        raise HTTPException(400, "Bad type")
    if len(body.source_quote.strip()) < 8:
        raise HTTPException(400, "Every rule needs the guideline text it comes from (no citation, no rule).")
    norm = compiler._normalize({**body.params, "type": body.type, "title": body.title, "severity": body.severity, "section": body.section,
                                 "source_quote": body.source_quote})
    if norm is None:
        raise HTTPException(400, "That rule has no checkable content (e.g. a term rule needs at least one pattern).")
    r = Rule(key=compiler.next_key(db, body.type), type=norm["type"], title=norm["title"], severity=norm["severity"], params=norm["params"],
             section=norm["section"], source_quote=norm["source_quote"], status="proposed", origin="manual")
    db.add(r)
    db.commit()
    return serialize.rule(r)


class PublishBody(BaseModel):
    note: str = ""


@router.post("/policy/publish")
def publish(body: PublishBody, u: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require(u, "policy.edit")
    pv = compiler.publish(db, u.id, body.note)
    return {"number": pv.number}


# ------------------------------------------------------------------ facts


@router.get("/facts")
def facts(u: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    day = today()
    return {"today": day.isoformat(), "can_edit": can(u, "facts.edit"),
            "facts": [serialize.fact(f, day) for f in db.scalars(select(Fact).order_by(Fact.key))]}


class FactBody(BaseModel):
    statement: str
    approved_phrasing: str = ""
    claim_class: str = "general"
    subject: str = ""
    value: Optional[float] = None
    unit: str = ""
    owner: str = ""
    evidence: str = ""
    valid_from: Optional[dt.date] = None
    valid_until: Optional[dt.date] = None
    superseded_by: Optional[str] = None


def _next_fact_key(db: Session) -> str:
    nums = [int(k.split("-")[1]) for k in db.scalars(select(Fact.key)) if k.split("-")[1].isdigit()]
    return f"F-{(max(nums) + 1) if nums else 1:02d}"


@router.post("/facts")
def add_fact(body: FactBody, u: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require(u, "facts.edit")
    if len(body.statement.strip()) < 8:
        raise HTTPException(400, "State the fact as a full sentence.")
    if not body.evidence.strip():
        raise HTTPException(400, "Every fact needs evidence (a document, link or reference).")
    f = Fact(key=_next_fact_key(db), verified_at=today(), **body.model_dump())
    db.add(f)
    db.commit()
    return serialize.fact(f, today())


class FactPatch(BaseModel):
    statement: Optional[str] = None
    approved_phrasing: Optional[str] = None
    value: Optional[float] = None
    unit: Optional[str] = None
    owner: Optional[str] = None
    evidence: Optional[str] = None
    valid_from: Optional[dt.date] = None
    valid_until: Optional[dt.date] = None
    clear_valid_until: bool = False
    superseded_by: Optional[str] = None
    retired: Optional[bool] = None


@router.patch("/facts/{key}")
def patch_fact(key: str, body: FactPatch, u: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require(u, "facts.edit")
    f = db.scalar(select(Fact).where(Fact.key == key))
    if f is None:
        raise HTTPException(404, "Fact not found")
    data = body.model_dump(exclude_unset=True)
    clear = data.pop("clear_valid_until", False)
    for k, v in data.items():
        setattr(f, k, v)
    if clear:
        f.valid_until = None
    f.verified_at = today()
    db.commit()
    return serialize.fact(f, today())


@router.get("/facts/{key}/impact")
def fact_impact(key: str, u: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    users = {x.id: x for x in db.scalars(select(User))}
    hits = []
    for c in db.scalars(select(ContentPR).where(ContentPR.status != "approved")):
        v = services.latest(c)
        if any(cl.get("fact_key") == key for cl in (v.claims or [])) or any(f.fact_key == key for f in v.findings):
            hits.append(serialize.cpr_brief(c, users))
    approved = []
    for c in db.scalars(select(ContentPR).where(ContentPR.status == "approved")):
        v = next((x for x in c.versions if x.id == c.approved_version_id), None)
        if v and any(cl.get("fact_key") == key for cl in (v.claims or [])):
            approved.append(serialize.cpr_brief(c, users))
    return {"open": hits, "approved": approved}


@router.post("/facts/{key}/recheck")
def fact_recheck(key: str, bg: BackgroundTasks, u: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require(u, "facts.edit")
    n = 0
    for c in db.scalars(select(ContentPR).where(ContentPR.status != "approved")):
        v = services.latest(c)
        if v.run_status in {"pending", "running"}:
            continue
        if any(cl.get("fact_key") == key for cl in (v.claims or [])) or any(f.fact_key == key for f in v.findings):
            _start_run(bg, db, v)
            n += 1
    return {"rechecked": n}


@router.post("/facts/import")
async def import_facts(file: UploadFile = File(...), u: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require(u, "facts.edit")
    raw = (await file.read()).decode("utf-8-sig")
    added = 0
    for row in csv.DictReader(io.StringIO(raw)):
        st = (row.get("statement") or "").strip()
        if not st or not (row.get("evidence") or "").strip():
            continue
        def d(k: str):
            v = (row.get(k) or "").strip()
            return dt.date.fromisoformat(v) if v else None
        db.add(Fact(key=_next_fact_key(db), statement=st, approved_phrasing=(row.get("approved_phrasing") or "").strip(),
                    claim_class=(row.get("claim_class") or "general").strip(), subject=(row.get("subject") or "").strip(),
                    value=float(row["value"]) if (row.get("value") or "").strip() else None, unit=(row.get("unit") or "").strip(),
                    owner=(row.get("owner") or "").strip(), evidence=row["evidence"].strip(), valid_from=d("valid_from"),
                    valid_until=d("valid_until"), verified_at=today()))
        db.flush()
        added += 1
    db.commit()
    return {"added": added}


# ------------------------------------------------------------------ AI disclosure log & settings


@router.get("/ailog")
def ailog(cpr: Optional[str] = None, u: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    q = select(AICall).order_by(AICall.created_at.desc()).limit(300)
    if cpr:
        c = _get(db, cpr, u)
        q = select(AICall).where(AICall.cpr_id == c.id).order_by(AICall.created_at.desc())
    else:
        require(u, "ailog.view_all")
    calls = db.scalars(q).all()
    keys = {c.id: c.key for c in db.scalars(select(ContentPR))}
    return {"llm": gateway.status(), "ml": ml.status(),
            "calls": [{**serialize.ai_call(a), "cpr_key": keys.get(a.cpr_id)} for a in calls],
            "totals": {s: sum(1 for a in calls if a.status == s) for s in ("ok", "cached", "skipped", "error", "invalid")}}


@router.get("/settings/confidential-terms")
def get_terms(u: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require(u, "settings.edit")
    return {"terms": gateway.confidential_terms(db)}


class TermsBody(BaseModel):
    terms: list[str]


@router.put("/settings/confidential-terms")
def put_terms(body: TermsBody, u: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require(u, "settings.edit")
    terms = sorted({t.strip() for t in body.terms if t.strip()})[:200]
    db.merge(Setting(key="confidential_terms", value=terms))
    db.commit()
    return {"terms": terms}


@router.get("/eval")
def eval_results() -> dict[str, Any]:
    p = Path(__file__).resolve().parents[2] / "eval" / "results.json"
    if not p.exists():
        return {"available": False}
    return {"available": True, **json.loads(p.read_text())}
