import glob
import os
import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'phase2'))
import dqn
from dqn import Reseau

from detecteur3 import ENTREES, POIDS, Detecteur3
from env3 import ACTIONS, AGENTS, AUTRE, DIM_OBS, GENEUR_DE, Env3, masque_obs, obs_pour
from intention3 import CHEMINS, traduire3
from jumeau3 import SERVICES, chemins_de

dqn.CACHE = 128
ICI = Path(__file__).resolve().parent
SEUIL_CONFIRMATION = 15.0
DUREE_DEMO = 420
DEMO = [(0, 'normal', []),
        (30, 'congestion_udp', [('h2', 'udp', 25)]), (90, 'normal', []),
        (130, 'degrade', [(('d1', 'c1'), 'degrade', 1.0)]), (190, 'normal', []),
        (230, 'congestion_tcp', [('h4', 'tcp', 4)]), (290, 'normal', []),
        (330, 'double_panne', [(('a1', 'd1'), 'instable', 1.0), (('c1', 'a4'), 'degrade', 0.8)]), (390, 'normal', [])]


def _rampe(t0, duree, sit, cible, pas):
    out = []
    for i in range(1, pas + 1):
        f = i / pas
        out.append((t0 + round((i - 1) * duree / pas), f'{sit}|progressive',
                    [(c, typ, (6 + (v - 6) * f) if typ == 'udp' else v * f) for c, typ, v in cible]))
    return out


DEMO_PROGRESSIF = ([(0, 'normal', [])]
                   + _rampe(30, 24, 'congestion_udp', [('h2', 'udp', 28)], 4) + [(80, 'normal', [])]
                   + _rampe(120, 30, 'degrade', [(('d1', 'c1'), 'degrade', 1.0)], 15) + [(190, 'normal', [])]
                   + _rampe(230, 24, 'congestion_udp', [('h4', 'udp', 26)], 4) + [(290, 'normal', [])]
                   + _rampe(330, 20, 'instable', [(('a1', 'd1'), 'instable', 1.2)], 10) + [(390, 'normal', [])])
CAUSE = {'congestion_udp': 'congestion (UDP)', 'congestion_tcp': 'congestion (TCP)', 'degrade': 'lien dégradé', 'instable': 'lien instable',
         'double_panne': 'deux liens en panne', 'double_congestion': 'deux gêneurs', 'normal': 'aucune'}


class AgentEnsemble:

    def __init__(self, dossier=None):
        dossier = Path(dossier or ICI / 'v1')
        self.nets = []
        for f in sorted(glob.glob(str(dossier / 'agent3_graine*.npz'))):
            n = Reseau(DIM_OBS, len(ACTIONS), np.random.default_rng(0))
            n.p = dict(np.load(f))
            self.nets.append(n)
        assert self.nets, f'aucun poids dans {dossier}'

    def __call__(self, obs, env=None, s=None):
        q = np.mean([n.avant(obs_pour(n.p['W1'].shape[0], obs)[None])[0] for n in self.nets], 0)
        return int(np.argmax(np.where(masque_obs(obs), q, -1e9)))


def estimer_cout(contrats, agent, episodes=3):
    hors = {s: [] for s in AGENTS}
    for g in range(episodes):
        env = Env3(randomiser=False, graine=2000 + g, intentions=contrats)
        obs, fin, h = env.reset(), False, {s: 0 for s in AGENTS}
        while not fin:
            obs, _, fin, info = env.step({s: agent(obs[s], env, s) for s in AGENTS})
            for s in AGENTS:
                h[s] += not info['ok'][s]
        for s in AGENTS:
            hors[s].append(100 * h[s] / env.duree)
    return {s: float(np.mean(v)) for s, v in hors.items()}


def accepter(texte, agent, confirmer=None):
    its = traduire3(texte)
    for it in its.values():
        print(it.resume())
    refus = [f'{s} : ' + ' ; '.join(it.refus) for s, it in its.items() if not it.acceptee]
    if refus:
        return None, 'refusée — ' + ' | '.join(refus)
    contrats = {s: it.contrat() for s, it in its.items()}
    cout = estimer_cout(contrats, agent)
    print('  coût prévu (jumeau, 3 × 10 min de pannes jamais vues) : ' + ', '.join(f'{s} {c:.1f} % du temps hors contrat' for s, c in cout.items()))
    if max(cout.values()) > SEUIL_CONFIRMATION:
        print('  ⚠ ces intentions coûtent cher : trop d\'interdits ou des seuils trop serrés pour ce réseau.')
        if confirmer is not None and not confirmer('  Confirmer quand même ? [o/n] '):
            return None, "annulée par l'utilisateur"
    return contrats, cout


