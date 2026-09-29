// Parseur des publications foncières (registre foncier Genève) — texte copié-collé depuis publications-foncieres.app.ge.ch
(function (global) {
  const MOIS = { janvier: 1, "février": 2, fevrier: 2, mars: 3, avril: 4, mai: 5, juin: 6, juillet: 7, "août": 8, aout: 8,
    septembre: 9, octobre: 10, novembre: 11, "décembre": 12, decembre: 12 };
  const iso = (j, m, a) => `${a}-${String(m).padStart(2, "0")}-${String(j).padStart(2, "0")}`;
  const num = s => s == null ? null : Number(String(s).replace(/['’\s]/g, "").replace(",", "."));
  const OPS = /(Achat|Héritage|Heritage|Donation|Partage|Fusion|Jugement|Fixations? de parts|Transfert|Délivrance de legs|Echange|Échange|Apport|Cession|Vente aux enchères|Adjudication|Constitution de PPE|Division|Réunion|Avancement d'hoirie|Liquidation|Dissolution|Scission|Attribution)/gi;
  const SOCIETE = /\b(SA|S\.A\.|SARL|SÀRL|Sàrl|AG|GMBH|GmbH|SNC|SCI|LTD|LIMITED|INC|FONDATION|COOPERATIVE|COOPÉRATIVE|CAISSE|HOLDING|IMMOBILI[EÈ]RE|INVEST\w*|FUND|FONDS|PROPERTIES|DEVELOPPEMENT|CONSTRUCTIONS?|COMMUNE|ETAT DE|ÉTAT DE|CONFEDERATION)\b/;

  // communes RF -> communes Popety
  function communePopety(c) {
    c = c.trim();
    if (/^Genève-/.test(c)) return "Genève";
    const map = { "Grand-Saconnex": "Le Grand-Saconnex", "Carouge": "Carouge (GE)", "Corsier": "Corsier (GE)",
      "Vandœuvres": "Vandoeuvres" };
    return map[c] || c;
  }

  function parse(texte) {
    const lignes = texte.replace(/\r/g, "").split("\n");
    const out = [];
    let datePub = null, rectif = null;
    for (const brut of lignes) {
      const l = brut.trim();
      if (!l) continue;
      const mp = l.match(/^(\d{1,2})\s+(janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre)\s+(\d{4})$/i);
      if (mp) { datePub = iso(mp[1], MOIS[mp[2].toLowerCase()], mp[3]); rectif = null; continue; }
      const mr = l.match(/^Rectification de la publication du (\d{2})\.(\d{2})\.(\d{4})/);
      if (mr) { rectif = iso(mr[1], mr[2], mr[3]); continue; }
      if (!/Affaire\s+\d{4}\/\d+\/\d+/.test(l) || !/^\d{2}\.\d{2}\.\d/.test(l)) continue;
      out.push(analyser(l, datePub, rectif));
    }
    // dédoublonne sur n° d'affaire (garde la dernière version, ex. rectification)
    const parAffaire = new Map();
    out.forEach(v => parAffaire.set(v.affaire, v));
    return [...parAffaire.values()];
  }

  function analyser(l, datePub, rectif) {
    const v = { texte: l, date_publication: datePub, rectification_du: rectif };
    const md = l.match(/^(\d{2})\.(\d{2})\.(\d{4})/) || l.replace(/^(\d{2}\.\d{2}\.\d{3})[A-Z](\d)/, "$1$2").match(/^(\d{2})\.(\d{2})\.(\d{4})/);
    v.date_acte = md ? iso(md[1], md[2], md[3]) : null;
    v.affaire = l.match(/Affaire\s+(\d{4}\/\d+\/\d+)/)[1];
    const entete = l.slice(0, l.indexOf("Affaire"));
    v.communes = [...entete.matchAll(/([A-ZÀ-Ýa-zà-ÿ'’\- ]+),\s*\d+\s*-/g)].map(m => m[1].replace(/^[\s-]+/, "").trim()).filter(c => c && !/^\d/.test(c));
    // prix
    const pt = l.match(/Prix total de l'affaire\s*:\s*([\d'’]+)/i);
    if (pt) v.prix = num(pt[1]);
    else {
      const ps = [...l.matchAll(/Prix\s*:\s*([\d'’]+)/gi)].map(m => num(m[1]));
      v.prix = ps.length ? ps.reduce((a, b) => a + b, 0) : null;
    }
    v.frais_annexes = /frais annexes/i.test(l);
    // opérations
    const corps = l.slice(l.indexOf("Affaire"));
    v.operations = [...new Set([...corps.matchAll(OPS)].map(m => m[1].replace(/^Heritage$/, "Héritage").replace(/^Fixation de parts$/i, "Fixations de parts")))]
      .filter(o => new RegExp(o.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\.?\\s*Ancien", "i").test(corps) || o === "Achat");
    if (!v.operations.length) { const m = corps.match(/\.\s*([A-ZÉ][^.]{2,40})\.\s*Ancien/); if (m) v.operations = [m[1]]; }
    // parties : un bloc par opération (Achat, Héritage, Partage…)
    const nettoie = t => t.replace(/,?\s*(cop\.?\s*[\d.,/]+(\s*chacun)?|en communauté héréditaire|communauté héréditaire|droits indivis|en nue-propriété|en usufruit)\s*/gi, ", ").replace(/(,\s*)+/g, ", ").replace(/^[,\s]+|[,\s.]+$/g, "").trim();
    const blocs = [];
    for (const m of corps.matchAll(/(?:^|[.;]\s*)([A-ZÉ][^.;]{2,40}?)\.\s*Ancien\(s\)\s*:\s*(.*?)[.,]?\s*Nouveau\(x\)\s*:\s*(.*?)(?=,?\s*(?:B-F|PPE|COP|DDP)\s)/g))
      blocs.push({ op: m[1].trim(), anciens: m[2].trim(), nouveaux: nettoie(m[3]) });
    v.anciens = blocs.length ? blocs[0].anciens : "";
    const achat = blocs.filter(b => /achat|transfert|vente|adjudication/i.test(b.op)).pop();
    v.nouveaux = (achat || blocs[blocs.length - 1] || { nouveaux: "" }).nouveaux;
    v.heritiers = /h[ée]ritage/i.test((blocs[0] || {}).op || "") ? blocs[0].nouveaux : null;
    v.heritage = /\bFeu\s/.test(l) || v.operations.includes("Héritage");
    v.defunt = (l.match(/Feu\s+([A-ZÀ-Ý][^,.]*?)(?:,|\.|\s+inscrit)/) || [])[1] || null;
    v.acheteur_societe = SOCIETE.test(v.nouveaux);
    v.vendeur_societe = SOCIETE.test(v.anciens);
    // détention la plus longue
    const inscrits = [...v.anciens.matchAll(/inscrit (?:dès )?le (\d{2})\.(\d{2})\.(\d{4})/g)].map(m => new Date(`${m[3]}-${m[2]}-${m[1]}`));
    if (inscrits.length && v.date_acte) {
      const plusAncien = new Date(Math.min(...inscrits));
      v.detention_annees = Math.round((new Date(v.date_acte) - plusAncien) / 3.15576e10 * 10) / 10;
    } else v.detention_annees = null;
    // immeubles
    v.immeubles = [];
    const re = /\b(B-F|PPE|DDP)\s+([A-ZÀ-Ýa-zà-ÿ'’\- ]+?),\s*(\d+)\/(\d+)((?:-[\w]+)*)(?:\s+sur\s+([\d,.'’]+\/[\d'’]+))?(?:,\s*([\d'’]+)\s*m2)?/g;
    const vus = new Set();
    for (const m of l.matchAll(re)) {
      const cle = `${m[1]}|${m[2]}|${m[4]}${m[5]}`;
      if (vus.has(cle)) continue;
      vus.add(cle);
      v.immeubles.push({ type: m[1], commune: m[2].trim(), commune_popety: communePopety(m[2]), feuille: m[3], numero: m[4],
        lot: m[5] ? m[5].slice(1) : null, quote: m[6] || null, surface: m[7] ? num(m[7]) : null });
    }
    v.type_principal = v.immeubles.some(i => i.type === "B-F") ? "B-F" : (v.immeubles[0] || {}).type || null;
    const bf = v.immeubles.filter(i => i.type === "B-F" && i.surface);
    v.surface_terrain = bf.length ? bf.reduce((a, i) => a + i.surface, 0) : null;
    v.prix_m2 = v.prix && v.surface_terrain && v.operations.includes("Achat") && v.type_principal === "B-F" ? Math.round(v.prix / v.surface_terrain) : null;
    if (v.prix_m2 != null && v.prix_m2 < 200) v.prix_m2 = null;  // achat d'une part de copropriété, pas significatif
    const adr = l.match(/,\s*((?:Chemin|Route|Rue|Avenue|Boulevard|Quai|Place|Promenade|Passage|Impasse|Plateau|Esplanade|Carrefour|Grand-Rue|Rampe|Sentier|Allée|Clos|Cours|Parc|Square)[^,;]*?\s\d+\w*),\s*(\d{4})\s+([^,;.]+)/);
    v.adresse = adr ? `${adr[1]}, ${adr[2]} ${adr[3].trim()}` : null;
    v.description = (l.match(/m2,\s*([^,;]+)/) || [])[1] || null;
    return v;
  }

  global.RF = { parse, communePopety };
  if (typeof module !== "undefined") module.exports = global.RF;
})(typeof window !== "undefined" ? window : globalThis);
