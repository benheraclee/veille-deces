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
LIMITE = AUJ - dt.timedelta(days=JOURS)

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
        self.index = defaultdict(list)
        for p in self.pers:
            p["homonymes"] = freq[" ".join(p["S"])]
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
    for p, pos in base.candidats(N):
        if pos == 0:
            continue
        autres = N[:pos] + N[pos + len(p["S"]):]
        if p["F1"] and p["F1"] in autres:
            niv, sc = "DÉFUNT — nom + 1er prénom", 90
        elif any(f in autres for f in p["F"]):
            niv, sc = "DÉFUNT — nom + prénom secondaire", 75
        elif p["homonymes"] <= 2 and len("".join(p["S"])) >= 6:
            niv, sc = "MÊME NOM — nom rare, autre prénom", 40
        else:
            continue
        res.append((p, niv, sc, avis["nom"]))
        vus.add(p["id"])
    T = tok(avis.get("texte"))
    for p, pos in base.candidats(T):
        if p["id"] in vus or len("".join(p["S"])) < 4:
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
def hommages(deja):
    base = "https://www.hommages.ch"
    avis, vus = [], set()
    for n in range(1, 80):
        r = get(f"{base}/fr/avis-de-deces" + (f"?page={n}" if n > 1 else ""))
        if not r:
            break
        soup = BeautifulSoup(r.text, "html.parser")
        items, vieux = 0, 0
        for a in soup.select('a[href*="/avis-de-deces/"]'):
            m = re.search(r"/fr/avis-de-deces/([a-z0-9\-]+)/?$", a["href"].split("?")[0])
            if not m or m.group(1) in vus:
                continue
            vus.add(m.group(1))
            bloc = a
            for _ in range(6):
                if bloc.parent is None:
                    break
                bloc = bloc.parent
                t = bloc.get_text(" ", strip=True)
                if re.search(r"\d{4}", t) and len(t) < 600:
                    break
            d = date_fr(bloc.get_text(" ", strip=True))
            items += 1
            if d and d < LIMITE:
                vieux += 1
                continue
            lignes = [l.strip() for l in a.get_text("\n").split("\n") if l.strip()]
            nom = next((l for l in lignes if not re.search(r"\d|publication|avis|bougie", l, re.I)), m.group(1))
            avis.append({"source": "hommages", "slug": m.group(1), "nom": nom,
                         "url": urljoin(base, a["href"]), "date_publication": d})
        print(f" hommages page {n}: {items} avis, {vieux} anciens")
        if items == 0 or (vieux and vieux == items):
            break
        time.sleep(PAUSE)
    # lecture des avis (journal + image -> OCR)
    for k, a in enumerate(avis, 1):
        if a["slug"] in deja:
            a["_skip"] = True
            continue
        r = get(a["url"])
        time.sleep(PAUSE)
        if not r:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        corps = soup.get_text(" ", strip=True)
        a["journaux"] = [j for j in JOURNAUX if j.lower() in corps.lower()]
        a["date_publication"] = a["date_publication"] or date_fr(corps)
        if not set(a["journaux"]) & JOURNAUX_OCR:
            continue
        srcs = [img.get("src") or img.get("data-src") or "" for img in soup.find_all("img")]
        srcs = [urljoin(a["url"], s) for s in srcs if "/print/" in s]
        textes = []
        for s in srcs[:3]:
            ri = get(s)
            if ri:
                textes.append(ocr(ri.content))
        a["texte"] = "\n".join(textes)[:20000]
        if k % 20 == 0:
            print(f"  {k}/{len(avis)} avis hommages lus")
    return avis


def funere(deja):
    base = "https://www.funere.com"
    avis, vus = [], set()
    for n in range(1, 25):
        r = get(f"{base}/ch-fr/avis-de-deces/geneve" + (f"?page={n}" if n > 1 else ""))
        if not r:
            break
        soup = BeautifulSoup(r.text, "html.parser")
        items, vieux = 0, 0
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
            d = date_fr(t)
            items += 1
            if d and d < LIMITE:
                vieux += 1
                continue
            ans = re.findall(r"(19\d{2}|20\d{2})", m.group(2))
            age = re.search(r"(\d{1,3})\s*ans", t)
            nom = next((x.strip() for x in t.split("·") if x.strip() and not re.search(r"\d", x)), m.group(2))
            com = re.search(r"·\s*([A-ZÀ-Ý][^·]*?),\s*GE", t)
            avis.append({"source": "funere", "slug": m.group(2), "nom": nom, "url": urljoin(base, href),
                         "date_deces": d, "date_publication": AUJ if m.group(2) not in deja else None,
                         "age": int(age.group(1)) if age else None,
                         "commune": com.group(1).strip() if com else m.group(1).replace("-", " ").title(),
                         "texte": "", "annee_naissance": int(ans[0]) if len(ans) >= 2 else None})
        print(f" funere page {n}: {items} avis, {vieux} anciens")
        if items == 0 or (vieux and vieux == items):
            break
        time.sleep(PAUSE)
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
    passage = sb_insert("passages", {"source": "hommages+funere", "statut": "en cours"})
    try:
        base = Base()
        existants = sb_get_all("avis", "source,slug", f"&lu_le=gte.{(AUJ - dt.timedelta(days=JOURS + 10)).isoformat()}")
        deja = defaultdict(set)
        for e in existants:
            deja[e["source"]].add(e["slug"])
        tous = []
        for nom_src, fn in (("hommages", hommages), ("funere", funere)):
            try:
                lot = fn(deja[nom_src])
                print(f"{nom_src}: {len(lot)} avis")
                tous += lot
            except Exception as e:
                traceback.print_exc()
                print(f"Source {nom_src} en erreur: {e}")
        nouveaux = [a for a in tous if not a.get("_skip") and a["slug"] not in deja[a["source"]]]
        cols = ["source", "slug", "nom", "url", "date_publication", "date_deces", "age", "commune", "journaux", "texte"]
        nouvelles, nb_c = [], 0
        for i in range(0, len(nouveaux), 50):
            lot = nouveaux[i:i + 50]
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
        # noms des propriétaires pour le mail
        if nouvelles:
            ids = ",".join(str(c["proprietaire_id"]) for c in nouvelles)
            noms = {r["id"]: f"{r['nom']} {r['prenoms'] or ''}" for r in
                    requests.get(f"{SB_URL}/rest/v1/proprietaires?select=id,nom,prenoms&id=in.({ids})", headers=SB).json()}
            for c in nouvelles:
                c["prop"] = noms.get(c["proprietaire_id"], "?")
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
