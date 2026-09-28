# Veille décès × propriétaires — Genève

- `index.html` : site privé (connexion Supabase) — pistes, avis, base, import Popety, suivi du robot
- `robot/veille.py` : robot quotidien (hommages.ch + funere.com, OCR, recoupement) — GitHub Actions
- `.github/workflows/veille.yml` : planification (tous les jours 7h10) + lancement manuel

Données : Supabase `veille-deces-geneve` (Zurich). Aucune donnée dans ce dépôt.
Accès aux données limité aux e-mails de la table `acces_autorises`.

Secrets GitHub (Settings → Secrets and variables → Actions) :
- `SUPABASE_SERVICE_KEY` (obligatoire) — Supabase → Project Settings → API keys → service_role / secret
- `GMAIL_USER`, `GMAIL_APP_PASSWORD`, `MAIL_TO` (optionnels, mail quand il y a de nouvelles pistes)
- Variable `SITE_URL` (optionnel) — lien du site dans le mail
