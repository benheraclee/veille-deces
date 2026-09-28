"""Diagnostic FAO Genève : le robot passe-t-il le CAPTCHA ? Structure des pages ventes immobilières."""
import os, re, json, time
import requests
from playwright.sync_api import sync_playwright

SB_URL = os.environ["SUPABASE_URL"].rstrip("/")
SB_KEY = os.environ["SUPABASE_SERVICE_KEY"]
SB = {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json"}


def debug(cle, contenu):
    requests.post(f"{SB_URL}/rest/v1/debug", headers=SB,
                  data=json.dumps({"cle": cle, "contenu": str(contenu)[:60000]}), timeout=30)


UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")

# 1) requêtes simples
for u in ["https://fao.ge.ch", "https://fao.ge.ch/"]:
    try:
        r = requests.get(u, headers={"User-Agent": UA}, timeout=30)
        debug("fao_requests", f"{u} {r.status_code} {len(r.text)} {r.text[:1500]}")
    except Exception as e:
        debug("fao_requests", f"{u} exception {e}")

# 2) navigateur
with sync_playwright() as pw:
    nav = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
    page = nav.new_page(locale="fr-CH", user_agent=UA, viewport={"width": 1300, "height": 900})
    reseau = []
    page.on("response", lambda r: reseau.append(f"{r.status} {r.request.method} {r.url}") if ("api" in r.url or "json" in r.headers.get("content-type", "")) else None)
    page.goto("https://fao.ge.ch", wait_until="domcontentloaded", timeout=60000)
    for i in range(12):
        time.sleep(5)
        t = page.inner_text("body")[:300]
        if not re.search(r"captcha|verif|robot|challenge", t, re.I):
            break
    debug("fao_accueil", json.dumps({"titre": page.title(), "url": page.url, "texte": page.inner_text("body")[:5000]}, ensure_ascii=False))
    liens = page.eval_on_selector_all("a[href]", "els => els.map(a => [a.innerText.trim().slice(0,80), a.href])")
    debug("fao_liens", json.dumps(liens[:400], ensure_ascii=False))
    cibles = [l for l in liens if re.search(r"foncier|mutation|transfert|immobili|ench[eè]re|vente|poursuite", l[0] + l[1], re.I)]
    debug("fao_cibles", json.dumps(cibles[:60], ensure_ascii=False))
    for txt, href in cibles[:4]:
        try:
            page.goto(href, wait_until="domcontentloaded", timeout=60000)
            time.sleep(6)
            debug("fao_page", json.dumps({"lien": txt, "url": page.url, "titre": page.title(),
                                          "texte": page.inner_text("body")[:8000],
                                          "html": page.content()[:15000]}, ensure_ascii=False))
        except Exception as e:
            debug("fao_page", f"{href} {e}")
    debug("fao_reseau", "\n".join(reseau[:200]))
    nav.close()
