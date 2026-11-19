import re
import secrets
from dataclasses import dataclass, field

SERVICES = {
    'visio':    (10, r'visio\w*|vidéo\w*|video\w*|réunion\w*'),
    'voix':     (20, r'voix|téléphon\w*|telephon\w*|voip'),
    'donnees':  (30, r'données|donnees|bureautique|postes? de travail|utilisateurs?'),
    'invites':  (40, r'invités?|invites?|visiteurs?|wifi public'),
    'serveurs': (50, r'serveurs?'),
}
GESTION = 99
PORTS_ACCES = 22
MAX_ACCES = 20
INTERNET = '203.0.113.1'


@dataclass
class Equipement:
    nom: str
    role: str
    modele: str
    ports: dict = field(default_factory=dict)


@dataclass
class Plan:
    texte: str
    services: list = field(default_factory=list)
    n_acces: int = 2
    postes: int = 20
    redondant: bool = True
    sortie: bool = True
    ssh: bool = True
    isolements: list = field(default_factory=list)
    compris: list = field(default_factory=list)
    non_compris: list = field(default_factory=list)
    refus: list = field(default_factory=list)
    equipements: dict = field(default_factory=dict)
    cables: list = field(default_factory=list)
    mot_de_passe: str = ''

    @staticmethod
    def reseau(vlan):
        return f'10.0.{vlan}.0'

    @staticmethod
    def adresse(vlan, hote):
        return f'10.0.{vlan}.{hote}'

    def vlans(self):
        return [(s, SERVICES[s][0]) for s in self.services] + [('gestion', GESTION)]

    def coeurs(self):
        return [e for e in self.equipements.values() if e.role == 'coeur']

    def acces(self):
        return [e for e in self.equipements.values() if e.role == 'acces']

    def resume(self):
        L = [f'Intention : « {self.texte} »',
             f'  services  : {", ".join(f"{s} (VLAN {SERVICES[s][0]})" for s in self.services)} + gestion (VLAN {GESTION})',
             f'  accès     : {self.n_acces} commutateur(s), {self.postes} postes chacun',
             f'  cœur      : {"redondant (2 commutateurs de niveau 3, HSRP)" if self.redondant else "un seul commutateur de niveau 3"}',
             f'  sortie    : {"routeur 2911 vers « Internet » (" + INTERNET + ")" if self.sortie else "aucune"}',
             f'  filtrage  : {"; ".join(f"{a} ✗→ {b}" for a, b in self.isolements) or "aucun"}',
             f'  compris   : {"; ".join(self.compris) or "rien de précis → réseau par défaut"}']
        if self.non_compris:
            L.append(f'  PAS compris : {"; ".join(self.non_compris)}')
        L.append('  → ACCEPTÉE' if not self.refus else '  → REFUSÉE : ' + ' ; '.join(self.refus))
        return '\n'.join(L)


NOMBRES = {'un': 1, 'une': 1, 'deux': 2, 'trois': 3, 'quatre': 4, 'cinq': 5, 'six': 6}


def _nombre(x):
    return int(x) if x.isdigit() else NOMBRES.get(x, 0)


