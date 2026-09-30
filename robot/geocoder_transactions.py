"""Géocode les transactions par lots (adresse -> point), via la fonction SQL geocoder_lot."""
import os, json, requests
SB_URL = os.environ["SUPABASE_URL"].rstrip("/")
SB = {"apikey": os.environ["SUPABASE_SERVICE_KEY"], "Authorization": f"Bearer {os.environ['SUPABASE_SERVICE_KEY']}", "Content-Type": "application/json"}
mx = requests.get(f"{SB_URL}/rest/v1/transactions?select=id&order=id.desc&limit=1", headers=SB, timeout=60).json()[0]["id"]
tot = {"adresse": 0, "rue": 0, "parcelle": 0}
import time
for debut in range(0, mx + 1, 5000):
    for essai in range(4):
        try:
            r = requests.post(f"{SB_URL}/rest/v1/rpc/geocoder_lot", headers=SB, data=json.dumps({"debut": debut, "fin": debut + 5000}), timeout=700)
        except requests.RequestException as e:
            print(debut, "erreur", e); time.sleep(20); continue
        print(debut, r.status_code, r.text[:200])
        if r.ok:
            for k, v in r.json().items(): tot[k] += v
            break
        time.sleep(20)
print("TOTAL", tot)
requests.post(f"{SB_URL}/rest/v1/debug", headers=SB, data=json.dumps({"cle": "geo_transactions", "contenu": json.dumps(tot)}), timeout=30)
