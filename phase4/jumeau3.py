import itertools
import math
import random

COEUR, DISTRIB = ['c1', 'c2'], ['d1', 'd2']
HOTES = {'h1': 'a1', 'h2': 'a1', 'h3': 'a2', 'h4': 'a2', 'h5': 'a3', 'h6': 'a3', 'h7': 'a4', 'h8': 'a4'}
SERVICES = {'visio': dict(src='h1', dst='h5', mbps=4), 'voix': dict(src='h3', dst='h7', mbps=1)}
GENEURS = {'h2': 'h6', 'h4': 'h8'}


def chemins(src, dst):
    return [[(src, d), (d, c), (c, dst)] for d, c in itertools.product(DISTRIB, COEUR)]


_CHEMINS = {}


def chemins_de(flux):
    if flux not in _CHEMINS:
        f = SERVICES.get(flux) or dict(src=flux, dst=GENEURS[flux])
        _CHEMINS[flux] = chemins(HOTES[f['src']], HOTES[f['dst']])
    return _CHEMINS[flux]


LIENS = sorted({l for s in SERVICES for p in chemins_de(s) for l in p})
COUCHES = [[l for l in LIENS if l[0][0] == x] for x in 'adc']

CAPACITE = 20.0
SURCOUT = 1.035
FILE_MAX = 9.94
RTT_BASE = 9.7
DELAI_BASE = 1.5
LIMITE_MBPS = 5.0
K_PERTES = 1.6
TCP_ATTENTE = 0.079
TCP_FILE = 1.75
SOUS_PAS = 5
DECALAGE = 3
LAT_ABSENTE = 1000.0
GIGUE_NETEM = [(0, 0.2), (1, 1.2), (8, 9.4), (14, 15.6), (20, 21.9), (30, 28.2)]


_SAIN = (DELAI_BASE, 0.0, 0.0)
_CALME = {0: (0.0, 0.0, 0.2), 1: (0.0, 0.0, 0.2)}


def _gigue(j):
    for (x0, y0), (x1, y1) in zip(GIGUE_NETEM, GIGUE_NETEM[1:]):
        if j <= x1:
            return y0 + (y1 - y0) * (j - x0) / (x1 - x0)
    return GIGUE_NETEM[-1][1]


