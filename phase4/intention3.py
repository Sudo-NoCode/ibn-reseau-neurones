import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'phase3'))
from intention import MAX, MIN, MOTS, NEGATION, NOMBRE, UNITES, _valeur

from jumeau3 import COEUR, DISTRIB, RTT_BASE, SERVICES

DEFAUT = dict(lat=60.0, pertes=2.0, gigue=15.0, debit=0.90)
PRESETS = {'excellente': dict(lat=40.0, pertes=0.5, gigue=8.0, debit=0.975), 'parfaite': dict(lat=40.0, pertes=0.5, gigue=8.0, debit=0.975),
           'bonne': dict(DEFAUT), 'correcte': dict(DEFAUT), 'acceptable': dict(lat=100.0, pertes=4.0, gigue=25.0, debit=0.75)}
NOMS = {'visio': r'(?:visio\w*|réunions?|reunions?|vidéo\w*|video\w*)', 'voix': r'(?:voix|téléphon\w*|telephon\w*|appels?)'}
CHEMINS = [(d, c) for d in DISTRIB for c in COEUR]
LAT_MIN = 10.0


@dataclass
class Intention3:
    service: str
    texte: str
    seuils: dict = field(default_factory=lambda: dict(DEFAUT))
    interdits: set = field(default_factory=set)
    compris: list = field(default_factory=list)
    non_compris: list = field(default_factory=list)
    refus: list = field(default_factory=list)

    @property
    def acceptee(self):
        return not self.refus

    def contrat(self):
        return dict(seuils=dict(self.seuils), interdits=set(self.interdits))

    def resume(self):
        s, mbps = self.seuils, SERVICES[self.service]['mbps']
        chemins = [f'{d}-{c}' for k, (d, c) in enumerate(CHEMINS) if f'chemin{k}' not in self.interdits]
        leviers = [n for n in ('priorite', 'brider') if n in self.interdits]
        lignes = [f'{self.service.upper()} : « {self.texte or "(rien de précisé)"} »',
                  f'  contrat   : latence ≤ {s["lat"]:g} ms, pertes ≤ {s["pertes"]:g} %, gigue ≤ {s["gigue"]:g} ms, '
                  f'débit ≥ {100 * s["debit"]:g} % ({s["debit"] * mbps:.2f} Mb/s)',
                  f'  chemins permis : {", ".join(chemins)} ; leviers interdits : {", ".join(leviers) or "aucun"}',
                  f'  compris   : {"; ".join(self.compris) or "rien de précis → contrat par défaut"}']
        if self.non_compris:
            lignes.append(f'  PAS compris : {"; ".join(self.non_compris)}')
        lignes.append('  → ACCEPTÉE' if self.acceptee else '  → REFUSÉE : ' + ' ; '.join(self.refus))
        return '\n'.join(lignes)