def nom_action(s, a):
    nom = ACTIONS[a]
    if nom.startswith('chemin'):
        d, c = CHEMINS[int(nom[-1])]
        return f'passe par {d}-{c}' + (' (chemin normal)' if nom == 'chemin0' else '')
    return {'priorite': 'prend la priorité', 'sans_priorite': 'rend la priorité', 'brider': f'bride {GENEUR_DE[s]}',
            'debrider': f'libère {GENEUR_DE[s]}'}[nom]


def raisons(m, seuils, mbps):
    r = []
    if m['lat'] > seuils['lat']:
        r.append(f'latence {m["lat"]:.0f} ms' if m['lat'] < 1000 else 'aucune réponse')
    if m['pertes'] > seuils['pertes']:
        r.append(f'pertes {m["pertes"]:.1f} %')
    if m['gigue'] > seuils['gigue']:
        r.append(f'gigue {m["gigue"]:.0f} ms')
    if m['debit'] < seuils['debit'] * mbps:
        r.append(f'débit {m["debit"]:.2f} Mb/s')
    return ', '.join(r)


class Journal:
    CALME = 5

    def __init__(self, contrats):
        self.contrats, self.lignes = contrats, []
        self.incidents = {s: [] for s in AGENTS}
        self.cours = {s: None for s in AGENTS}
        self.hors = {s: 0 for s in AGENTS}
        self.usage = {s: dict(detour=0, priorite=0, bride=0, interdit=0, changements=0) for s in AGENTS}
        self.n = 0
        self.prevoyait = {s: False for s in AGENTS}
        self.derniere_alerte = {s: None for s in AGENTS}
        self.alertes = {s: 0 for s in AGENTS}

    def _dire(self, t, texte):
        self.lignes.append(f'[{t:>4} s] {texte}')

    def seconde(self, t, actions, info, det=None):
        self.n += 1
        det = det or {}
        for s in AGENTS:
            o = det.get(s)
            if o and o['prevision'] and not self.prevoyait[s] and info['ok'][s] and self.cours[s] is None:
                self.alertes[s] += 1; self.derniere_alerte[s] = t
                self._dire(t, f'{s.upper():<5} ⚠ alerte du détecteur : contrat menacé dans les 10 s (il reconnaît : {o["classe"]})')
            self.prevoyait[s] = bool(o and o['prevision'])
        for s in AGENTS:
            ok, i, u = info['ok'][s], self.contrats[s]['interdits'], self.usage[s]
            u['detour'] += info['chemin'][s] != 0; u['priorite'] += info['prio'][s]; u['bride'] += info['limite'][GENEUR_DE[s]]
            u['interdit'] += (f'chemin{info["chemin"][s]}' in i) or (info['prio'][s] and 'priorite' in i) or (info['limite'][GENEUR_DE[s]] and 'brider' in i)
            self.hors[s] += not ok
            if not ok and self.cours[s] is None:
                a = self.derniere_alerte[s]
                self.cours[s] = dict(debut=t, hors=0, bons=0, actions=[], causes=set(), retabli=None, diags=Counter(),
                                     annonce=t - a if a is not None and t - a <= 15 else None)
                self._dire(t, f'{s.upper():<5} ✗ contrat perdu : {raisons(info["mesure"][s], self.contrats[s]["seuils"], SERVICES[s]["mbps"])}')
        for s in AGENTS:
            if info['changes'][s]:
                self.usage[s]['changements'] += 1
                act = nom_action(s, actions[s])
                ouverts = [x for x in self.cours.values() if x]
                self._dire(t, f'{s.upper():<5} → {act}' + ('' if ouverts else '  (aucun incident en cours)'))
                for x in ouverts:
                    x['actions'].append(f'{s} {act} (à {t} s)')
        for s in AGENTS:
            c = self.cours[s]
            if not c:
                continue
            if info['situation'] != 'normal':
                c['causes'].add(info['situation'].split('|')[0])
            if det.get(s) and det[s]['classe'] != 'normal':
                c['diags'][det[s]['classe']] += 1
            if info['ok'][s]:
                c['bons'] += 1
                if c['bons'] == 1 and c['retabli'] is None:
                    c['retabli'] = t
                    self._dire(t, f'{s.upper():<5} ✓ contrat rétabli, {t - c["debut"]} s après la perte')
                if c['bons'] >= self.CALME:
                    c['fin'] = t - self.CALME
                    self.incidents[s].append(c); self.cours[s] = None
            else:
                c['hors'] += 1
                if c['bons']:
                    c['bons'], c['retabli'] = 0, None
        return self

    def clore(self, t):
        for s in AGENTS:
            if self.cours[s]:
                self.cours[s]['fin'] = t
                self.incidents[s].append(self.cours[s]); self.cours[s] = None

    def rapport(self, texte, cout_prevu=None):
        L = ['=' * 96, 'RAPPORT IBN — réseau à 3 couches', '=' * 96, f'Intentions : « {texte} »', f'Durée      : {self.n} s']
        for s in AGENTS:
            se, u = self.contrats[s]['seuils'], self.usage[s]
            prevu = f'   (prévu par le jumeau : {100 - cout_prevu[s]:.1f} %)' if cout_prevu else ''
            L += ['', f'{s.upper()} — contrat : latence ≤ {se["lat"]:g} ms, pertes ≤ {se["pertes"]:g} %, gigue ≤ {se["gigue"]:g} ms, débit ≥ {100 * se["debit"]:g} %',
                  f'  Contrat tenu : {100 - 100 * self.hors[s] / self.n:.1f} % du temps{prevu}',
                  f'  Leviers : hors du chemin normal {100 * u["detour"] / self.n:.0f} % du temps, priorité {100 * u["priorite"] / self.n:.0f} %, '
                  f'{GENEUR_DE[s]} bridé {100 * u["bride"] / self.n:.0f} %, {u["changements"]} changements ; secondes sur un levier interdit : {u["interdit"]}',
                  f'  Incidents : {len(self.incidents[s])}, dont {sum(c["annonce"] is not None for c in self.incidents[s])} annoncés par le détecteur ; '
                  f'alertes du détecteur : {self.alertes[s]}']
            for k, c in enumerate(self.incidents[s], 1):
                causes = ', '.join(CAUSE[x] for x in sorted(c['causes'])) or 'aucune panne en cours'
                L.append(f'    #{k}  {c["debut"]:>4}–{c["fin"]:>4} s   {c["hors"]} s hors contrat   (vraie cause, connue du labo seulement : {causes})')
                diag = c['diags'].most_common(1)[0][0] if c['diags'] else 'aucun'
                annonce = f'annoncé {c["annonce"]} s avant' if c['annonce'] is not None else 'non annoncé'
                L.append(f'        détecteur : {annonce} ; diagnostic : {diag}')
                L.append(f'        actions : {"; ".join(c["actions"]) or "aucune"}')
        return '\n'.join(L)


