// Essai de bout en bout de la carte de gestion de crise (lot C1), hors pytest : Edge sans affichage piloté par le
// protocole DevTools (WebSocket intégré à Node 22, aucune bibliothèque), serveur SIG lancé sur le port 8020.
// Déroule les trois scénarios de recette (Bastille, République, Stade de France), écrit les compteurs et des captures.
// Prérequis : interface construite (lancer.cmd ou npm run build), maquettes déjà traitées, caches des couches.
// Usage (PowerShell, dans le dossier du projet) : node tests\sig\essai_crise_edge.mjs . $env:TEMP
import { spawn, execSync } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const [projet, sortie] = process.argv.slice(2);
const PORT = 8020, CDP = 9334, URL_APP = `http://127.0.0.1:${PORT}/#vue=crise`;
const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const pause = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log('·', ...a);

const serveur = spawn(join(projet, '.venv/Scripts/python.exe'), ['-m', 'uvicorn', 'serveur.sig.app:app', '--port', String(PORT)], { cwd: projet, stdio: 'ignore' });
const profil = mkdtempSync(join(tmpdir(), 'edge-crise-'));
const edge = spawn(EDGE, ['--headless=new', `--remote-debugging-port=${CDP}`, `--user-data-dir=${profil}`, '--window-size=1600,1000', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', 'about:blank'], { stdio: 'ignore' });
const fin = () => { try { execSync(`taskkill /PID ${serveur.pid} /T /F`, { stdio: 'ignore' }); } catch {} try { execSync(`taskkill /PID ${edge.pid} /T /F`, { stdio: 'ignore' }); } catch {} };

try {
  for (let i = 0; i < 120; i++) { try { if ((await fetch(`http://127.0.0.1:${PORT}/api/sig/sante`)).ok) break; } catch {} await pause(500); }
  let cible;
  for (let i = 0; i < 40 && !cible; i++) { try { cible = (await (await fetch(`http://127.0.0.1:${CDP}/json`)).json()).find((t) => t.type === 'page'); } catch {} await pause(250); }
  const ws = new WebSocket(cible.webSocketDebuggerUrl);
  await new Promise((r) => ws.addEventListener('open', r));
  let id = 0; const attente = new Map();
  ws.addEventListener('message', (m) => { const d = JSON.parse(m.data); if (d.id && attente.has(d.id)) { attente.get(d.id)(d); attente.delete(d.id); } if (d.method === 'Runtime.exceptionThrown') log('EXCEPTION', d.params.exceptionDetails.exception?.description?.slice(0, 300)); if (d.method === 'Runtime.consoleAPICalled' && d.params.type === 'error') log('console.error', JSON.stringify(d.params.args.map((a) => a.value ?? a.description)).slice(0, 300)); });
  const cmd = (method, params = {}) => new Promise((r) => { id += 1; attente.set(id, r); ws.send(JSON.stringify({ id, method, params })); });
  const evaluer = async (expr) => (await cmd('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true })).result?.result?.value;
  const photo = async (nom) => { const r = await cmd('Page.captureScreenshot', { format: 'png' }); writeFileSync(join(sortie, nom), Buffer.from(r.result.data, 'base64')); log('photo', nom); };
  const cliquerTexte = (texte) => evaluer(`(() => { const b = [...document.querySelectorAll('button')].find((x) => x.textContent.includes(${JSON.stringify(texte)})); if (!b) return false; b.click(); return true; })()`);
  const souris = (type, x, y, buttons = 0, mod = 0) => cmd('Input.dispatchMouseEvent', { type, x, y, button: 'left', buttons, clickCount: 1, modifiers: mod });
  await cmd('Runtime.enable'); await cmd('Page.enable');
  await cmd('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false });
  await cmd('Page.navigate', { url: URL_APP });
  for (let i = 0; i < 180 && !(await evaluer(`!!document.querySelector('select.sig-champ')`)); i++) await pause(1000);
  log('volet crise :', await evaluer(`!!document.querySelector('select.sig-champ')`));

  const chercher = async (texte, choix) => {
    await evaluer(`(() => { const q = document.querySelector('input[type=search][placeholder^="Station"]'); q.value = ${JSON.stringify(texte)}; q.dispatchEvent(new Event('input')); return true; })()`);
    for (let i = 0; i < 20 && !(await cliquerTexte(choix)); i++) await pause(500);
  };
  const nouveau = async (titre) => {
    await evaluer(`(() => { const t = document.querySelector('input.sig-champ[type=text]'); t.value = ${JSON.stringify(titre)}; return true; })()`);
    await cliquerTexte('Placer l’événement sur la carte');
    await pause(300);
  };
  const synthese = () => evaluer(`[...document.querySelectorAll('.sig-crise-table tbody tr')].map((t) => [...t.cells].map((c) => c.textContent).join(' ')).join(' | ')`);
  const attendre = async () => { for (let i = 0; i < 30 && await evaluer(`document.body.innerText.includes('Analyse en cours')`); i++) await pause(500); await pause(2500); };

  // A — Bastille : recherche, puis tracé au rayon libre depuis le centre de la carte
  await nouveau('Explosion place de la Bastille');
  await chercher('Bastille', 'Station : Bastille');
  await attendre();
  const [x, y] = await evaluer(`(() => { const c = document.querySelector('.sig-carte canvas').getBoundingClientRect(); return [c.left + c.width / 2, c.top + c.height / 2]; })()`);
  await cliquerTexte('Replacer l’événement');
  await pause(300);
  await souris('mousePressed', x, y, 1);
  for (let i = 1; i <= 12; i++) { await souris('mouseMoved', x + i * 14, y + i * 3, 1); await pause(50); }
  log('A pendant le glissement :', await synthese());
  await photo('crise_A1_glissement.png');
  await souris('mouseReleased', x + 168, y + 36, 0);
  await attendre();
  log('A rayon tracé :', await evaluer(`document.querySelector('.sig-crise-rayon').value`));
  await cliquerTexte('+ Cercle');
  await attendre();
  log('A synthèse :', await synthese());
  await photo('crise_A2_bastille.png');
  log('A stations :', (await evaluer(`[...document.querySelectorAll('.sig-crise-categorie')][0]?.innerText`))?.slice(0, 400).replace(/\n/g, ' / '));
  await cliquerTexte('Fermer l’événement');
  await pause(500);

  // B — République (PLAI)
  await nouveau('Colis suspect place de la République');
  await chercher('République', 'Station : République');
  await attendre();
  log('B synthèse :', await synthese());
  log('B PLAI :', (await evaluer(`[...document.querySelectorAll('.sig-crise-categorie')].find((d) => d.innerText.startsWith('Points'))?.innerText`))?.slice(0, 500).replace(/\n/g, ' / '));
  await photo('crise_B_republique.png');
  await cliquerTexte('Fermer l’événement');
  await pause(500);

  // C — Stade de France (maquettes), par coordonnées, 1 km
  await nouveau('Incendie gare Stade de France');
  await evaluer(`(() => { const c = document.querySelector('input.sig-champ[placeholder^="X Y"]'); c.value = '48.9179, 2.3631'; return true; })()`);
  await cliquerTexte('Placer');
  await attendre();
  await cliquerTexte('1 km');
  await attendre();
  log('C synthèse :', await synthese());
  log('C ouvrages :', (await evaluer(`[...document.querySelectorAll('.sig-crise-categorie')].find((d) => d.innerText.startsWith('Ouvrages'))?.innerText`))?.slice(0, 500).replace(/\n/g, ' / '));
  await photo('crise_C_stade_de_france.png');
  await cliquerTexte('Fermer l’événement');
  await pause(500);

  // D — simple clic sans glisser (cercle par défaut), directement sur la carte, sans recherche
  await nouveau('Simple clic');
  await souris('mousePressed', x, y, 1);
  await souris('mouseReleased', x, y, 0);
  await attendre();
  log('D rayon :', await evaluer(`document.querySelector('.sig-crise-rayon')?.value`));
  log('D synthèse :', await synthese());
  log('D état :', await evaluer(`document.querySelector('.sig-crise-table') ? 'tableau affiché' : document.body.innerText.match(/Analyse[^
]*/)?.[0]`));
  await photo('crise_D_simple_clic.png');
  await cliquerTexte('Fermer l’événement');
  await pause(800);

  // E — lot C2 : décisions en masse et unitaires, élargissement (changements), note, exports
  // téléchargements autorisés dans le dossier de sortie (commande du navigateur, pas de la page)
  const nav = new WebSocket((await (await fetch(`http://127.0.0.1:${CDP}/json/version`)).json()).webSocketDebuggerUrl);
  await new Promise((r) => nav.addEventListener('open', r));
  nav.send(JSON.stringify({ id: 1, method: 'Browser.setDownloadBehavior', params: { behavior: 'allow', downloadPath: sortie, eventsEnabled: true } }));
  await pause(300);
  await nouveau('Explosion Bastille C2');
  await chercher('Bastille', 'Station : Bastille');
  await attendre();
  const categorie = (debut) => `[...document.querySelectorAll('.sig-crise-categorie')].find((d) => d.querySelector('summary')?.textContent.startsWith(${JSON.stringify(debut)}))`;
  log('E masse accès :', await evaluer(`(() => { const d = ${categorie('Accès des stations')}; d.open = true; const b = d.querySelector('.sig-crise-masse button'); b.click(); return d.querySelector('summary').textContent; })()`));
  await pause(500);
  log('E station :', await evaluer(`(() => { const d = ${categorie('Stations')}; const s = d.querySelector('.sig-crise-decision select'); s.value = 'ferme'; s.dispatchEvent(new Event('change')); return ${categorie('Stations')}.querySelector('summary').textContent; })()`));
  await pause(500);
  await cliquerTexte('600 m');
  await attendre();
  log('E changements :', await evaluer(`[...document.querySelectorAll('.sig-crise-changements p, .sig-crise-changements li')].map((x) => x.textContent).join(' / ')`));
  await evaluer(`(() => { const t = [...document.querySelectorAll('textarea')].find((x) => x.placeholder.startsWith('Note')); t.value = 'Préfecture prévenue, périmètre de 600 m demandé'; return true; })()`);
  await cliquerTexte('Ajouter à la main courante');
  await pause(1500);
  log('E main courante :', await evaluer(`[...document.querySelectorAll('.sig-crise-journal li')].slice(0, 8).map((l) => l.textContent).join(' / ')`));
  log('E sauvegarde :', await evaluer(`[...document.querySelectorAll('.sig-section p')].map((p) => p.textContent).find((t) => t.startsWith('Enregistr'))`));
  await photo('crise_E_c2.png');
  // fichiers produits capturés dans la page (Edge sans affichage n'écrit pas les téléchargements de blob)
  await evaluer(`(() => { window.__fichiers = [];
    const garder = (a) => { const nom = a.download; fetch(a.href).then((r) => r.blob()).then((b) => new Promise((ok) => { const f = new FileReader(); f.onload = () => ok(f.result); f.readAsDataURL(b); }))
      .then((u) => window.__fichiers.push({ nom, donnees: u.slice(u.indexOf(',') + 1) })); };
    HTMLAnchorElement.prototype.click = function () { garder(this); };
    const envoi = HTMLAnchorElement.prototype.dispatchEvent;   // jsPDF déclenche un faux clic
    HTMLAnchorElement.prototype.dispatchEvent = function (ev) { if (ev.type === 'click' && this.download) { garder(this); return true; } return envoi.call(this, ev); };
    return true; })()`);
  for (const b of ['PDF', 'Excel', 'GeoJSON du périmètre']) { log('E export', b, await cliquerTexte(b)); await pause(5000); }
  for (const f of (await evaluer('window.__fichiers')) || []) {
    writeFileSync(join(sortie, f.nom), Buffer.from(f.donnees, 'base64'));
    log('E fichier', f.nom, Buffer.from(f.donnees, 'base64').length, 'octets');
  }
  log('E journal après exports :', await evaluer(`[...document.querySelectorAll('.sig-crise-journal li')].slice(0, 3).map((l) => l.textContent).join(' / ')`));
  log('E erreurs :', await evaluer(`[...document.querySelectorAll('.sig-erreur')].map((x) => x.textContent).join(' | ')`));
} catch (e) {
  log('ERREUR', e.message);
} finally {
  fin();
  process.exit(0);
}
