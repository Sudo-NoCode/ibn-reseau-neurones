import re
import sys
from dataclasses import dataclass, field

DEFAUT = dict(lat=60.0, pertes=2.0, gigue=15.0, debit=3.6)
PRESETS = {
    'excellente': dict(lat=40.0, pertes=0.5, gigue=8.0, debit=3.9),
    'parfaite':   dict(lat=40.0, pertes=0.5, gigue=8.0, debit=3.9),
    'bonne':      dict(DEFAUT),
    'correcte':   dict(DEFAUT),
    'acceptable': dict(lat=100.0, pertes=4.0, gigue=25.0, debit=3.0),
}
PHYSIQUE = dict(lat=10.0, gigue=1.0, debit=4.0)
LAT_SECOURS = 32.0
LEVIERS = {'deroute': ['secours', 'dérout', 'derout', 'autre chemin'],
           'limite': ['limiter h2', 'brider h2', 'limiter les autres', 'brider les autres'],
           'priorite': ['priorit']}
NEGATION = r"(?:sans|interdit|pas (?:le droit )?d[e']|ne pas|jamais|n'utilise pas|ne touche pas)"

NOMBRE = r'(\d+(?:[.,]\d+)?)'
MAX = r'(?:sous|<=?|≤|moins de|inférieure? à|inferieure? a|au plus|maximum|max|pas plus de|ne dépasse pas)'
MIN = r'(?:>=?|≥|au moins|plus de|supérieure? à|superieure? a|minimum|min)'
MOTS = {'lat': r'(?:latence|délai|delai|ping|rtt)', 'pertes': r'(?:pertes?|perdus?)',
        'gigue': r'(?:gigue|jitter)', 'debit': r'(?:débit|debit|bande passante)'}
UNITES = {'lat': r'\s*(ms|s)\b', 'pertes': r'\s*(%|pour ?cent)', 'gigue': r'\s*(ms|s)\b',
          'debit': r'\s*(mb/?s|mbps|kb/?s|kbps)'}


@dataclass
class Intention:
    texte: str
    flux: str = 'visio'
    seuils: dict = field(default_factory=lambda: dict(DEFAUT))
    interdits: set = field(default_factory=set)
    compris: list = field(default_factory=list)
    non_compris: list = field(default_factory=list)
    refus: list = field(default_factory=list)

    @property
    def acceptee(self):
        return not self.refus

    def resume(self):
        s = self.seuils
        lignes = [f'Intention : « {self.texte} »',
                  f'  flux      : {self.flux}',
                  f'  contrat   : latence ≤ {s["lat"]:g} ms, pertes ≤ {s["pertes"]:g} %, gigue ≤ {s["gigue"]:g} ms, '
                  f'débit ≥ {s["debit"]:g} Mb/s',
                  f'  interdits : {", ".join(sorted(self.interdits)) or "aucun"}',
                  f'  compris   : {"; ".join(self.compris) or "rien de précis → contrat par défaut"}']
        if self.non_compris:
            lignes.append(f'  PAS compris : {"; ".join(self.non_compris)}')
        lignes.append('  → ACCEPTÉE' if self.acceptee else '  → REFUSÉE : ' + ' ; '.join(self.refus))
        return '\n'.join(lignes)


def _valeur(nombre, unite, metrique):
    v = float(nombre.replace(',', '.'))
    u = (unite or '').lower()
    if metrique in ('lat', 'gigue') and u == 's':
        v *= 1000
    if metrique == 'debit' and u.startswith('kb'):
        v /= 1000
    return v


