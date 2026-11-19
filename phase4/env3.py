import random

import numpy as np

from jumeau3 import GENEURS, LAT_ABSENTE, LIENS, SERVICES, Physique, chemins_de

AGENTS = list(SERVICES)
AUTRE = {'visio': 'voix', 'voix': 'visio'}
GENEUR_DE = {'visio': 'h2', 'voix': 'h4'}
ACTIONS = ['rien', 'chemin0', 'chemin1', 'chemin2', 'chemin3', 'priorite', 'sans_priorite', 'brider', 'debrider']
R_OK, R_VIOL = 1.0, -1.0
COUT = dict(priorite=0.05, bride=0.10, detour=0.05)
COUT_CHANGEMENT = 0.5
MAINTIEN = 10
PROBLEMES = ['degrade', 'instable', 'congestion_udp', 'congestion_tcp', 'double_panne', 'double_congestion']
PANNE = {'degrade': lambda x: (1.5 + 100 * x, 1.0, 5 * x), 'instable': lambda x: (25.0, 20 * x, 0.2)}
CONTRAT = dict(lat=60.0, pertes=2.0, gigue=15.0, debit=0.9)
DIM_OBS = 12 + 12 + 12 + 4 + 6 + 3 + 9 + 4 + 3 + 4 + len(ACTIONS)
MEMOIRE_CHEMINS = 4


def obs_pour(n_entrees, obs):
    if obs.shape[-1] - n_entrees == MEMOIRE_CHEMINS:
        k = len(ACTIONS) + MEMOIRE_CHEMINS
        return np.concatenate([obs[..., :-k], obs[..., -len(ACTIONS):]], -1)
    return obs


def contrat_ok(m, seuils, mbps):
    return not (m['lat'] > seuils['lat'] or m['pertes'] > seuils['pertes'] or m['gigue'] > seuils['gigue']
                or m['debit'] < seuils['debit'] * mbps)


