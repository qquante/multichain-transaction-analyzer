"""Regression: every wallet exposes actual tokens and all matching purchase evidence."""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright
out=Path(__file__).resolve().parents[1]/"output"
with sync_playwright() as p:
    b=p.chromium.launch(headless=True)
    page=b.new_page(viewport={"width":1440,"height":1000})
    page.goto((out/"report.html").as_uri())
    actual=page.evaluate("""() => {
      const d=JSON.parse(document.querySelector('#payload').textContent);
      return {loaded_trades:d.tables.trades.length,total_trades:d.counts.trades};
    }""")
    print(json.dumps(actual))
    assert actual["loaded_trades"]==actual["total_trades"],"Search must include every collected trade"
    page.get_by_role("tab",name="Wallets e compras",exact=True).click()
    wallet="0x9f46ff59dd7c885f30ab937456623e86323bb0f8"
    page.locator("#chain").select_option("robinhood")
    page.locator("#search").fill(wallet)
    row=page.locator("#rows tr")
    assert row.count()==1
    assert "STARMAN" in row.inner_text()
    expected=int(row.locator("td").nth(4).inner_text())
    row.get_by_role("button",name="Ver compras").click()
    assert page.locator("#wallet-detail").is_visible()
    assert page.locator("#wallet-title").inner_text()==wallet
    assert page.locator("#purchase-rows tr").count()==expected
    assert page.locator("#purchase-rows a.tx-link").count()==expected
    assert "DONALD" in page.locator("#purchase-rows").inner_text()
    page.locator("#close-detail").click()
    page.get_by_role("tab",name="Tokens comprados",exact=True).click()
    page.locator("#search").fill("0x35498154f0f6f2b94a1f57d3a7309b5212c36713")
    page.locator("#rows").get_by_role("button",name="Ver wallets").click()
    assert page.get_by_role("tab",name="Wallets e compras").get_attribute("aria-selected")=="true"
    assert page.locator("#rows tr").count()>1
    page.locator("#search").fill("");page.locator("#chain").select_option("")
    page.screenshot(path=str(out/"wallets-desktop.png"))
    page.set_viewport_size({"width":390,"height":844})
    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
    page.screenshot(path=str(out/"wallets-mobile.png"))
    b.close()
print("PASS: full search, wallet tokens, all matching purchase hashes and token-to-wallet navigation")
