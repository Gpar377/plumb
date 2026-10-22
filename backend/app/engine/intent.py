"""Intent capture: what the creator is trying to achieve. Fixes must preserve it."""
from __future__ import annotations

import json
import re
from typing import Any

from ..llm import gateway
from ..llm.gateway import ARR, OBJ, S
from .core import RunContext
from .text import phrase_regex, sentences

CTA_VERBS = ["shop", "buy", "find", "get", "order", "grab", "try", "discover", "explore", "book", "sign up", "register",
             "download", "use code", "visit", "join", "treat yourself", "learn", "see", "start", "enjoy", "claim", "subscribe", "meet"]

SCHEMA = OBJ({
    "goal": S("string", description="what the piece is for, e.g. 'launch announcement'"),
    "audience": S("string"),
    "cta": S("string", description="the call to action, quoted or paraphrased; empty if none"),
    "key_message": S("string"),
    "must_keep": ARR(S("string", description="short exact phrases from the content that carry the message (product names, offer, CTA)")),
})
SYSTEM = (
    "You summarise the creator's intent for a piece of brand content so that later edits can preserve it. "
    "Be brief and concrete. must_keep: up to 4 short exact phrases copied from the content that must survive any edit "
    "(product name, the offer, the call to action). Do not judge compliance."
)


def cta_verbs_in(text: str) -> set[str]:
    return {v for v in CTA_VERBS if phrase_regex(v).search(text)}


def heuristic(ctx: RunContext, title: str = "") -> dict[str, Any]:
    text = ctx.text
    canon = ctx.canonicalize(text)
    products = [p for p in ctx.products() if phrase_regex(p).search(canon)]
    main = next((p for p in products if p != "Kora Naturals"), products[0] if products else "")
    cta = ""
    for s in reversed(sentences(text)):
        if cta_verbs_in(s.text):
            cta = s.text.strip()[:120]
            break
    goal = {"instagram": "Social post", "x": "Social post", "linkedin": "Professional update", "email": "Email campaign",
            "web": "Web page copy", "blog": "Blog article", "sms": "SMS message", "print": "Print collateral"}.get(ctx.channel, "Content")
    if main:
        goal += f" promoting {main}"
    return {"goal": goal if not title else f"{goal} — {title}", "audience": "", "cta": cta,
            "key_message": "", "must_keep": products[:4], "source": "heuristic", "confirmed": False}


def extract(ctx: RunContext, title: str = "") -> dict[str, Any]:
    base = heuristic(ctx, title)
    if not ctx.use_llm:
        return base
    res = gateway.call(gateway.Ctx("intent", **ctx.llm_ctx), SYSTEM,
                       json.dumps({"channel": ctx.channel, "title": title, "content": ctx.text}, ensure_ascii=False), SCHEMA)
    if not res:
        return base
    keep = [k for k in res.get("must_keep", []) if isinstance(k, str) and k.strip() and k in ctx.text][:4]
    for p in base["must_keep"]:
        if p not in keep and phrase_regex(p).search(ctx.canonicalize(ctx.text)):
            keep.append(p)
    return {"goal": str(res.get("goal", ""))[:160] or base["goal"], "audience": str(res.get("audience", ""))[:160],
            "cta": str(res.get("cta", ""))[:160] or base["cta"], "key_message": str(res.get("key_message", ""))[:240],
            "must_keep": keep[:6], "source": "llm", "confirmed": False}


def guard(ctx: RunContext, before: str, after: str) -> tuple[bool, str]:
    """Intent lock: a fix may not drop a must-keep phrase or remove every call-to-action."""
    cb, ca = ctx.canonicalize(before).lower(), ctx.canonicalize(after).lower()
    for k in ctx.intent.get("must_keep", []) or []:
        kc = ctx.canonicalize(k).lower()
        if kc and kc in cb and kc not in ca:
            return False, f"Would remove “{k}”, which carries the message."
    if cta_verbs_in(before) and not cta_verbs_in(after):
        return False, "Would remove the call to action."
    return True, ""
