import re
import sys
from pathlib import Path

from plan import GESTION, INTERNET, SERVICES, bloc, construire, traduire

ICI = Path(__file__).resolve().parent
MASQUE = '255.255.255.0'
JOKER = '0.0.0.255'
DOMAINE = 'ibn.lab'
SERVEUR_IP = 50, 10
TRANSIT = {'CORE1': ('10.255.0.1', '10.255.0.2'), 'CORE2': ('10.255.0.5', '10.255.0.6')}


def _liste(vlans):
    return ','.join(str(v) for _, v in vlans)


def _commun(p, nom):
    return ['enable', 'configure terminal', f'hostname {nom}', 'no ip domain-lookup', f'enable secret {p.mot_de_passe}',
            'service password-encryption', 'banner motd #Configure automatiquement par la boucle IBN - acces reserve#',
            'line console 0', ' logging synchronous', ' exit']


def _ssh(p):
    if not p.ssh:
        return []
    return [f'ip domain-name {DOMAINE}', f'username admin privilege 15 secret {p.mot_de_passe}',
            'crypto key generate rsa general-keys modulus 1024', 'ip ssh version 2',
            'line vty 0 15', ' login local', ' transport input ssh', ' exit']


def _fin():
    return ['end', 'write memory']


def _vlans(p):
    out = []
    for s, v in p.vlans():
        out += [f'vlan {v}', f' name {s.upper()}', ' exit']
    return out


def _trunk(ports, vlans, encapsulation):
    out = []
    for port in ports:
        out += [f'interface {port}']
        if encapsulation:
            out.append(' switchport trunk encapsulation dot1q')
        out += [' switchport mode trunk', f' switchport trunk allowed vlan {vlans}', ' no shutdown', ' exit']
    return out


def coeur(p, nom):
    rang = [c.nom for c in p.coeurs()].index(nom)
    e = p.equipements[nom]
    vl, vlans = p.vlans(), _liste(p.vlans())
    L = _commun(p, nom) + ['ip routing', 'spanning-tree mode rapid-pvst',
                           f'spanning-tree vlan {vlans} root {"primary" if rang == 0 else "secondary"}'] + _vlans(p)
    L += _trunk([port for port, d in e.ports.items() if d.startswith('trunk')], vlans, encapsulation=True)
    for port, d in e.ports.items():
        if d == 'lien routé vers EDGE':
            moi, _ = TRANSIT[nom]
            L += [f'interface {port}', ' no switchport', f' ip address {moi} 255.255.255.252', ' no shutdown', ' exit']
        elif d == 'accès VLAN serveurs':
            L += [f'interface {port}', ' switchport mode access', f' switchport access vlan {SERVICES["serveurs"][0]}',
                  ' spanning-tree portfast', ' no shutdown', ' exit']
    for s, v in vl:
        L += [f'interface Vlan{v}']
        if p.redondant:
            L += [f' ip address {p.adresse(v, 2 + rang)} {MASQUE}', f' standby {v} ip {p.adresse(v, 1)}',
                  f' standby {v} priority {110 if rang == 0 else 100}', f' standby {v} preempt']
        else:
            L += [f' ip address {p.adresse(v, 1)} {MASQUE}']
        if any(a == s for a, _ in p.isolements):
            L.append(f' ip access-group FILTRE-{s.upper()} in')
        L += [' no shutdown', ' exit']
    for a in sorted({a for a, _ in p.isolements}):
        L += [f'ip access-list extended FILTRE-{a.upper()}']
        for _, b in [x for x in p.isolements if x[0] == a]:
            L.append(f' deny ip {p.reseau(SERVICES[a][0])} {JOKER} {p.reseau(SERVICES[b][0])} {JOKER}')
        L += [' permit ip any any', ' exit']
    if rang == 0:
        dns = p.adresse(*SERVEUR_IP) if 'serveurs' in p.services else None
        for s, v in vl:
            if s in ('serveurs', 'gestion'):
                continue
            L += [f'ip dhcp excluded-address {p.adresse(v, 1)} {p.adresse(v, 20)}', f'ip dhcp pool {s.upper()}',
                  f' network {p.reseau(v)} {MASQUE}', f' default-router {p.adresse(v, 1)}'] + ([f' dns-server {dns}'] if dns else []) + [' exit']
    L += ['router ospf 1', f' router-id {rang + 1}.{rang + 1}.{rang + 1}.{rang + 1}']
    L += [f' passive-interface Vlan{v}' for s, v in vl if s != 'gestion']
    L += [' network 10.0.0.0 0.0.255.255 area 0'] + ([' network 10.255.0.0 0.0.0.255 area 0'] if p.sortie else []) + [' exit']
    return L + _ssh(p) + _fin()


