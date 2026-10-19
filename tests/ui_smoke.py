"""Optional headless UI smoke test. Run after python -m wallet_forensics report."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
out=Path(__file__).resolve().parents[1]/"output"
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page(viewport={"width":1440,"height":1000},device_scale_factor=1)
    errors=[]
    page.on("pageerror",lambda e:errors.append(str(e)))
    page.goto((out/"report.html").as_uri(),wait_until="networkidle")
    assert page.locator("#cards .card").count()==4
    assert page.locator("#tabs button").count()==9
    page.screenshot(path=str(out/"report-desktop.png"),full_page=False)
    page.get_by_role("tab",name="Calls",exact=True).click()
    page.locator("#search").fill("18330")
    assert page.locator("#rows tr").count()>=1
    page.locator("#search").fill("")
    page.get_by_role("tab",name="Negociações",exact=True).click()
    page.locator("#chain").select_option("ethereum")
    assert page.locator("#csv").get_attribute("href")=="trades.csv"
    page.locator("#chain").select_option("")
    page.get_by_role("tab",name="Candidatos",exact=True).click()
    page.set_viewport_size({"width":390,"height":844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"),"mobile page overflow"
    page.screenshot(path=str(out/"report-mobile.png"),full_page=False)
    assert not errors,errors
    result={"cards":4,"tabs":9,"search":"passed","chain_filter":"passed",
            "javascript_errors":errors,"mobile_overflow":False}
    (out/"ui-smoke.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result))
    browser.close()
