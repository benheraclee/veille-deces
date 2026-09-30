"""Charge le registre officiel des adresses de bâtiments (swisstopo) pour le canton de Genève dans Supabase (table adresses_ge)."""
import os, io, csv, json, zipfile, requests

SB_URL = os.environ["SUPABASE_URL"].rstrip("/")
SB_KEY = os.environ["SUPABASE_SERVICE_KEY"]
SB = {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json"}


def debug(cle, contenu):
    requests.post(f"{SB_URL}/rest/v1/debug", headers=SB, data=json.dumps({"cle": cle, "contenu": str(contenu)[:60000]}), timeout=30)


# 1) trouver le fichier CSV via l'API STAC de geo.admin.ch
coll = "https://data.geo.admin.ch/api/stac/v0.9/collections/ch.swisstopo.amtliches-gebaeudeadressverzeichnis/items"
items = requests.get(coll, timeout=60).json()
assets = [a for it in items.get("features", []) for a in it.get("assets", {}).values()]
debug("geo_assets", json.dumps([a.get("href") for a in assets], ensure_ascii=False))
href = next((a["href"] for a in assets if "csv" in a["href"].lower()), None)
if not href:
    raise SystemExit("pas de CSV trouvé")
print("Téléchargement", href)
data = requests.get(href, timeout=600).content
z = zipfile.ZipFile(io.BytesIO(data))
nom = next(n for n in z.namelist() if n.lower().endswith(".csv"))
texte = z.read(nom).decode("utf-8-sig", errors="replace")
delim = ";" if texte[:2000].count(";") > texte[:2000].count(",") else ","
rd = csv.DictReader(io.StringIO(texte), delimiter=delim)
h = rd.fieldnames
debug("geo_header", json.dumps({"fichier": nom, "colonnes": h}, ensure_ascii=False))


def col(*mots):
    for m in mots:
        for c in h:
            if c.upper() == m:
                return c
    for m in mots:
        for c in h:
            if m in c.upper():
                return c
    return None


C = {"egaid": col("EGAID", "ADR_EGAID"), "rue": col("STN_LABEL", "STR_LABEL", "STRNAME"),
     "numero": col("ADR_NUMBER", "DEINR", "NUMBER"), "npa": col("ZIP_LABEL", "PLZ", "ZIP"),
     "commune": col("COM_NAME", "GDENAME", "COM_LABEL"), "canton": col("COM_CANTON", "CANTON", "GDEKT"),
     "e": col("ADR_EASTING", "EASTING", "GKODE"), "n": col("ADR_NORTHING", "NORTHING", "GKODN")}
debug("geo_colonnes", json.dumps(C))

lot, total = [], 0


def envoyer(lot):
    r = requests.post(f"{SB_URL}/rest/v1/adresses_ge?on_conflict=egaid",
                      headers={**SB, "Prefer": "resolution=merge-duplicates,return=minimal"}, data=json.dumps(lot), timeout=120)
    if r.status_code >= 300:
        raise SystemExit(f"{r.status_code} {r.text[:300]}")


premier = None
for row in rd:
    premier = premier or row
    zipl = (row.get(C["npa"]) or "").strip()
    npa = zipl[:4]
    canton = (row.get(C["canton"]) or "").strip() if C["canton"] else ""
    if canton and canton != "GE":
        continue
    if not canton and not (npa.isdigit() and 1200 <= int(npa) <= 1299):
        continue
    try:
        e, n = float(row[C["e"]]), float(row[C["n"]])
    except (TypeError, ValueError, KeyError):
        continue
    lot.append({"egaid": (row.get(C["egaid"]) or f"{row.get(C['rue'])}|{row.get(C['numero'])}|{npa}"),
                "rue": row.get(C["rue"]), "numero": row.get(C["numero"]), "npa": npa,
                "localite": zipl[4:].strip() or None, "commune": row.get(C["commune"]) if C["commune"] else None,
                "e": e, "n": n})
    if len(lot) >= 1000:
        envoyer(lot); total += len(lot); lot = []
if lot:
    envoyer(lot); total += len(lot)
debug("geo_resultat", json.dumps({"adresses_ge": total, "exemple": premier}, ensure_ascii=False))
print("Adresses GE chargées :", total)
