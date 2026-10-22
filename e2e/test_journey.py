"""Browser end-to-end test of the core journey, run against a live Plumb server.

    PLUMB_URL=http://localhost:8000 python -m pytest e2e -q

Covers: sample draft -> checks -> apply verified fixes -> v2 clean -> submit ->
brand manager approves -> claims reviewer approves -> signed receipt verifies.
"""
import os
import re

import pytest
from playwright.sync_api import expect, sync_playwright

URL = os.getenv("PLUMB_URL", "http://localhost:8000")


@pytest.fixture(scope="module")
def page():
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1360, "height": 900})
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        yield pg
        b.close()
        assert not errors, errors


IDS = {"Bluefin": "bluefin", "Asha": "asha", "Meera": "meera", "Rohan": "rohan", "Admin": "admin"}


def switch(page, name):
    page.get_by_label("Viewing as").select_option(value=IDS[name])
    page.wait_for_timeout(400)


def test_core_journey(page):
    page.goto(URL)
    switch(page, "Bluefin")
    expect(page.get_by_role("heading", name="Try it in two minutes")).to_be_visible()
    page.get_by_role("button", name=re.compile("Instagram launch caption")).click()
    page.wait_for_url("**/c/CPR-*")
    key = page.url.rsplit("/", 1)[-1]
    expect(page.get_by_text("4 blocking", exact=True)).to_be_visible(timeout=60000)
    expect(page.get_by_role("button", name="Submit for review")).to_be_disabled()

    # click an inline mark: the matching finding is focused
    page.get_by_role("button", name=re.compile("₹299")).first.click()
    expect(page.get_by_text("Outdated information")).to_be_visible()

    page.get_by_role("button", name="Apply all verified fixes").click()
    expect(page.get_by_text("Version 2", exact=False)).to_be_visible(timeout=60000)
    expect(page.get_by_text("Every check passes on this version.")).to_be_visible(timeout=60000)
    expect(page.locator("text=₹349").first).to_be_visible()
    page.get_by_role("button", name="Submit for review").click()
    expect(page.get_by_text("In review", exact=True).first).to_be_visible()

    switch(page, "Meera")
    page.goto(f"{URL}/c/{key}")
    page.get_by_role("button", name=re.compile("Approve v2")).click()
    expect(page.get_by_text(re.compile("Waiting for claims reviewer"))).to_be_visible()

    switch(page, "Rohan")
    page.goto(f"{URL}/c/{key}")
    page.get_by_role("button", name=re.compile("Approve v2")).click()
    expect(page.get_by_text("Audit receipt")).to_be_visible(timeout=10000)
    page.get_by_role("button", name="Verify signature").click()
    expect(page.get_by_role("button", name=re.compile("Signature valid"))).to_be_visible()


def test_policy_import_as_brand_manager(page):
    page.goto(f"{URL}/policy")
    switch(page, "Meera")
    page.goto(f"{URL}/policy")
    page.get_by_role("button", name="Use the October 2026 update").click()
    page.get_by_role("button", name="Compile into rules").click()
    expect(page.get_by_role("heading", name=re.compile(r"Proposed rules \("))).to_be_visible(timeout=60000)
    approve = page.get_by_role("button", name="Approve", exact=True)
    while approve.count():
        approve.first.click()
        page.wait_for_timeout(400)
    page.get_by_role("button", name=re.compile(r"Publish v\d")).click()
    expect(page.get_by_text(re.compile(r"Policy v\d, in force"))).to_be_visible()


def test_creator_cannot_see_policy_controls(page):
    switch(page, "Asha")
    page.goto(f"{URL}/policy")
    expect(page.get_by_role("button", name="Compile into rules")).to_have_count(0)
    page.goto(f"{URL}/facts")
    expect(page.get_by_role("button", name="Add a fact")).to_have_count(0)
