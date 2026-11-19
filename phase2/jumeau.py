import csv
import math
import os
import random
from collections import defaultdict
from pathlib import Path

import numpy as np

CALIB = Path(__file__).resolve().parent.parent / 'lab' / os.environ.get('CALIB', 'calib_varie_garde.csv')
SITUATIONS = ['normal', 'congestion_udp', 'congestion_tcp', 'degrade', 'instable', 'secours_degrade']
PROBLEMES = SITUATIONS[1:]
ACTIONS = ['rien', 'limiter_h2', 'priorite_visio', 'deroute_visio', 'reinitialiser']
SEUILS = dict(lat=60.0, pertes=2.0, gigue=15.0, debit=3.6)
LAT_ABSENTE = 1000.0

R_OK, R_VIOL = 1.0, -1.0
COUT = dict(deroute=0.3, limite=0.1, priorite=0.05)
COUT_CHANGEMENT = float(os.environ.get('COUT_CHANGEMENT', 0.5))
MAINTIEN = int(os.environ.get('MAINTIEN', 10))
MODE_MAINTIEN = os.environ.get('MODE_MAINTIEN', 'retour')
VISIO_MBPS, GOULOT_MBPS, LIMITE_H2_MBPS, FILE_MAX = 4, 20, 5, 12.0
FILE_DYNAMIQUE = int(os.environ.get('FILE_DYNAMIQUE', 1))
CORRECTION_SONDE = int(os.environ.get('CORRECTION_SONDE', 1))
SONDE = int(os.environ.get('SONDE', 1))


def violation(m):
    return (m['lat'] > SEUILS['lat'] or m['pertes'] > SEUILS['pertes']
            or m['gigue'] > SEUILS['gigue'] or m['debit'] < SEUILS['debit'])


def charger(chemin=CALIB):
    runs = defaultdict(list)
    for r in csv.DictReader(open(chemin, encoding='utf-8')):
        if r['valide'] != '1':
            continue
        m = dict(lat=float(r['lat']) if r['lat'] else LAT_ABSENTE, pertes=float(r['vpertes'] or 100),
                 gigue=float(r['vgigue']), debit=float(r['vdebit']))
        runs[(r['rep'], r['situation'], r['action'], float(r['intensite']))].append((int(r['k']), m))
    series = defaultdict(list)
    for (_, sit, act, x), rows in runs.items():
        rows.sort(key=lambda t: t[0])
        avant = [m for k, m in rows if k < 0]
        apres = [m for k, m in rows if k >= 5]
        series[(sit, 'rien')].append((x, avant))
        series[(sit, act)].append((x, apres))
    return series


def config_mesuree(leviers):
    if leviers['deroute']:
        return 'deroute_visio'
    if leviers['priorite']:
        return 'priorite_visio'
    if leviers['limite']:
        return 'limiter_h2'
    return 'rien'