def boucle(env, agent, journal, programme=None, chaque_seconde=None):
    obs = env.reset()
    if programme:
        env.prog = programme
    fin = False
    detecteurs = {s: Detecteur3() for s in AGENTS} if POIDS.exists() else {}
    while not fin:
        actions = {s: agent(obs[s], env, s) for s in AGENTS}
        obs, rec, fin, info = env.step(actions)
        det = {}
        for s, d in detecteurs.items():
            m, liens = info['mesure'][s], chemins_de(s)[info['chemin'][s]]
            x = dict(lat=m['lat'], pertes=m['pertes'], gigue=m['gigue'], debit=m['debit'] / SERVICES[s]['mbps'],
                     **{f'sonde{k}': v for k, v in enumerate(m['sondes'])},
                     **{f'occ{k}': env.liens[l][0] for k, l in enumerate(liens)}, **{f'file{k}': env.liens[l][1] for k, l in enumerate(liens)})
            det[s] = d.ajouter(x, viole=not info['ok'][s])
        journal.seconde(env.t, actions, info, det)
        if chaque_seconde:
            chaque_seconde(env, actions, rec, info)
    journal.clore(env.t)
    return journal


def main():
    texte = next((x for x in sys.argv[1:] if not x.startswith('--')), 'visio excellente ; voix correcte, sans passer par c2')
    agent = AgentEnsemble()
    contrats, cout = accepter(texte, agent, confirmer=None if '--oui' in sys.argv else (lambda q: input(q).strip().lower().startswith('o')))
    if contrats is None:
        print('\nIntention ' + cout)
        return
    print(f'\n--- boucle IBN dans le jumeau : {DUREE_DEMO} s, 4 pannes ---')
    prog = DEMO_PROGRESSIF if '--progressif' in sys.argv else DEMO
    journal = boucle(Env3(duree=DUREE_DEMO, randomiser=False, graine=7, intentions=contrats), agent, Journal(contrats), programme=prog)
    print('\n'.join(journal.lignes))
    print('\n' + journal.rapport(texte, cout))


if __name__ == '__main__':
    main()