def traduire(texte):
    p = Plan(texte=texte.strip())
    t = ' ' + texte.lower().replace('’', "'") + ' '
    for s, (_, motif) in SERVICES.items():
        if re.search(rf'\b(?:{motif})\b', t):
            p.services.append(s)
    if not p.services:
        p.services = ['donnees']
        p.compris.append('aucun service nommé → un seul réseau « données »')
    else:
        p.compris.append('services : ' + ', '.join(p.services))
    g = re.search(r'(\d+|un|deux|trois|quatre|cinq|six)\s+(?:commutateurs?|switch\w*)\s+d.acc[eè]s|(\d+|un|deux|trois|quatre|cinq|six)\s+étages?', t)
    if g:
        p.n_acces = _nombre(g.group(1) or g.group(2))
        p.compris.append(f'{p.n_acces} commutateur(s) d\'accès')
    g = re.search(r'(\d+)\s+(?:postes|pc|ordinateurs|utilisateurs)(?!\s+par)', t)
    gp = re.search(r'(\d+)\s+(?:postes|pc|ordinateurs)\s+par\s+(?:commutateur|switch|étage)', t)
    if gp:
        p.postes = int(gp.group(1)); p.compris.append(f'{p.postes} postes par commutateur')
    elif g:
        total = int(g.group(1))
        if not re.search(r'(\d+|un|deux|trois|quatre|cinq|six)\s+(?:commutateurs?|switch\w*)\s+d.acc[eè]s|étages?', t):
            p.n_acces = max(1, -(-total // PORTS_ACCES))
        p.postes = -(-total // p.n_acces)
        p.compris.append(f'{total} postes au total → {p.n_acces} commutateur(s) de {p.postes}')
    if re.search(r'sans redondance|un seul c(?:œ|oe)ur|pas de redondance', t):
        p.redondant = False; p.compris.append('cœur non redondant')
    elif re.search(r'redondan\w*|haute disponibilit\w*|tol[eé]ran\w* aux pannes|sans coupure', t):
        p.redondant = True; p.compris.append('cœur redondant')
    if re.search(r"sans (?:accès|acces) (?:à |a )?internet|pas d.internet|sans sortie", t):
        p.sortie = False; p.compris.append('pas de sortie Internet')
    if re.search(r'\bsans ssh\b|telnet', t):
        p.ssh = False; p.compris.append('administration sans SSH')
    noms = '|'.join(m for _, m in SERVICES.values())
    for g in re.finditer(rf"\b({noms})\b[^,;.]{{0,30}}?(?:ne (?:doivent|doit|peuvent|peut) pas|n'ont pas le droit d')\s*(?:accéder|acceder|joindre|voir|atteindre)\s*(?:à |a |aux |au )?(?:la |les |le )?\b({noms})\b"
                         rf"|isol\w*\s+(?:les |la |le )?\b({noms})\b\s+(?:des|du|de la|de)\s+\b({noms})\b", t):
        a, b = (g.group(1), g.group(2)) if g.group(1) else (g.group(3), g.group(4))
        sa = next(s for s, (_, m) in SERVICES.items() if re.fullmatch(m, a))
        sb = next(s for s, (_, m) in SERVICES.items() if re.fullmatch(m, b))
        for s in (sa, sb):
            if s not in p.services:
                p.services.append(s)
        p.isolements.append((sa, sb)); p.compris.append(f'filtrage : {sa} ne joint pas {sb}')
    for g in re.finditer(r'\b(wifi|vpn|nat|qos|priorit\w*|bgp|eigrp|ipv6|802\.1x|radius)\b', t):
        p.non_compris.append(f'« {g.group(1)} » : pas encore géré par le générateur')
    if p.n_acces > MAX_ACCES:
        p.refus.append(f'{p.n_acces} commutateurs d\'accès : un 3560-24PS n\'en accepte que {MAX_ACCES}')
    if p.n_acces < 1:
        p.refus.append('il faut au moins un commutateur d\'accès')
    serveurs_seul = [s for s in p.services if s != 'serveurs']
    if p.postes > PORTS_ACCES:
        p.refus.append(f'{p.postes} postes sur un commutateur de 24 ports : précisez plus de commutateurs d\'accès')
    if not serveurs_seul:
        p.refus.append('aucun service pour des postes utilisateurs')
    p.mot_de_passe = 'Lab-' + secrets.token_hex(4)
    return p


def construire(p):
    E, C = {}, []
    coeurs = ['CORE1', 'CORE2'] if p.redondant else ['CORE1']
    for c in coeurs:
        E[c] = Equipement(c, 'coeur', '3560-24PS')
    for i in range(1, p.n_acces + 1):
        a = E[f'ACC{i}'] = Equipement(f'ACC{i}', 'acces', '2960-24TT')
        for k, c in enumerate(coeurs):
            port_acc, port_coeur = f'GigabitEthernet0/{k + 1}', f'FastEthernet0/{i}'
            a.ports[port_acc] = f'trunk vers {c}'
            E[c].ports[port_coeur] = f'trunk vers ACC{i}'
            C.append((f'ACC{i}', port_acc, c, port_coeur, 'croisé (cuivre)'))
    if p.redondant:
        E['CORE1'].ports['GigabitEthernet0/1'] = 'trunk vers CORE2'
        E['CORE2'].ports['GigabitEthernet0/1'] = 'trunk vers CORE1'
        C.append(('CORE1', 'GigabitEthernet0/1', 'CORE2', 'GigabitEthernet0/1', 'croisé (cuivre)'))
    if p.sortie:
        E['EDGE'] = Equipement('EDGE', 'sortie', '2911')
        for k, c in enumerate(coeurs):
            E[c].ports['GigabitEthernet0/2'] = 'lien routé vers EDGE'
            E['EDGE'].ports[f'GigabitEthernet0/{k}'] = f'lien routé vers {c}'
            C.append((c, 'GigabitEthernet0/2', 'EDGE', f'GigabitEthernet0/{k}', 'droit (cuivre)'))
    if 'serveurs' in p.services:
        E['CORE1'].ports['FastEthernet0/24'] = 'accès VLAN serveurs'
        E['SRV1'] = Equipement('SRV1', 'serveur', 'Server-PT')
        C.append(('SRV1', 'FastEthernet0', 'CORE1', 'FastEthernet0/24', 'droit (cuivre)'))
    usagers = [s for s in p.services if s != 'serveurs']
    for i in sorted({1, p.n_acces}):
        for k, s in enumerate(usagers):
            port = f'FastEthernet0/{bloc(p, s)[0]}'
            pc = f'PC-{s.upper()}-{i}'
            E[pc] = Equipement(pc, 'pc', 'PC-PT')
            C.append((pc, 'FastEthernet0', f'ACC{i}', port, 'droit (cuivre)'))
    p.equipements, p.cables = E, C
    return p


def bloc(p, service):
    usagers = [s for s in p.services if s != 'serveurs']
    par = max(1, min(PORTS_ACCES // len(usagers), -(-p.postes // len(usagers)) + 2))
    k = usagers.index(service)
    return 1 + k * par, min(PORTS_ACCES, (k + 1) * par)


if __name__ == '__main__':
    import sys
    for phrase in sys.argv[1:] or ["réseau d'entreprise avec visio, voix, données, invités et serveurs ; 2 commutateurs d'accès ; "
                                    "cœur redondant ; les invités ne doivent pas accéder aux serveurs"]:
        p = construire(traduire(phrase))
        print(p.resume())
        for c in p.cables:
            print('   câble', c)