class Jumeau:
    DIM_OBS = 3 * 4 + 3 * SONDE + 3 + 1

    def __init__(self, series=None, duree=600, randomiser=True, graine=0):
        self.series = series or charger()
        self.duree, self.randomiser = duree, randomiser
        self.rng = random.Random(graine)

    def _programme(self):
        prog, t, problemes = [], 0, []
        while t < self.duree:
            if not problemes:
                problemes = PROBLEMES[:]; self.rng.shuffle(problemes)
            for sit in ('normal', problemes.pop()):
                d = self.rng.randint(30, 120)
                prog.append((t, sit, 1.0 if sit == 'normal' else self.rng.uniform(0.4, 1.5)))
                t += d
        return prog

    def _situation(self, t):
        cur = self.prog[0]
        for p in self.prog:
            if p[0] <= t:
                cur = p
        return cur[1], cur[2]

    def _nouvelle_serie(self, sit, x, conf, voie):
        cand = self.series[(sit, conf)]
        poids = [math.exp(-abs(xc - x) / 0.2) for xc, _ in cand]
        _, secondes = self.rng.choices(cand, weights=poids)[0]
        self.lectures[voie] = (sit, conf, secondes, self.rng.randrange(len(secondes)))

    def _mesure(self, sit, x, conf, voie='visio'):
        lu = self.lectures.get(voie)
        if lu is None or lu[0] != sit or lu[1] != conf:
            self._nouvelle_serie(sit, x, conf, voie)
        s, c, secondes, i = self.lectures[voie]
        if i >= len(secondes):
            self._nouvelle_serie(sit, x, conf, voie)
            s, c, secondes, i = self.lectures[voie]
        self.lectures[voie] = (s, c, secondes, i + 1)
        m = dict(secondes[i])
        for k in ('lat', 'gigue', 'pertes'):
            m[k] *= self.facteurs[k]
        return m

    def reset(self):
        self.t, self.lectures = 0, {}
        self.prog = self._programme()
        self.leviers = dict(limite=False, priorite=False, deroute=False)
        self.effectif = dict(self.leviers)
        self.attente = []
        self.depuis_action = 30
        r = self.randomiser
        self.facteurs = {k: self.rng.uniform(0.7, 1.3) if r else 1.0 for k in ('lat', 'gigue', 'pertes')}
        self.file = 0.0
        self.fq = self.rng.uniform(0.7, 1.3) if (r and FILE_DYNAMIQUE) else 1.0
        self.decalage = self.rng.choice([-1, 0, 1]) if r else 0
        self.histo, self.sonde = [], []
        for _ in range(3):
            self._avancer_mesure()
        return self._obs()

    def _delai(self, levier, sit):
        base = {'limite': 2 if sit == 'congestion_tcp' else 1, 'priorite': 1, 'deroute': 1}[levier]
        return max(0, base + self.decalage)

    def _maj_file(self, sit, x):
        e = self.effectif
        if sit == 'congestion_udp':
            h2 = min(25 * x, LIMITE_H2_MBPS) if e['limite'] else 25 * x
            arrivees = h2 + (VISIO_MBPS if not e['deroute'] else 0)
            exces = arrivees - GOULOT_MBPS + (0.5 if arrivees >= GOULOT_MBPS - 0.5 else 0)
            self.file = min(FILE_MAX, max(0.0, self.file + exces * self.fq))
        elif sit == 'congestion_tcp' and not e['limite']:
            self.file = min(FILE_MAX, max(self.file, 2.6 * self.fq) + 0.6 * self.fq)
        else:
            self.file = max(0.0, self.file - 16)

    def _plafond_file(self, sit, conf, m):
        if (FILE_DYNAMIQUE and sit.startswith('congestion') and conf in ('rien', 'limiter_h2')
                and self.file < 0.97 * FILE_MAX):
            lat = (10.2 + 1000 * self.file / GOULOT_MBPS) * self.facteurs['lat']
            m = dict(m, lat=min(m['lat'], lat), pertes=0.0, debit=max(m['debit'], 3.85), gigue=min(m['gigue'], 1.0))
        return m

    def _avancer_mesure(self):
        sit, x = self._situation(self.t)
        if FILE_DYNAMIQUE:
            self._maj_file(sit, x)
        m = self._plafond_file(sit, config_mesuree(self.effectif), self._mesure(sit, x, config_mesuree(self.effectif)))
        self.histo = (self.histo + [m])[-3:]
        if SONDE:
            principal = config_mesuree(dict(self.effectif, deroute=False))
            xs = x - VISIO_MBPS / 25 if sit == 'congestion_udp' and CORRECTION_SONDE else x
            lat = m['lat'] if not self.effectif['deroute'] else \
                self._plafond_file(sit, principal, self._mesure(sit, xs, principal, 'sonde'))['lat']
            self.sonde = (self.sonde + [lat])[-3:]
        return sit, m

    def _obs(self):
        v = []
        for m in self.histo:
            v += [min(m['lat'], LAT_ABSENTE) / 100, min(m['pertes'], 20) / 5, min(m['gigue'], 60) / 30, m['debit'] / 4]
        v += [min(l, LAT_ABSENTE) / 100 for l in self.sonde]
        v += [float(self.leviers[k]) for k in ('limite', 'priorite', 'deroute')]
        v.append(min(self.depuis_action, 30) / 30)
        return np.array(v, dtype=np.float32)

    def step(self, a):
        nom = ACTIONS[a]
        voulu = dict(self.leviers)
        if nom == 'limiter_h2':
            voulu['limite'] = True
        elif nom == 'priorite_visio':
            voulu['priorite'] = True
        elif nom == 'deroute_visio':
            voulu['deroute'] = True
        elif nom == 'reinitialiser':
            voulu = dict(limite=False, priorite=False, deroute=False)
        sit, _ = self._situation(self.t)
        changes = [k for k in voulu if voulu[k] != self.leviers[k]]
        retour = any(self.leviers[k] and not voulu[k] for k in changes)
        if changes and self.depuis_action < MAINTIEN and (MODE_MAINTIEN == 'tout' or retour):
            changes, voulu = [], dict(self.leviers)
        for k in changes:
            self.attente.append((self.t + self._delai(k, sit), k, voulu[k]))
        self.leviers = voulu
        self.depuis_action = 0 if changes else self.depuis_action + 1

        self.t += 1
        for e in [e for e in self.attente if e[0] <= self.t]:
            self.effectif[e[1]] = e[2]
            self.attente.remove(e)
        sit, m = self._avancer_mesure()
        viol = violation(m)
        r = (R_VIOL if viol else R_OK) - sum(COUT[k] for k in COUT if self.leviers[k]) - COUT_CHANGEMENT * len(changes)
        done = self.t >= self.duree
        return self._obs(), r, done, dict(situation=sit, violation=viol, leviers=dict(self.leviers))