def traduire(texte):
    it = Intention(texte=texte.strip())
    t = ' ' + texte.lower().replace('’', "'") + ' '
    reste = re.sub(r'\bh\d+\b', ' ', t)

    if re.search(r'\bh(?:[24-9]|\d\d)\b', t) and not re.search(r'brider h2|limiter h2', t):
        it.non_compris.append('seul le flux visio h1 → h3 est piloté dans ce labo')

    for mot, preset in PRESETS.items():
        if re.search(rf'\b{mot}s?\b', t):
            it.seuils.update(preset)
            it.compris.append(f'qualité « {mot} »')
            reste = re.sub(rf'\b{mot}s?\b', ' ', reste)

    if re.search(r'sans coupure|fluide|sans saccade', t):
        it.seuils['pertes'] = min(it.seuils['pertes'], 1.0)
        it.seuils['gigue'] = min(it.seuils['gigue'], 10.0)
        it.compris.append('« fluide / sans coupure » → pertes ≤ 1 %, gigue ≤ 10 ms')
        reste = re.sub(r'sans coupure|fluide|sans saccade', ' ', reste)

    for m, mot in MOTS.items():
        motif = rf"{mot}[^,;.\d]{{0,25}}?({MAX}|{MIN})?\s*(?:de |d')?{NOMBRE}(?:{UNITES[m]})?"
        for g in re.finditer(motif, t):
            sens, nombre, unite = g.group(1), g.group(2), g.group(3)
            v = _valeur(nombre, unite, m)
            if sens and re.fullmatch(MIN, sens) and m != 'debit':
                it.non_compris.append(f"« {g.group(0).strip()} » : un minimum de {m} n'a pas de sens")
            else:
                it.seuils[m] = v
                it.compris.append(f'{m} {"≥" if m == "debit" else "≤"} {v:g}')
            reste = reste.replace(g.group(0), ' ')

    if not any(re.search(MOTS[m], t) for m in ('lat', 'gigue')):
        g = re.search(rf'({MAX})\s*{NOMBRE}\s*(ms|s)\b', reste)
        if g:
            v = _valeur(g.group(2), g.group(3), 'lat')
            it.seuils['lat'] = v
            it.compris.append(f'lat ≤ {v:g} (« {g.group(0).strip()} »)')
            reste = reste.replace(g.group(0), ' ')

    for lev, mots in LEVIERS.items():
        for mot in mots:
            g = re.search(rf'{NEGATION}[^,;.]{{0,25}}{mot}\w*', t)
            if g:
                it.interdits.add(lev)
                it.compris.append(f'interdit : {lev}')
                reste = reste.replace(g.group(0), ' ')
                break

    for g in re.finditer(rf'[^,;.\d]{{0,20}}{NOMBRE}\s*\S*', reste):
        it.non_compris.append(f'« {g.group(0).strip()} »')

    s = it.seuils
    if s['lat'] < PHYSIQUE['lat']:
        it.refus.append(f'latence ≤ {s["lat"]:g} ms impossible (minimum physique ~{PHYSIQUE["lat"]:g} ms)')
    if s['debit'] > PHYSIQUE['debit']:
        it.refus.append(f'débit ≥ {s["debit"]:g} Mb/s impossible (la visio émet {PHYSIQUE["debit"]:g} Mb/s)')
    if s['gigue'] < PHYSIQUE['gigue']:
        it.refus.append(f'gigue ≤ {s["gigue"]:g} ms irréaliste')
    if PHYSIQUE['lat'] <= s['lat'] < LAT_SECOURS and 'deroute' not in it.interdits:
        it.interdits.add('deroute')
        it.compris.append(f'déduit : secours inutilisable (latence ~{LAT_SECOURS:g} ms > {s["lat"]:g} ms)')
    if {'deroute', 'priorite', 'limite'} <= it.interdits:
        it.refus.append('tous les leviers sont interdits : le système ne pourra rien réparer')
    return it


if __name__ == '__main__':
    phrases = sys.argv[1:] or [
        'la visio doit rester sous 50 ms',
        'visio excellente sans utiliser le lien de secours',
        "latence < 80 ms, pertes au plus 1 %, débit d'au moins 3.8 Mb/s",
        'réunion fluide, gigue max 12 ms, ne pas brider h2',
        'la visio de h1 vers h3 doit avoir une latence sous 20 ms',
        'visio correcte et 7 utilisateurs',
    ]
    for p in phrases:
        print(traduire(p).resume(), '\n')