def acces(p, nom):
    i = int(nom[3:])
    e = p.equipements[nom]
    vlans = _liste(p.vlans())
    L = _commun(p, nom) + ['spanning-tree mode rapid-pvst'] + _vlans(p)
    usagers = [s for s in p.services if s != 'serveurs']
    dernier = 0
    for s in usagers:
        a, b = bloc(p, s)
        dernier = max(dernier, b)
        L += [f'interface range FastEthernet0/{a} - {b}' if b > a else f'interface FastEthernet0/{a}', ' switchport mode access',
              f' switchport access vlan {SERVICES[s][0]}', ' spanning-tree portfast', ' no shutdown', ' exit']
    if dernier < 24:
        L += [f'interface range FastEthernet0/{dernier + 1} - 24' if dernier + 1 < 24 else 'interface FastEthernet0/24',
              ' shutdown', ' exit']
    L += _trunk([port for port, d in e.ports.items() if d.startswith('trunk')], vlans, encapsulation=False)
    L += [f'interface Vlan{GESTION}', f' ip address {p.adresse(GESTION, 10 + i)} {MASQUE}', ' no shutdown', ' exit',
          f'ip default-gateway {p.adresse(GESTION, 1)}']
    return L + _ssh(p) + _fin()


def sortie(p, nom):
    e = p.equipements[nom]
    L = _commun(p, nom)
    for port, d in e.ports.items():
        coeur_nom = d.split()[-1]
        _, moi = TRANSIT[coeur_nom]
        L += [f'interface {port}', f' description vers {coeur_nom}', f' ip address {moi} 255.255.255.252', ' no shutdown', ' exit']
    L += ['interface Loopback0', ' description joue le role d Internet', f' ip address {INTERNET} {MASQUE}', ' exit',
          'router ospf 1', ' router-id 9.9.9.9', ' network 10.255.0.0 0.0.0.255 area 0', f' network {INTERNET.rsplit(".", 1)[0]}.0 {JOKER} area 0', ' exit']
    return L + _ssh(p) + _fin()