def politique_rien(obs, env):
    return 0


class RegleManuelle:

    def __call__(self, obs, env):
        if env.t == 0 or not hasattr(self, 'mauvais'):
            self.mauvais = self.bon = 0
        m = env.histo[-1]
        if violation(m):
            self.mauvais += 1; self.bon = 0
        else:
            self.bon += 1; self.mauvais = 0
        lv = env.leviers
        if self.mauvais >= 3 and env.depuis_action >= 3:
            if not lv['priorite']:
                return ACTIONS.index('priorite_visio')
            if not lv['deroute']:
                return ACTIONS.index('deroute_visio')
        if self.bon >= 20 and any(lv.values()):
            return ACTIONS.index('reinitialiser')
        return 0


def politique_oracle(obs, env):
    sit, _ = env._situation(env.t)
    voulu = dict(limite=False, priorite=sit.startswith('congestion'), deroute=sit in ('degrade', 'instable'))
    if voulu == env.leviers:
        return 0
    if not any(voulu.values()):
        return ACTIONS.index('reinitialiser')
    return ACTIONS.index('deroute_visio' if voulu['deroute'] else 'priorite_visio')


def evaluer(politique, graines=range(5), randomiser=False, series=None):
    res = []
    for g in graines:
        env = Jumeau(series=series, randomiser=randomiser, graine=1000 + g)
        obs, total, viols, deroute = env.reset(), 0.0, 0, 0
        done = False
        while not done:
            obs, r, done, info = env.step(politique(obs, env))
            total += r; viols += info['violation']; deroute += info['leviers']['deroute']
        res.append((total, 100 * viols / env.duree, 100 * deroute / env.duree))
    a = np.array(res)
    return a.mean(0), a.std(0)


def fidelite(series):
    print('Fidélité : % de secondes en violation après l\'action (réel Mininet  vs  jumeau)')
    print(f'{"situation":<17}' + ''.join(f'{a:>17}' for a in ACTIONS[:4]))
    ecart_max = 0
    for sit in SITUATIONS:
        ligne = f'{sit:<17}'
        for act in ACTIONS[:4]:
            reel = [violation(m) for _, sec in series[(sit, act)] for m in sec]
            env = Jumeau(series=series, randomiser=False, graine=7)
            env.reset(); env.prog = [(0, sit, 1.0)]
            sim = []
            for rep in range(40):
                env.lectures = {}
                env.effectif = dict(limite=act == 'limiter_h2', priorite=act == 'priorite_visio', deroute=act == 'deroute_visio')
                env.prog = [(0, sit, env.rng.uniform(0.4, 1.5) if sit != 'normal' else 1.0)]
                sim += [violation(env._mesure(sit, env.prog[0][2], config_mesuree(env.effectif))) for _ in range(10)]
            pr, ps = 100 * np.mean(reel), 100 * np.mean(sim)
            ecart_max = max(ecart_max, abs(pr - ps))
            ligne += f'{pr:>8.0f} vs {ps:>4.0f}  '
        print(ligne)
    print(f'écart maximal : {ecart_max:.0f} points\n')


if __name__ == '__main__':
    series = charger()
    print(f'{sum(len(v) for v in series.values())} séries réelles chargées depuis {CALIB.name}\n')
    fidelite(series)
    print('Politiques de référence sur 5 épisodes de 10 min (moyenne ± écart-type)')
    print(f'{"politique":<22}{"récompense":>16}{"violation %":>14}{"secours %":>12}')
    for nom, pol in (('ne rien faire', politique_rien), ('règle manuelle', RegleManuelle()),
                     ('oracle (triche)', politique_oracle)):
        for rand in (False, True):
            (r, v, d), (sr, sv, sd) = evaluer(pol, randomiser=rand, series=series)
            print(f'{nom + (" (rand.)" if rand else ""):<22}{r:>9.0f} ± {sr:<5.0f}{v:>9.1f} ± {sv:<4.1f}{d:>7.1f}')