def _un_service(service, texte):
    it = Intention3(service, texte.strip(' ,;.'))
    t = ' ' + texte.lower().replace('’', "'") + ' '
    reste = re.sub(r'\b[hacd]\d\b', ' ', t)
    mbps = SERVICES[service]['mbps']

    for mot, preset in PRESETS.items():
        if re.search(rf'\b{mot}s?\b', t):
            it.seuils.update(preset); it.compris.append(f'qualité « {mot} »')
            reste = re.sub(rf'\b{mot}s?\b', ' ', reste)
    if re.search(r'sans coupure|fluide|sans saccade', t):
        it.seuils['pertes'] = min(it.seuils['pertes'], 1.0); it.seuils['gigue'] = min(it.seuils['gigue'], 10.0)
        it.compris.append('« fluide / sans coupure » → pertes ≤ 1 %, gigue ≤ 10 ms')
        reste = re.sub(r'sans coupure|fluide|sans saccade', ' ', reste)

    for m, mot in MOTS.items():
        unite = r'\s*(mb/?s|mbps|kb/?s|kbps|%)' if m == 'debit' else UNITES[m]
        for g in re.finditer(rf"{mot}[^,;.\d]{{0,25}}?({MAX}|{MIN})?\s*(?:de |d')?{NOMBRE}(?:{unite})?", t):
            sens, nombre, u = g.group(1), g.group(2), g.group(3)
            if sens and re.fullmatch(MIN, sens) and m != 'debit':
                it.non_compris.append(f"« {g.group(0).strip()} » : un minimum de {m} n'a pas de sens")
            elif m == 'debit':
                v = float(nombre.replace(',', '.')) / 100 if u == '%' else _valeur(nombre, u, m) / mbps
                it.seuils[m] = v; it.compris.append(f'débit ≥ {100 * v:g} % du débit du service')
            else:
                it.seuils[m] = _valeur(nombre, u, m); it.compris.append(f'{m} ≤ {it.seuils[m]:g}')
            reste = reste.replace(g.group(0), ' ')
    if not any(re.search(MOTS[m], t) for m in ('lat', 'gigue')):
        g = re.search(rf'({MAX})\s*{NOMBRE}\s*(ms|s)\b', reste)
        if g:
            it.seuils['lat'] = _valeur(g.group(2), g.group(3), 'lat'); it.compris.append(f'lat ≤ {it.seuils["lat"]:g} (« {g.group(0).strip()} »)')
            reste = reste.replace(g.group(0), ' ')

    if re.search(rf'{NEGATION}[^,;.]{{0,25}}priorit\w*', t):
        it.interdits.add('priorite'); it.compris.append('interdit : priorité')
    if re.search(rf'{NEGATION}[^,;.]{{0,25}}(?:brid\w*|limit\w*)', t):
        it.interdits.add('brider'); it.compris.append('interdit : brider les autres utilisateurs')
    if re.search(rf"{NEGATION}[^,;.]{{0,25}}(?:dérout\w*|derout\w*|changer de chemin|autre chemin)|rest\w* sur (?:le|son) chemin normal", t):
        it.interdits |= {'chemin1', 'chemin2', 'chemin3'}; it.compris.append('interdit : quitter le chemin normal (d1-c1)')
    for g in re.finditer(rf"(?:{NEGATION}[^,;.]{{0,20}}(?:par|via|sur)|évit\w*|evit\w*|contourn\w*)\s+(?:le |la |les )?((?:[cd][12](?:\s*(?:,|et|ni)\s*)?)+)", t):
        for sw in re.findall(r'[cd][12]', g.group(1)):
            it.interdits |= {f'chemin{k}' for k, p in enumerate(CHEMINS) if sw in p}
            it.compris.append(f'interdit : passer par {sw}')

    for g in re.finditer(rf'[^,;.\d]{{0,20}}{NOMBRE}\s*\S*', reste):
        it.non_compris.append(f'« {g.group(0).strip()} »')

    s = it.seuils
    if s['lat'] < LAT_MIN:
        it.refus.append(f'latence ≤ {s["lat"]:g} ms impossible (un client joint un serveur en ~{RTT_BASE:g} ms au mieux)')
    if s['debit'] > 1:
        it.refus.append(f'débit ≥ {s["debit"] * mbps:g} Mb/s impossible (ce service émet {mbps:g} Mb/s)')
    if s['gigue'] < 1:
        it.refus.append(f'gigue ≤ {s["gigue"]:g} ms irréaliste')
    if all(f'chemin{k}' in it.interdits for k in range(4)):
        it.refus.append('les 4 chemins sont interdits : le service ne peut plus passer')
    return it


def traduire3(texte):
    morceaux = {s: [] for s in SERVICES}
    for bout in re.split(r'[;.\n]| et (?=(?:la |le |les )?(?:' + '|'.join(NOMS.values()) + r'))', texte):
        if not bout or not bout.strip():
            continue
        cibles = [s for s, motif in NOMS.items() if re.search(rf'\b{motif}\b', bout.lower())] or list(SERVICES)
        for s in cibles:
            morceaux[s].append(bout.strip())
    return {s: _un_service(s, ', '.join(morceaux[s])) for s in SERVICES}


if __name__ == '__main__':
    phrases = sys.argv[1:] or ['visio excellente ; voix correcte, sans passer par c2',
                               'la visio doit rester sous 50 ms et sans coupure, jamais de priorité. Téléphonie acceptable',
                               'voix : latence < 5 ms',
                               'tout doit rester fluide, ne pas brider les autres ; visio : éviter d1 et c1',
                               'visio correcte en restant sur le chemin normal ; voix : débit d\'au moins 950 kb/s, gigue max 12 ms']
    for p in phrases:
        print('=' * 100 + f'\n« {p} »')
        for it in traduire3(p).values():
            print(it.resume())
