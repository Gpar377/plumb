"""Run the golden set and report precision/recall per check.

    python -m eval.run_eval            # offline: rules + local models (what CI runs)
    GEMINI_API_KEY=... python -m eval.run_eval --llm

Writes eval/results.json (served at /api/eval) and prints a table.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
os.environ.setdefault("PLUMB_TODAY", "2026-10-22")
os.environ.setdefault("PLUMB_DATA_DIR", tempfile.mkdtemp(prefix="plumb-eval-"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true", help="use the Gemini gateway too (needs GEMINI_API_KEY)")
    args = ap.parse_args()
    if not args.llm:
        os.environ["PLUMB_LLM_DISABLED"] = "1"

    from app import seed_data as sd
    from app.db import init_db
    from app.engine import intent
    from app.engine.core import RunContext, today
    from app.engine.runner import analyse
    from app.engine.core import FactView
    from app.models import Fact
    from eval.golden import CASES

    init_db()
    facts = []
    for f in sd.FACTS:
        fo = Fact(**f)
        facts.append(FactView(key=fo.key, statement=fo.statement, approved_phrasing=fo.approved_phrasing, claim_class=fo.claim_class, subject=fo.subject,
                              value=fo.value, unit=fo.unit or "", owner=fo.owner, evidence=fo.evidence, status=fo.status_on(today()),
                              valid_until=str(fo.valid_until) if fo.valid_until else None, superseded_by=fo.superseded_by, verified_at=str(fo.verified_at)))
    rules = sd.RULES
    sev = {r["key"]: r["severity"] for r in rules}
    check_of = {"T": "terms", "V": "voice", "C": "claims", "A": "a11y", "P": "format"}
    rule_keys, fact_keys = {r["key"] for r in rules}, {f.key for f in facts}

    def run(case):
        ctx = RunContext(text=case["text"], channel=case["channel"], author_type=case["author"], rules=rules, facts=facts, policy_version=2,
                         use_llm=args.llm, llm_ctx={"cpr_id": None, "version_id": None, "confidential": False})
        ctx.intent = intent.heuristic(ctx)
        ds, _, _ = analyse(ctx)
        return ds

    stats: dict[str, dict[str, int]] = {}
    blk = {"tp": 0, "fp": 0, "fn": 0}
    allc = {"tp": 0, "fp": 0, "fn": 0}
    cited = total = 0
    deterministic = True
    misses, extras = [], []

    def bump(check: str, k: str) -> None:
        stats.setdefault(check, {"tp": 0, "fp": 0, "fn": 0, "expected": 0})[k] += 1

    for case in CASES:
        ds = run(case)
        if not args.llm:
            again = run(case)
            if [(d.rule_key, d.fact_key, d.start, d.end, d.fix) for d in ds] != [(d.rule_key, d.fact_key, d.start, d.end, d.fix) for d in again]:
                deterministic = False
        for d in ds:
            total += 1
            cited += int(d.rule_key in rule_keys and (d.fact_key is None or d.fact_key in fact_keys))
        used = set()
        for rule, fact, ex in case["expect"]:
            chk = check_of[rule[0]]
            stats.setdefault(chk, {"tp": 0, "fp": 0, "fn": 0, "expected": 0})["expected"] += 1
            occ = []
            if ex:
                j = case["text"].find(ex)
                while j >= 0:
                    occ.append((j, j + len(ex)))
                    j = case["text"].find(ex, j + 1)
            hit = None
            for i, d in enumerate(ds):
                if i in used or d.rule_key != rule or (fact and d.fact_key != fact):
                    continue
                if not ex or any(d.overlaps(a, b) and d.start >= a - 1 and d.end <= b + 1 for a, b in occ) or (
                        len(occ) == 1 and d.overlaps(*occ[0])):
                    hit = i
                    break
            is_block = sev.get(rule) == "block"
            if hit is None:
                bump(chk, "fn")
                allc["fn"] += 1
                blk["fn"] += int(is_block)
                misses.append({"case": case["id"], "rule": rule, "fact": fact, "excerpt": ex})
            else:
                used.add(hit)
                bump(chk, "tp")
                allc["tp"] += 1
                if is_block and ds[hit].severity == "block":
                    blk["tp"] += 1
                elif is_block:
                    blk["fn"] += 1
        for i, d in enumerate(ds):
            if i in used:
                continue
            bump(d.check, "fp")
            allc["fp"] += 1
            blk["fp"] += int(d.severity == "block")
            extras.append({"case": case["id"], "rule": d.rule_key, "fact": d.fact_key, "excerpt": case["text"][d.start:d.end], "severity": d.severity})

    def pr(s):
        p = s["tp"] / (s["tp"] + s["fp"]) if (s["tp"] + s["fp"]) else 1.0
        r = s["tp"] / (s["tp"] + s["fn"]) if (s["tp"] + s["fn"]) else 1.0
        f = 2 * p * r / (p + r) if p + r else 0.0
        return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(f, 3)}

    out = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "mode": "rules + local models (no external LLM)" if not args.llm else "rules + local models + Gemini",
        "cases": len(CASES),
        "overall": {"all": {**pr(allc), **allc}, "block": {**pr(blk), **blk}},
        "by_check": {k: {**pr(v), "expected": v["expected"]} for k, v in sorted(stats.items())},
        "citation_validity": round(cited / total, 3) if total else 1.0,
        "determinism": deterministic,
        "misses": misses,
        "false_positives": extras,
    }
    (HERE / ("results.json" if not args.llm else "results-llm.json")).write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"{len(CASES)} cases, mode: {out['mode']}")
    print(f"all findings  P={out['overall']['all']['precision']:.2f} R={out['overall']['all']['recall']:.2f}")
    print(f"blockers      P={out['overall']['block']['precision']:.2f} R={out['overall']['block']['recall']:.2f}")
    for k, v in out["by_check"].items():
        print(f"  {k:8} P={v['precision']:.2f} R={v['recall']:.2f} (expected {v['expected']})")
    print(f"citation validity {out['citation_validity']:.0%}, deterministic: {deterministic}")
    for m in misses:
        print("  MISS", m)
    for e in extras:
        print("  EXTRA", e)


if __name__ == "__main__":
    main()
