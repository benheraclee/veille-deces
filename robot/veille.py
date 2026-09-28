#!/usr/bin/env python3
"""
Robot de veille décès × propriétaires (Genève) — tourne sur GitHub Actions.

Sources : hommages.ch (Tribune de Genève, 24 Heures…, avis en image -> OCR)
          funere.com  (Genève : commune + année de naissance dans l'URL)
Écrit dans Supabase : avis, correspondances, passages.

Variables d'environnement :
  SUPABASE_URL, SUPABASE_SERVICE_KEY   (obligatoires)
  JOURS (défaut 3), GMAIL_USER, GMAIL_APP_PASSWORD, MAIL_TO (optionnels)
"""
import os, re, io, sys, time, json, smtplib, unicodedata, datetime as dt, traceback
from collections import defaultdict
from email.message import EmailMessage
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

SB_URL = os.environ["SUPABASE_URL"].rstrip("/")
SB_KEY = os.environ["SUPABASE_SERVICE_KEY"]
JOURS = int(os.environ.get("JOURS") or 3)
PAUSE = 1.2
SB = {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json"}
WEB = requests.Session()
WEB.headers.update({"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                    "Accept-Language": "fr-CH,fr;q=0.9"})
AUJ = dt.date.today()
DATE_FIN = dt.date.fromisoformat(os.environ["DATE_FIN"]) if os.environ.get("DATE_FIN") else AUJ
DATE_DEBUT = dt.date.fromisoformat(os.environ["DATE_DEBUT"]) if os.environ.get("DATE_DEBUT") else DATE_FIN - dt.timedelta(days=JOURS)
LIMITE = DATE_DEBUT

MOIS = {"janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juillet": 7, "aout": 8,
        "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12}
JOURNAUX = ["Tribune de Genève", "24 Heures", "Le Courrier", "20 minutes", "GHI", "Le Temps", "La Liberté",
            "Le Nouvelliste", "ArcInfo", "La Côte", "Basler Zeitung", "Berner Zeitung", "Der Bund",
            "Tages-Anzeiger", "Der Landbote", "Thuner Tagblatt", "Berner Oberländer", "Zürcher Unterländer",
            "Zürichsee-Zeitung"]
JOURNAUX_OCR = {"Tribune de Genève", "24 Heures", "Le Courrier", "20 minutes", "GHI", "Le Temps"}


# ------------------------------------------------------------ outils
def sa(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def tok(s):
    return re.sub(r"[^a-z]+", " ", sa(str(s or "")).lower()).split()


def date_fr(t):
    t = sa(t or "").lower()
    m = re.search(r"(\d{1,2})\.?\s*(janvier|fevrier|mars|avril|mai|juin|juillet|aout|septembre|octobre|novembre|decembre)\s+(\d{4})", t)
    if m:
        return dt.date(int(m.group(3)), MOIS[m.group(2)], int(m.group(1)))
    m = re.search(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", t)
    if m:
        return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    return None


def get(url, **kw):
    for essai in range(3):
        try:
            r = WEB.get(url, timeout=40, **kw)
            if r.status_code == 200:
                return r
            print(f"   {r.status_code} {url}")
            if r.status_code in (403, 429):
                time.sleep(10 * (essai + 1))
        except requests.RequestException as e:
            print("   erreur réseau", e)
            time.sleep(5)
    return None


# ------------------------------------------------------------ supabase
def sb_get_all(table, select, extra=""):
    out, pas = [], 1000
    while True:
        r = requests.get(f"{SB_URL}/rest/v1/{table}?select={select}{extra}",
                         headers={**SB, "Range": f"{len(out)}-{len(out) + pas - 1}"}, timeout=60)
        r.raise_for_status()
        lot = r.json()
        out += lot
        if len(lot) < pas:
            return out


def sb_upsert(table, rows, conflict, ignorer=False, retour=True):
    if not rows:
        return []
    pref = ("resolution=ignore-duplicates" if ignorer else "resolution=merge-duplicates") + \
           (",return=representation" if retour else ",return=minimal")
    r = requests.post(f"{SB_URL}/rest/v1/{table}?on_conflict={conflict}", headers={**SB, "Prefer": pref},
                      data=json.dumps(rows, default=str), timeout=60)
    if r.status_code >= 300:
        raise RuntimeError(f"{table}: {r.status_code} {r.text[:300]}")
    return r.json() if retour else []


def sb_insert(table, row):
    r = requests.post(f"{SB_URL}/rest/v1/{table}", headers={**SB, "Prefer": "return=representation"},
                      data=json.dumps(row, default=str), timeout=30)
    r.raise_for_status()
    return r.json()[0]


def sb_patch(table, id_, row):
    requests.patch(f"{SB_URL}/rest/v1/{table}?id=eq.{id_}", headers=SB, data=json.dumps(row, default=str), timeout=30)


def debug(cle, contenu):
    try:
        requests.post(f"{SB_URL}/rest/v1/debug", headers=SB, data=json.dumps({"cle": cle, "contenu": str(contenu)[:60000]}), timeout=30)
    except Exception:
        pass


def slugs_hommages(html):
    return [m for m in re.findall(r'/fr/avis-de-deces/([a-z0-9\-]+)"', html)][:5]


def diagnostic_hommages():
    base = "https://www.hommages.ch/fr/avis-de-deces"
    r1 = get(base)
    if not r1:
        debug("hommages_p1", "échec")
        return
    soup = BeautifulSoup(r1.text, "html.parser")
    liens = sorted({a["href"] for a in soup.find_all("a", href=True) if "page" in a["href"] or "?" in a["href"]})
    forms = [str(f)[:4000] for f in soup.find_all("form")]
    btns = [str(b)[:300] for b in soup.find_all(["button", "nav"]) if re.search(r"page|suiv|next|plus", b.get_text(" ", strip=True) + str(b.attrs), re.I)][:20]
    scripts = [sc.get("src") for sc in soup.find_all("script") if sc.get("src")]
    debug("hommages_p1", json.dumps({"slugs": slugs_hommages(r1.text), "liens": liens[:80], "forms": forms,
                                     "boutons": btns, "scripts": scripts, "taille": len(r1.text)}, ensure_ascii=False))
    idx = r1.text.find("page=2")
    debug("hommages_p1_autour_page2", r1.text[max(0, idx - 3000): idx + 1500] if idx >= 0 else "pas de page=2")
    j = r1.text.find("avis-de-deces/")
    debug("hommages_p1_bloc_avis", r1.text[max(0, j - 1500): j + 2500])
    hier = (AUJ - dt.timedelta(days=12)).strftime("%d.%m.%Y")
    for v in ["?page=2", "?page=3", "?p=2", f"?from={hier}&to={hier}", f"?page=1&from={hier}&to={hier}",
              f"?name=&newspaper=&from={hier}&to={hier}"]:
        r = get(base + v)
        debug("hommages_variante", f"{v} -> {r.status_code if r else 'x'} {slugs_hommages(r.text) if r else ''}")
        time.sleep(1)


def diagnostic_funere():
    u = "https://www.funere.com/ch-fr/avis-de-deces/geneve"
    try:
        r = WEB.get(u, timeout=40)
        soup = BeautifulSoup(r.text, "html.parser")
        hrefs = [a["href"] for a in soup.find_all("a", href=True) if "avis-de-deces" in a["href"]][:30]
        debug("funere", json.dumps({"status": r.status_code, "taille": len(r.text), "hrefs": hrefs,
                                    "texte": soup.get_text(" ", strip=True)[:3000]}, ensure_ascii=False))
        j = r.text.find("/avis-de-deces/geneve/")
        debug("funere_html", r.text[max(0, j - 1500): j + 3000])
    except Exception as e:
        debug("funere", f"exception {e}")


# ------------------------------------------------------------ base propriétaires
class Base:
    def __init__(self):
        rows = sb_get_all("proprietaires", "id,nom,prenoms,surnom,parcelle_id,parcelles(commune,priorite)")
        self.pers = []
        freq = defaultdict(int)
        for r in rows:
            p = {"id": r["id"], "S": tok(r["nom"]),
                 "F": [t for t in tok(f"{r['prenoms'] or ''} {r['surnom'] or ''}") if len(t) > 2],
                 "F1": (tok(r["prenoms"]) or [""])[0],
                 "commune": tok(re.sub(r"\(.*?\)", "", (r.get("parcelles") or {}).get("commune") or "")),
                 "prio": (r.get("parcelles") or {}).get("priorite") or "basse"}
            if p["S"]:
                self.pers.append(p)
                freq[" ".join(p["S"])] += 1
        prenoms_freq = defaultdict(int)
        for p in self.pers:
            for t in set(p["F"]):
                prenoms_freq[t] += 1
        MOTS = {"compagnon", "compagne", "petite", "petit", "fils", "fille", "ami", "amie", "frere", "soeur", "mari",
                "epoux", "epouse", "famille", "maman", "papa", "grand", "mere", "pere", "bon", "bonne", "belle", "beau",
                "jeune", "vieux", "blanc", "noir", "rouge", "vert", "leur", "leurs", "tout", "tous", "ainsi", "amis",
                "dieu", "paix", "coeur", "vie", "juste", "douce", "cher", "chere", "saint", "sainte", "roi", "comte"}
        self.index = defaultdict(list)
        for p in self.pers:
            p["homonymes"] = freq[" ".join(p["S"])]
            # nom de famille qui est aussi un prénom courant ou un mot courant -> ambigu dans un texte
            p["ambigu"] = len(p["S"]) == 1 and (prenoms_freq[p["S"][0]] >= 15 or p["S"][0] in MOTS)
            self.index[p["S"][0]].append(p)
        print(f"Base : {len(self.pers)} lignes propriétaire")

    def candidats(self, T):
        for pos, t in enumerate(T):
            for p in self.index.get(t, ()):
                if T[pos:pos + len(p["S"])] == p["S"]:
                    yield p, pos


VIDES = {"le", "la", "les", "grand", "petit", "de", "du", "sur", "sous", "ge", "geneve"}


def meme_commune(p, commune_avis):
    a = set(commune_avis) - VIDES
    b = set(p["commune"]) - VIDES
    return bool(a & b)


def analyser(base, avis):
    res, vus = [], set()
    N = tok(avis["nom"])
    mots = avis["nom"].split()
    k = len(tok(mots[0])) if mots else 1  # nb de tokens du 1er mot (prénom, éventuellement composé)
    for p, pos in base.candidats(N):
        if pos < k:  # le nom trouvé fait partie du prénom (ex. "Valerio Pavesi", "Hans-Conrad Kessler")
            continue
        autres = N[:pos]  # les prénoms précèdent le nom
        if p["F1"] and p["F1"] in autres:
            niv, sc = "DÉFUNT — nom + 1er prénom", 90
        elif any(f in autres for f in p["F"]):
            niv, sc = "DÉFUNT — nom + prénom secondaire", 60
        elif p["homonymes"] <= 2 and len("".join(p["S"])) >= 6 and not p["ambigu"]:
            niv, sc = "MÊME NOM — nom rare, autre prénom", 40
        else:
            continue
        res.append((p, niv, sc, avis["nom"]))
        vus.add(p["id"])
    T = tok(avis.get("texte"))
    for p, pos in base.candidats(T):
        if p["id"] in vus or len("".join(p["S"])) < 4 or p["ambigu"]:
            continue
        fen = T[max(0, pos - 6):pos] + T[pos + len(p["S"]):pos + len(p["S"]) + 4]
        if p["F1"] and p["F1"] in fen:
            niv, sc = "FAMILLE — nom + 1er prénom dans l'avis", 60
        elif any(f in fen for f in p["F"]):
            niv, sc = "FAMILLE — nom + prénom secondaire", 45
        else:
            continue
        res.append((p, niv, sc, " ".join(T[max(0, pos - 8):pos + len(p["S"]) + 6])))
        vus.add(p["id"])
    out = []
    texte_t = " " + " ".join(T) + " "
    for p, niv, sc, extrait in res:
        cc = ""
        if avis.get("commune") and meme_commune(p, tok(avis["commune"])):
            cc = avis["commune"]
        elif p["commune"] and f" {' '.join(p['commune'])} " in texte_t:
            cc = " ".join(p["commune"])
        sc += (15 if cc else 0) + {"haute": 5, "moyenne": 0}.get(p["prio"], -15)
        out.append({"proprietaire_id": p["id"], "niveau": niv, "score": max(0, min(100, sc)),
                    "extrait": extrait[:300], "commune_citee": cc or None})
    return out


# ------------------------------------------------------------ OCR
def ocr(data):
    try:
        import pytesseract
        from PIL import Image
        img = Image.open(io.BytesIO(data)).convert("L")
        if img.width < 1400:
            img = img.resize((img.width * 2, img.height * 2))
        return pytesseract.image_to_string(img, lang="fra+deu")
    except Exception as e:
        print("   OCR impossible:", e)
        return ""


# ------------------------------------------------------------ sources
def lister_hommages():
    """Liste des avis des journaux romands, jour par jour (filtre date du site)."""
    base = "https://www.hommages.ch"
    avis, vus = [], set()
    jour = DATE_FIN
    while jour >= DATE_DEBUT:
        d = jour.isoformat()
        nb_jour = 0
        for n in range(1, 30):
            url = f"{base}/fr/avis-de-deces?published_in=fr&date_from={d}&date_to={d}" + (f"&page={n}" if n > 1 else "")
            r = get(url)
            if not r:
                print(f"   {d} page {n}: échec, on passe")
                break
            soup = BeautifulSoup(r.text, "html.parser")
            nouveaux = 0
            for a in soup.select('ul li a[href*="/avis-de-deces/"]'):
                m = re.search(r"/fr/avis-de-deces/([a-z0-9\-]+)/?$", a["href"].split("?")[0])
                if not m or m.group(1) in vus:
                    continue
                vus.add(m.group(1))
                titre = a.select_one("div.text-xl") or a
                nom = titre.get_text(" ", strip=True)
                avis.append({"source": "hommages", "slug": m.group(1), "nom": nom,
                             "url": urljoin(base, a["href"]), "date_publication": jour})
                nouveaux += 1
            nb_jour += nouveaux
            time.sleep(PAUSE)
            if nouveaux == 0 or not soup.select_one(f'a[href*="page={n + 1}"]'):
                break
        print(f" hommages {d}: {nb_jour} avis")
        jour -= dt.timedelta(days=1)
    return avis


def lire_hommages(a):
    r = get(a["url"])
    time.sleep(PAUSE)
    if not r:
        return a
    soup = BeautifulSoup(r.text, "html.parser")
    corps = soup.get_text(" ", strip=True)
    a["journaux"] = [j for j in JOURNAUX if j.lower() in corps.lower()]
    srcs = [img.get("src") or img.get("data-src") or "" for img in soup.find_all("img")]
    srcs = [urljoin(a["url"], s) for s in srcs if "/print/" in s]
    textes = []
    for s in srcs[:3]:
        ri = get(s)
        if ri:
            textes.append(ocr(ri.content))
    a["texte"] = "\n".join(textes)[:20000]
    return a


def parse_funere(html, vus):
    base = "https://www.funere.com"
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.select('a[href*="/avis-de-deces/geneve/"]'):
        href = a["href"].split("?")[0]
        m = re.search(r"/avis-de-deces/geneve/([a-z0-9\-]+)/([a-z0-9\-]+)/?$", href)
        if not m or m.group(2) in vus:
            continue
        vus.add(m.group(2))
        bloc = a
        for _ in range(5):
            t = bloc.get_text(" · ", strip=True)
            if " ans" in t and len(t) < 500:
                break
            if bloc.parent is None:
                break
            bloc = bloc.parent
        t = bloc.get_text(" · ", strip=True)
        age = re.search(r"(\d{1,3})\s*ans", t)
        nom = next((x.strip() for x in t.split("·") if x.strip() and not re.search(r"\d", x)), m.group(2))
        com = re.search(r"·\s*([A-ZÀ-Ý][^·]*?),\s*GE", t)
        out.append({"source": "funere", "slug": m.group(2), "nom": nom, "url": urljoin(base, href),
                    "date_deces": date_fr(t), "date_publication": AUJ, "age": int(age.group(1)) if age else None,
                    "commune": com.group(1).strip() if com else m.group(1).replace("-", " ").title(), "texte": ""})
    return out


def lister_funere():
    """funere.com est protégé (Vercel) : on passe par un vrai navigateur."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("funere : playwright absent")
        return []
    avis, vus = [], set()
    with sync_playwright() as pw:
        nav = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        page = nav.new_page(locale="fr-CH", user_agent=WEB.headers["User-Agent"], viewport={"width": 1300, "height": 900})
        for n in range(1, 25):
            url = "https://www.funere.com/ch-fr/avis-de-deces/geneve" + (f"?page={n}" if n > 1 else "")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_selector('a[href*="/avis-de-deces/geneve/"]', timeout=45000)
            except Exception as e:
                debug("funere_playwright", f"page {n}: {e} — titre: {page.title()}")
                break
            lot = parse_funere(page.content(), vus)
            anciens = [x for x in lot if x["date_deces"] and x["date_deces"] < DATE_DEBUT]
            avis += [x for x in lot if x not in anciens]
            print(f" funere page {n}: {len(lot)} avis, {len(anciens)} anciens")
            if not lot or len(anciens) == len(lot):
                break
            time.sleep(PAUSE)
        nav.close()
    return avis


# ------------------------------------------------------------ mail
def mail(nouvelles):
    u, pw, to = os.environ.get("GMAIL_USER"), os.environ.get("GMAIL_APP_PASSWORD"), os.environ.get("MAIL_TO")
    if not (u and pw and to and nouvelles):
        return
    m = EmailMessage()
    m["Subject"] = f"Veille décès — {len(nouvelles)} nouvelle(s) piste(s) — {AUJ:%d.%m.%Y}"
    m["From"], m["To"] = u, to
    corps = "\n".join(f"- [{c['score']}] {c['niveau']} : {c['avis_nom']} -> {c['prop']}" for c in nouvelles)
    site = os.environ.get("SITE_URL", "")
    m.set_content(f"Nouvelles pistes :\n\n{corps}\n\nDétails : {site}\n")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(u, pw)
        s.send_message(m)
    print("Mail envoyé")


# ------------------------------------------------------------ main
def main():
    passage = sb_insert("passages", {"source": ("rattrapage " if os.environ.get("RATTRAPAGE") == "1" else "") + f"{DATE_DEBUT:%d.%m} → {DATE_FIN:%d.%m.%Y}", "statut": "en cours"})
    if os.environ.get("DIAGNOSTIC") == "1":
        diagnostic_hommages()
        diagnostic_funere()
    try:
        base = Base()
        existants = sb_get_all("avis", "source,slug")
        deja = defaultdict(set)
        for e in existants:
            deja[e["source"]].add(e["slug"])
        print(f"Période : {DATE_DEBUT} -> {DATE_FIN} — {len(existants)} avis déjà en base")
        cols = ["source", "slug", "nom", "url", "date_publication", "date_deces", "age", "commune", "journaux", "texte"]
        nouvelles, nb_c, nb_vus, nb_nouv = [], 0, 0, 0

        def traiter(lot):
            nonlocal nb_c
            enreg = sb_upsert("avis", [{c: a.get(c) for c in cols} for a in lot], "source,slug")
            ids = {(e["source"], e["slug"]): e["id"] for e in enreg}
            corr = []
            for a in lot:
                for c in analyser(base, a):
                    c["avis_id"] = ids[(a["source"], a["slug"])]
                    corr.append(c)
                    nouvelles.append({**c, "avis_nom": a["nom"]})
            sb_upsert("correspondances", corr, "avis_id,proprietaire_id", ignorer=True, retour=False)
            nb_c += len(corr)

        for nom_src, lister, lire in (("hommages", lister_hommages, lire_hommages), ("funere", lister_funere, None)):
            if nom_src == "funere" and os.environ.get("SANS_FUNERE") == "1":
                continue
            try:
                liste = lister()
            except Exception as e:
                traceback.print_exc()
                debug(f"erreur_{nom_src}", str(e))
                continue
            nb_vus += len(liste)
            a_faire = [a for a in liste if a["slug"] not in deja[nom_src]]
            nb_nouv += len(a_faire)
            print(f"{nom_src}: {len(liste)} avis, {len(a_faire)} nouveaux")
            lot = []
            for k, a in enumerate(a_faire, 1):
                if lire:
                    try:
                        lire(a)
                    except Exception as e:
                        print("   lecture impossible", a["slug"], e)
                lot.append(a)
                if len(lot) >= 25:
                    traiter(lot)
                    lot = []
                    print(f"  {k}/{len(a_faire)} enregistrés")
            if lot:
                traiter(lot)
        nouveaux = [None] * nb_nouv
        tous = [None] * nb_vus
        # noms des propriétaires pour le mail
        if nouvelles:
            ids = ",".join(str(c["proprietaire_id"]) for c in nouvelles)
            noms = {r["id"]: f"{r['nom']} {r['prenoms'] or ''}" for r in
                    requests.get(f"{SB_URL}/rest/v1/proprietaires?select=id,nom,prenoms&id=in.({ids})", headers=SB).json()}
            for c in nouvelles:
                c["prop"] = noms.get(c["proprietaire_id"], "?")
            if os.environ.get("RATTRAPAGE") != "1":
                mail(sorted(nouvelles, key=lambda c: -c["score"]))
        msg = f"{len(tous)} avis vus, {len(nouveaux)} nouveaux, {nb_c} correspondances"
        print(msg)
        sb_patch("passages", passage["id"], {"fin": dt.datetime.utcnow().isoformat() + "Z", "nb_avis": len(nouveaux),
                                             "nb_correspondances": nb_c, "statut": "ok", "message": msg})
    except Exception as e:
        traceback.print_exc()
        sb_patch("passages", passage["id"], {"fin": dt.datetime.utcnow().isoformat() + "Z", "statut": "erreur",
                                             "message": str(e)[:500]})
        sys.exit(1)


if __name__ == "__main__":
    main()