class Physique:

    def __init__(self, graine=0, alea=1.0):
        self.rng = random.Random(graine)
        self.alea = alea
        self.k_pertes, self.tcp_file, self.file_max = K_PERTES, TCP_FILE, FILE_MAX
        self.netem = {l: (DELAI_BASE, 0.0, 0.0) for l in LIENS}
        self.file = {(l, c): 0.0 for l in LIENS for c in (0, 1)}
        self.geneurs = {h: None for h in GENEURS}
        self.chemin = {s: 0 for s in SERVICES}
        self.prio = {s: False for s in SERVICES}
        self.limite = {h: False for h in GENEURS}

    def panne(self, a, b, delai=DELAI_BASE, gigue=0.0, pertes=0.0):
        self.netem[a, b] = (delai, gigue, pertes)

    def gener(self, hote, mbps=0.0, tcp=0):
        self.geneurs[hote] = ('tcp', tcp) if tcp else ('udp', mbps) if mbps else None

    def reinitialiser(self):
        for s in SERVICES:
            self.chemin[s], self.prio[s] = 0, False
        for h in GENEURS:
            self.limite[h] = False

    def _sous_pas(self, dt):
        flux = []
        for s, d in SERVICES.items():
            flux.append([s, d['mbps'] * SURCOUT, chemins_de(s)[self.chemin[s]], 0 if self.prio[s] else 1, 'petit', math.inf])
        for h, g in self.geneurs.items():
            if g:
                plafond = LIMITE_MBPS * SURCOUT if self.limite[h] else math.inf
                debit = 0.0 if g[0] == 'tcp' else min(g[1] * SURCOUT, plafond)
                flux.append([h, debit, chemins_de(h)[0], 1, g[0], plafond])
        etat, sortie = {}, {}
        for couche in range(3):
            for l in COUCHES[couche]:
                ici = [f for f in flux if f[2][couche] == l]
                if self.netem[l] == _SAIN and not self.file[l, 0] and not self.file[l, 1]:
                    total = 0.0
                    for f in ici:
                        if f[4] == 'tcp':
                            total = math.inf
                        total += f[1]
                    if total <= CAPACITE:
                        etat[l] = _CALME; sortie[l] = (total, 0.0)
                        continue
                delai, gigue, pertes = self.netem[l]
                survie_netem = 1 - pertes / 100
                for f in ici:
                    if f[4] != 'tcp':
                        f[1] *= survie_netem
                arr0 = sum(f[1] for f in ici if f[3] == 0)
                q0 = self.file[l, 0]
                out0 = min(arr0 + q0 / dt, CAPACITE)
                self.file[l, 0] = min(self.file_max, max(0.0, q0 + (arr0 - out0) * dt))
                cap = max(0.5, CAPACITE - out0)
                q1 = self.file[l, 1]
                tcp = [f for f in ici if f[4] == 'tcp']
                if couche == 0:
                    fixes = sum(f[1] for f in ici if f[3] == 1 and f[4] != 'tcp')
                    for f in tcp:
                        f[1] = min(f[5], max(0.3, cap - fixes))
                        if f[1] < f[5]:
                            q1 = max(q1, TCP_ATTENTE * cap)
                arr1 = sum(f[1] for f in ici if f[3] == 1)
                place = self.file_max * (self.tcp_file if tcp else 1.0)
                place = max(0.5, place - min(arr1, cap) * (delai - DELAI_BASE) / 1000)
                q1 = min(q1, place)
                q = q1 + (arr1 - cap) * dt
                deborde = max(0.0, q - place)
                if tcp and deborde:
                    for f in tcp:
                        f[1] = max(0.0, f[1] - deborde / dt / len(tcp))
                    arr1 -= deborde / dt
                    deborde = 0.0
                f_perte = min(1.0, deborde / (arr1 * dt)) if arr1 > 0 else 0.0
                self.file[l, 1] = min(place, max(0.0, q))
                servi1 = min(arr1 + q1 / dt, cap)
                attente = {0: (q0 + self.file[l, 0]) / 2 / CAPACITE, 1: (q1 + self.file[l, 1]) / 2 / cap}
                perte = {0: 0.0, 1: 1 - (1 - f_perte) ** self.k_pertes}
                etat[l] = {c: (1000 * attente[c] + delai - DELAI_BASE, 1 - (1 - perte[c]) * survie_netem, _gigue(gigue)) for c in (0, 1)}
                poids = {id(f): f[1] * (1 - (0.0 if f[3] == 0 else perte[1] if f[4] == 'petit' else f_perte)) for f in ici}
                tot = {c: sum(poids[id(f)] for f in ici if f[3] == c) for c in (0, 1)}
                for f in ici:
                    servi = out0 if f[3] == 0 else servi1
                    f[1] = servi * poids[id(f)] / tot[f[3]] if tot[f[3]] > 0 else 0.0
                sortie[l] = (servi1 + out0, deborde)
        return etat, sortie

    def _composer(self, etat, liens, classe):
        lat = RTT_BASE + sum(etat[l][classe][0] for l in liens)
        survie = 1.0
        for l in liens:
            survie *= 1 - etat[l][classe][1]
        return lat, 1 - survie, math.sqrt(sum(etat[l][classe][2] ** 2 for l in liens))

    def seconde(self, evenement=None):
        dt, rng, a = 1.0 / SOUS_PAS, self.rng, self.alea
        ech = {s: [] for s in SERVICES}
        sond = {s: [[] for _ in range(4)] for s in SERVICES}
        occ = {l: [0.0, 0.0] for l in LIENS}
        for i in range(SOUS_PAS):
            if evenement and i == DECALAGE:
                evenement()
            etat, sortie = self._sous_pas(dt)
            for l in LIENS:
                occ[l][0] += sortie[l][0] / SOUS_PAS; occ[l][1] += sortie[l][1]
            for s in SERVICES:
                c = 0 if self.prio[s] else 1
                ech[s].append(self._composer(etat, chemins_de(s)[self.chemin[s]], c))
                for k, p in enumerate(chemins_de(s)):
                    sond[s][k].append(self._composer(etat, p, c))
        m = {}
        for s, d in SERVICES.items():
            paquets = round(d['mbps'] * 1e6 / 9600)
            p = sum(e[1] for e in ech[s]) / SOUS_PAS
            perdus = (rng.binomialvariate(paquets, min(1.0, p)) if p > 0 else 0) if a else p * paquets
            pertes = 100 * perdus / paquets
            gigue = sum(e[2] for e in ech[s]) / SOUS_PAS
            m[s] = dict(lat=self._ping(ech[s]), pertes=pertes, debit=d['mbps'] * (1 - pertes / 100),
                        gigue=max(0.02, gigue * (1 + a * rng.gauss(0, 0.12))),
                        sondes=[self._ping(x) for x in sond[s]])
        m['liens'] = {l: (round(100 * occ[l][0] / CAPACITE, 1), round(1000 * self.file[l, 1] / self.file_max),
                          round(occ[l][1] * 1e6 / 9936)) for l in LIENS}
        return m

    def _ping(self, echantillons):
        rng, a, recus = self.rng, self.alea, []
        for lat, perte, gigue in echantillons:
            if a and rng.random() < perte:
                continue
            bruit = rng.gauss(0, 0.12 + 0.35 * gigue) if a else 0.0
            recus.append(max(0.5, lat + bruit))
        if not recus:
            return LAT_ABSENTE
        recus.sort()
        n = len(recus)
        return recus[n // 2] if n % 2 else (recus[n // 2 - 1] + recus[n // 2]) / 2