def guide(p, dossier):
    L = [f'# Guide Packet Tracer — {dossier}', '', f'Intention : « {p.texte} »', '', '```', p.resume(), '```', '',
         '## 1. Poser les équipements', '', '| Nom | Modèle Packet Tracer | Rôle |', '|---|---|---|']
    for e in p.equipements.values():
        L.append(f'| {e.nom} | {e.modele} | {e.role} |')
    L += ['', 'Renommez chaque équipement (onglet Config > Display Name) pour vous y retrouver.', '',
          '## 2. Câbler', '', '| De | Port | Vers | Port | Câble |', '|---|---|---|---|---|']
    for a, pa, b, pb, cable in p.cables:
        L.append(f'| {a} | {pa} | {b} | {pb} | {cable} |')
    L += ['', '## 3. Coller les configurations', '',
          'Pour chaque commutateur et routeur : cliquez dessus > onglet **CLI** > appuyez sur Entrée si une question s\'affiche '
          '(répondez **no** à « initial configuration dialog ») > collez tout le contenu du fichier `<NOM>.txt`.', '',
          'Ordre conseillé : les commutateurs d\'accès, puis les cœurs, puis EDGE.',
          'Attendez environ 30 secondes après le dernier collage (le temps que le spanning-tree, HSRP et OSPF se mettent en place), '
          'ou cliquez plusieurs fois sur **Fast Forward Time** en bas.', '',
          '## 4. Régler les PC et le serveur', '',
          '- Chaque **PC** : Desktop > IP Configuration > **DHCP**. Il doit recevoir une adresse dans son VLAN.']
    if 'serveurs' in p.services:
        L.append(f'- **SRV1** : Desktop > IP Configuration > Static : IP `{p.adresse(*SERVEUR_IP)}`, masque `{MASQUE}`, '
                 f'passerelle `{p.adresse(SERVICES["serveurs"][0], 1)}`.')
    L += ['', '## 5. Tester (et noter ce qui ne marche pas)', '', '| # | Test | Où | Résultat attendu |', '|---|---|---|---|']
    usagers = [s for s in p.services if s != 'serveurs']
    pc = lambda s, i=1: f'PC-{s.upper()}-{i}'
    k = 1
    s0 = usagers[0]
    L.append(f'| {k} | Adresse reçue par DHCP | {pc(s0)} > Desktop > IP Configuration | `{p.reseau(SERVICES[s0][0])[:-1]}x`, passerelle `{p.adresse(SERVICES[s0][0], 1)}` |'); k += 1
    if len(usagers) > 1:
        L.append(f'| {k} | `ping <adresse de {pc(usagers[1], p.n_acces)}>` (lue dans son IP Configuration ; autre VLAN, autre commutateur) | {pc(s0)} > Command Prompt | réponses (le cœur route entre VLAN) |'); k += 1
    if p.sortie:
        L.append(f'| {k} | `ping {INTERNET}` | {pc(s0)} > Command Prompt | réponses (sortie par EDGE, appris par OSPF) |'); k += 1
    for a, b in p.isolements:
        cible = p.adresse(*SERVEUR_IP) if b == 'serveurs' else f'l\'adresse de {pc(b)}'
        L.append(f'| {k} | `ping {cible}` | {pc(a)} > Command Prompt | **échec** (« Destination host unreachable » ou délai dépassé) : la liste d\'accès bloque |'); k += 1
        autre = next((s for s in usagers if s != a), None)
        if autre:
            L.append(f'| {k} | `ping {cible}` | {pc(autre)} > Command Prompt | réponses (seul {a} est bloqué) |'); k += 1
    if p.redondant:
        L.append(f'| {k} | `show standby brief` | CORE1 et CORE2 | CORE1 **Active**, CORE2 **Standby** sur chaque VLAN |'); k += 1
    if p.sortie:
        n = len(p.coeurs())
        L.append(f'| {k} | `show ip ospf neighbor` | EDGE | {n} voisin(s) en état **FULL** |'); k += 1
    if p.ssh:
        admin = 'donnees' if 'donnees' in usagers else usagers[0]
        L.append(f'| {k} | `ssh -l admin {p.adresse(GESTION, 11)}` | {pc(admin)} > Command Prompt | demande le mot de passe `{p.mot_de_passe}`, puis l\'invite ACC1# |'); k += 1
    if p.redondant and p.sortie:
        L.append(f'| {k} | Panne du cœur : `ping -t {INTERNET}` sur {pc(s0)}, puis éteindre CORE1 (onglet Physical, bouton) | {pc(s0)} | quelques pertes, puis les réponses reviennent : CORE2 devient Active |'); k += 1
    L += ['', '## Notes', '',
          f'- Mot de passe de **labo** (enable et SSH) : `{p.mot_de_passe}`. Sur un vrai réseau, il faudrait le changer.',
          '- Le DHCP est sur CORE1 seulement : si CORE1 tombe, les PC déjà configurés continuent de marcher, mais un nouveau PC n\'aura pas d\'adresse.',
          '- Le serveur n\'est branché que sur CORE1 : si CORE1 tombe, il devient injoignable (vu dans Packet Tracer le 16/11). Pour un vrai réseau, il faudrait le brancher aux deux cœurs.',
          '- Packet Tracer : les PC se branchent sur **FastEthernet0** (le dernier du menu, après RS 232 et USB). Les 3560 et le 2911 demandent « initial configuration dialog » au premier démarrage : répondre **no** avant de coller.',
          '- Ces commandes n\'ont pas été exécutées par le générateur lui-même : si Packet Tracer refuse une ligne (« % Invalid input »), '
          'note le nom de l\'équipement et la ligne, c\'est exactement ce qu\'il me faut pour corriger.']
    return '\n'.join(L) + '\n'


def generer(texte, dossier=None):
    p = construire(traduire(texte))
    print(p.resume())
    if p.refus:
        return None
    dossier = dossier or re.sub(r'[^a-z0-9]+', '-', p.texte.lower())[:40].strip('-')
    out = ICI / 'sortie' / dossier
    out.mkdir(parents=True, exist_ok=True)
    for f in out.glob('*.txt'):
        f.unlink()
    for e in p.equipements.values():
        cmd = {'coeur': coeur, 'acces': acces, 'sortie': sortie}.get(e.role)
        if cmd:
            (out / f'{e.nom}.txt').write_text('\n'.join(cmd(p, e.nom)) + '\n', encoding='utf-8')
    (out / 'GUIDE.md').write_text(guide(p, dossier), encoding='utf-8')
    print(f'\nécrit dans {out} : ' + ', '.join(sorted(f.name for f in out.iterdir())))
    return out


if __name__ == '__main__':
    generer(sys.argv[1] if len(sys.argv) > 1 else "réseau d'entreprise avec visio, voix, données, invités et serveurs ; "
            "2 commutateurs d'accès ; cœur redondant ; les invités ne doivent pas accéder aux serveurs",
            sys.argv[2] if len(sys.argv) > 2 else None)