class Env3:
    def __init__(self, duree=600, randomiser=True, graine=0, intentions=None, progressif=0.0):
        self.duree, self.randomiser, self.fixe, self.progressif = duree, randomiser, intentions, progressif
        self.rng = random.Random(graine)

    def _programme(self):
        r, prog, t, reste = self.rng, [], 0, []
        while t < self.duree:
            if not reste:
                reste = PROBLEMES[:]; r.shuffle(reste)
            for sit in ('normal', reste.pop()):
                x = lambda: r.uniform(0.4, 1.5)
                if sit in ('degrade', 'instable'):
                    detail = [(r.choice(LIENS), sit, x())]
                elif sit == 'double_panne':
                    detail = [(l, r.choice(['degrade', 'instable']), x()) for l in r.sample(LIENS, 2)]
                elif sit == 'congestion_udp':
                    detail = [(r.choice(list(GENEURS)), 'udp', r.uniform(17, 35))]
                elif sit == 'congestion_tcp':
                    detail = [(r.choice(list(GENEURS)), 'tcp', 4)]
                elif sit == 'double_congestion':
                    detail = [(h, 'udp', r.uniform(12, 25)) for h in GENEURS]
                else:
                    detail = []
                d = r.randint(30, 120)
                if detail and self.progressif and sit != 'congestion_tcp' and r.random() < self.progressif:
                    rampe = min(r.randint(10, 40), d - 5)
                    n = 4 if 'congestion' in sit else max(2, rampe // 2)
                    for i in range(1, n + 1):
                        f = i / n
                        prog.append((t + round((i - 1) * rampe / n), f'{sit}|progressive',
                                     [(c, typ, (6 + (v - 6) * f) if typ == 'udp' else v * f) for c, typ, v in detail]))
                else:
                    prog.append((t, sit, detail))
                t += d
        return prog

    def _poser(self, detail):
        ph = self.ph
        for l in LIENS:
            ph.panne(*l)
        for h in GENEURS:
            ph.gener(h)
        for cible, typ, x in detail:
            if typ in PANNE:
                ph.panne(*cible, *PANNE[typ](x))
            else:
                ph.gener(cible, tcp=x) if typ == 'tcp' else ph.gener(cible, mbps=x)

    def situation(self, t):
        cur = self.prog[0]
        for p in self.prog:
            if p[0] <= t:
                cur = p
        return cur

    def _tirer_intention(self):
        r = self.rng
        seuils = dict(lat=r.choice([r.uniform(25, 60), r.uniform(60, 150)]), pertes=r.uniform(0.5, 4),
                      gigue=r.uniform(5, 30), debit=r.uniform(0.80, 0.97))
        interdits = {a for a in ('priorite', 'brider') if r.random() < 0.15}
        if r.random() < 0.15:
            interdits |= {f'chemin{k}' for k in (r.choice([(1, 3), (0, 2)]))}
        return dict(seuils=seuils, interdits=interdits)

    def _nouveau_reseau(self):
        r = self.rng
        ph = Physique(graine=r.randrange(1 << 30))
        if self.randomiser:
            ph.k_pertes = r.uniform(1.2, 2.0)
            ph.file_max *= r.uniform(0.85, 1.15)
            ph.tcp_file = r.uniform(1.5, 2.0)
        return ph

    def reset(self):
        r = self.rng
        self.ph = self._nouveau_reseau()
        self.intent = {s: (self.fixe[s] if self.fixe else self._tirer_intention()) for s in AGENTS}
        self.intent = {s: dict(seuils=dict(CONTRAT, **i.get('seuils', {})), interdits=set(i.get('interdits', ()))) for s, i in self.intent.items()}
        for s in AGENTS:
            if all(f'chemin{k}' in self.intent[s]['interdits'] for k in range(4)):
                self.intent[s]['interdits'].discard('chemin0')
        self.prog = self._programme()
        for s in AGENTS:
            self.ph.chemin[s] = next(k for k in range(4) if f'chemin{k}' not in self.intent[s]['interdits'])
        self.t = 0
        self.depuis_action = {s: 30 for s in AGENTS}
        self.depuis_chemin = {s: 60 for s in AGENTS}
        self.mauvais = {s: 0 for s in AGENTS}
        self.bon_depuis = {s: [60] * 4 for s in AGENTS}
        self.histo = {s: [] for s in AGENTS}
        self.liens = {}
        self._poser(self.prog[0][2])
        for _ in range(3):
            self._mesurer(None)
        return {s: self.obs(s) for s in AGENTS}

    def _mesurer(self, evenement):
        m = self.ph.seconde(evenement)
        self.liens = m['liens']
        for s in AGENTS:
            self.histo[s] = (self.histo[s] + [m[s]])[-3:]
        return m

    def ok(self, s, m=None):
        return contrat_ok(m or self.histo[s][-1], self.intent[s]['seuils'], SERVICES[s]['mbps'])

    def autorisees(self, s):
        i = self.intent[s]['interdits']
        return np.array([a not in i for a in ACTIONS])

    def _appliquer(self, s, a):
        ph, nom, g = self.ph, ACTIONS[a], GENEUR_DE[s]
        if not self.autorisees(s)[a]:
            return 0
        retour = nom in ('sans_priorite', 'debrider') or (nom == 'chemin0' and ph.chemin[s] != 0)
        if retour and self.depuis_action[s] < MAINTIEN:
            return 0
        if nom.startswith('chemin'):
            k = int(nom[-1])
            if ph.chemin[s] == k:
                return 0
            ph.chemin[s] = k; self.depuis_chemin[s] = 0
        elif nom in ('priorite', 'sans_priorite'):
            if ph.prio[s] == (nom == 'priorite'):
                return 0
            ph.prio[s] = nom == 'priorite'
        elif nom in ('brider', 'debrider'):
            if ph.limite[g] == (nom == 'brider'):
                return 0
            ph.limite[g] = nom == 'brider'
        else:
            return 0
        return 1

    def step(self, actions):
        avant = self.situation(self.t)
        changes = {}

        def evenement():
            for s in AGENTS:
                changes[s] = self._appliquer(s, actions[s])
            if self.situation(self.t + 1) is not avant:
                self._poser(self.situation(self.t + 1)[2])

        m = self._mesurer(evenement)
        self.t += 1
        ph, oks = self.ph, {s: self.ok(s) for s in AGENTS}
        rec = {}
        for s in AGENTS:
            self.depuis_action[s] = 0 if changes[s] else min(self.depuis_action[s] + 1, 30)
            self.depuis_chemin[s] = min(self.depuis_chemin[s] + 1, 60) if not (changes[s] and ACTIONS[actions[s]].startswith('chemin')) else 0
            self.mauvais[s] = 0 if oks[s] else min(self.mauvais[s] + 1, 30)
            seuil = 0.8 * self.intent[s]['seuils']['lat']
            self.bon_depuis[s] = [0 if x > seuil else min(b + 1, 60) for x, b in zip(m[s]['sondes'], self.bon_depuis[s])]
            cout = (COUT['priorite'] * ph.prio[s] + COUT['bride'] * ph.limite[GENEUR_DE[s]] + COUT['detour'] * (ph.chemin[s] != 0)
                    + COUT_CHANGEMENT * changes[s])
            rec[s] = 0.5 * (R_OK if oks[s] else R_VIOL) + 0.5 * (R_OK if oks[AUTRE[s]] else R_VIOL) - cout
        info = dict(situation=self.situation(self.t)[1], ok=oks, changes=dict(changes), mesure={s: m[s] for s in AGENTS},
                    chemin=dict(ph.chemin), prio=dict(ph.prio), limite=dict(ph.limite))
        return {s: self.obs(s) for s in AGENTS}, rec, self.t >= self.duree, info

    def obs(self, s):
        ph, se, mbps, o = self.ph, self.intent[s]['seuils'], SERVICES[s]['mbps'], AUTRE[s]
        v = []
        for m in self.histo[s]:
            v += [min(m['lat'], LAT_ABSENTE) / 100, min(m['pertes'], 20) / 5, min(m['gigue'], 60) / 30, m['debit'] / mbps]
        for m in self.histo[s]:
            v += [min(m['lat'] / se['lat'], 10) / 3, min(m['pertes'] / se['pertes'], 10) / 3, min(m['gigue'] / se['gigue'], 10) / 3,
                  m['debit'] / (se['debit'] * mbps)]
        for m in self.histo[s]:
            v += [min(x, LAT_ABSENTE) / 100 for x in m['sondes']]
        v += [min(x / se['lat'], 10) / 3 for x in self.histo[s][-1]['sondes']]
        v += [float(ph.chemin[s] == k) for k in range(4)] + [float(ph.prio[s]), float(ph.limite[GENEUR_DE[s]])]
        v += [self.liens[l][0] / 100 for l in chemins_de(s)[ph.chemin[s]]]
        v += [float(ph.chemin[o] == k) for k in range(4)] + [float(ph.prio[o]), float(ph.limite[GENEUR_DE[o]])]
        v += [float(self.ok(o, m)) for m in self.histo[o]]
        v += [se['lat'] / 100, se['pertes'] / 5, se['gigue'] / 30, se['debit']]
        v += [self.mauvais[s] / 30, self.depuis_chemin[s] / 60, self.depuis_action[s] / 30]
        v += [b / 60 for b in self.bon_depuis[s]]
        v += [float(x) for x in self.autorisees(s)]
        return np.array(v, dtype=np.float32)


def masque_obs(obs):
    return obs[..., -len(ACTIONS):] > 0.5


def politique_rien(obs, env, s):
    return 0


class Regle:

    def __init__(self):
        self.bon = {}

    def __call__(self, obs, env, s):
        if env.t == 0:
            self.bon[s] = 0
        ph, permis, seuil = env.ph, env.autorisees(s), env.intent[s]['seuils']['lat']
        A = ACTIONS.index
        if env.ok(s):
            self.bon[s] += 1
            if self.bon[s] >= 20 and env.depuis_action[s] >= MAINTIEN:
                sonde0 = max(m['sondes'][0] for m in env.histo[s])
                if ph.chemin[s] != 0 and sonde0 < 0.8 * seuil and permis[A('chemin0')]:
                    return A('chemin0')
                if ph.prio[s]:
                    return A('sans_priorite')
                if ph.limite[GENEUR_DE[s]]:
                    return A('debrider')
            return 0
        self.bon[s] = 0
        if env.mauvais[s] < 3 or env.depuis_action[s] < 3:
            return 0
        sondes = [max(m['sondes'][k] for m in env.histo[s][-2:]) for k in range(4)]
        bons = [k for k in range(4) if permis[A(f'chemin{k}')] and sondes[k] < 0.8 * seuil and k != ph.chemin[s]]
        if sondes[ph.chemin[s]] >= 0.8 * seuil and bons:
            return A(f'chemin{min(bons, key=lambda k: sondes[k])}')
        if not ph.prio[s] and permis[A('priorite')]:
            return A('priorite')
        if not ph.limite[GENEUR_DE[s]] and permis[A('brider')]:
            return A('brider')
        for k in range(1, 4):
            suivant = (ph.chemin[s] + k) % 4
            if permis[A(f'chemin{suivant}')] and env.depuis_action[s] >= 6:
                return A(f'chemin{suivant}')
        return 0


def evaluer(politique, graines=range(5), randomiser=False, intentions=None, duree=600, progressif=0.0):
    res = []
    for g in graines:
        env = Env3(duree=duree, randomiser=randomiser, graine=1000 + g, intentions=intentions, progressif=progressif)
        obs, fin, total, hors, chg = env.reset(), False, 0.0, {s: 0 for s in AGENTS}, 0
        while not fin:
            obs, r, fin, info = env.step({s: politique(obs[s], env, s) for s in AGENTS})
            total += sum(r.values()) / len(AGENTS)
            chg += sum(info['changes'].values())
            for s in AGENTS:
                hors[s] += not info['ok'][s]
        res.append((total, 100 * hors['visio'] / env.duree, 100 * hors['voix'] / env.duree, chg))
    return np.array(res)


INTENTION_DEFAUT = {s: dict(seuils=dict(CONTRAT), interdits=set()) for s in AGENTS}


if __name__ == '__main__':
    import time
    env = Env3(graine=1)
    o = env.reset()
    assert o['visio'].shape == (DIM_OBS,), (o['visio'].shape, DIM_OBS)
    print(f'observation : {DIM_OBS} nombres par agent ; {len(ACTIONS)} actions ; programme de l\'épisode 1 :')
    for t, sit, detail in env.prog:
        print(f'   {t:>4} s  {sit:<18}', [(f'{c[0]}→{c[1]}' if isinstance(c, tuple) else c, typ, round(x, 1)) for c, typ, x in detail])
    print('\nPolitiques de référence, contrat par défaut, 5 épisodes de 10 min (moyenne ± écart-type)')
    print(f'{"politique":<26}{"récompense":>14}{"visio hors contrat":>22}{"voix hors contrat":>21}{"changements":>14}')
    for nom, pol in (('ne rien faire', politique_rien), ('règle manuelle (sondes)', Regle())):
        for rand in (False, True):
            t0 = time.time()
            r = evaluer(pol, randomiser=rand, intentions=INTENTION_DEFAUT)
            m, e = r.mean(0), r.std(0)
            print(f'{nom + (" (rand.)" if rand else ""):<26}{m[0]:>8.0f} ± {e[0]:<4.0f}{m[1]:>14.1f} ± {e[1]:<4.1f} %{m[2]:>12.1f} ± {e[2]:<4.1f} %{m[3]:>10.0f}'
                  f'    [{3000 / (time.time() - t0):.0f} s simulées/s]')
