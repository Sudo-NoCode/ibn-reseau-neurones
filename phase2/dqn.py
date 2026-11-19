import sys
import time

import numpy as np

from jumeau import ACTIONS, Jumeau, RegleManuelle, charger, evaluer, politique_oracle, politique_rien

GAMMA, LR, BATCH = 0.95, 1e-3, 64
MEMOIRE, DEBUT, CIBLE_TOUS = 50_000, 2_000, 1_000
EPS_DEBUT, EPS_FIN, EPS_DUREE = 1.0, 0.05, 60_000
CACHE = 64
VALID_TOUS = 10_000


class Reseau:

    def __init__(self, n_in, n_out, rng):
        def couche(a, b):
            return rng.normal(0, np.sqrt(2 / a), (a, b)), np.zeros(b)
        self.p = dict(zip(('W1', 'b1', 'W2', 'b2', 'W3', 'b3'),
                          [*couche(n_in, CACHE), *couche(CACHE, CACHE), *couche(CACHE, n_out)]))
        self.p['W3'] *= 0.1
        self.m = {k: np.zeros_like(v) for k, v in self.p.items()}
        self.v = {k: np.zeros_like(v) for k, v in self.p.items()}
        self.t = 0

    def copie_de(self, autre):
        self.p = {k: v.copy() for k, v in autre.p.items()}

    def avant(self, x):
        p = self.p
        z1 = x @ p['W1'] + p['b1']; h1 = np.maximum(z1, 0)
        z2 = h1 @ p['W2'] + p['b2']; h2 = np.maximum(z2, 0)
        q = h2 @ p['W3'] + p['b3']
        self.cache = (x, z1, h1, z2, h2)
        return q

    def apprendre(self, x, a, cible):
        q = self.avant(x)
        x, z1, h1, z2, h2 = self.cache
        err = q[np.arange(len(a)), a] - cible
        d = np.clip(err, -1, 1) / len(a)
        dq = np.zeros_like(q); dq[np.arange(len(a)), a] = d
        g = {}
        g['W3'] = h2.T @ dq; g['b3'] = dq.sum(0)
        dh2 = dq @ self.p['W3'].T * (z2 > 0)
        g['W2'] = h1.T @ dh2; g['b2'] = dh2.sum(0)
        dh1 = dh2 @ self.p['W2'].T * (z1 > 0)
        g['W1'] = x.T @ dh1; g['b1'] = dh1.sum(0)
        norme = np.sqrt(sum((v ** 2).sum() for v in g.values()))
        if norme > 10:
            g = {k: v * 10 / norme for k, v in g.items()}
        self.t += 1
        for k in self.p:
            self.m[k] = 0.9 * self.m[k] + 0.1 * g[k]
            self.v[k] = 0.999 * self.v[k] + 0.001 * g[k] ** 2
            mh, vh = self.m[k] / (1 - 0.9 ** self.t), self.v[k] / (1 - 0.999 ** self.t)
            self.p[k] -= LR * mh / (np.sqrt(vh) + 1e-8)
        return float(np.mean(np.abs(err)))


class Memoire:
    def __init__(self, n, dim):
        self.s = np.zeros((n, dim), np.float32); self.s2 = np.zeros((n, dim), np.float32)
        self.a = np.zeros(n, np.int64); self.r = np.zeros(n, np.float32); self.fin = np.zeros(n, np.float32)
        self.n, self.i, self.plein = n, 0, False

    def ajouter(self, s, a, r, s2, fin):
        j = self.i
        self.s[j], self.a[j], self.r[j], self.s2[j], self.fin[j] = s, a, r, s2, fin
        self.i = (j + 1) % self.n; self.plein |= self.i == 0

    def tirer(self, k, rng):
        idx = rng.integers(0, self.n if self.plein else self.i, k)
        return self.s[idx], self.a[idx], self.r[idx], self.s2[idx], self.fin[idx]


def politique_de(net):
    return lambda obs, env: int(np.argmax(net.avant(obs[None])[0]))


def entrainer(graine, etapes, series):
    rng = np.random.default_rng(graine)
    env = Jumeau(series=series, randomiser=True, graine=graine)
    net, cible = Reseau(Jumeau.DIM_OBS, len(ACTIONS), rng), Reseau(Jumeau.DIM_OBS, len(ACTIONS), rng)
    cible.copie_de(net)
    mem = Memoire(MEMOIRE, Jumeau.DIM_OBS)
    s = env.reset()
    meilleur, meilleurs_poids, courbe = -1e9, None, []
    for t in range(1, etapes + 1):
        eps = max(EPS_FIN, EPS_DEBUT - (EPS_DEBUT - EPS_FIN) * t / EPS_DUREE)
        a = int(rng.integers(len(ACTIONS))) if rng.random() < eps else int(np.argmax(net.avant(s[None])[0]))
        s2, r, fin, _ = env.step(a)
        mem.ajouter(s, a, r, s2, fin)
        s = env.reset() if fin else s2
        if t >= DEBUT:
            bs, ba, br, bs2, bfin = mem.tirer(BATCH, rng)
            a_suiv = np.argmax(net.avant(bs2), 1)
            q_suiv = cible.avant(bs2)[np.arange(BATCH), a_suiv]
            net.apprendre(bs, ba, br + GAMMA * (1 - bfin) * q_suiv)
        if t % CIBLE_TOUS == 0:
            cible.copie_de(net)
        if t % VALID_TOUS == 0:
            (rv, vv, dv), _ = evaluer(politique_de(net), graines=range(500, 503), randomiser=True, series=series)
            courbe.append((t, rv))
            if rv > meilleur:
                meilleur, meilleurs_poids = rv, {k: v.copy() for k, v in net.p.items()}
    net.p = meilleurs_poids
    return net, courbe


def compter_changements(politique, series, graines=range(5)):
    n = 0
    for g in graines:
        env = Jumeau(series=series, randomiser=False, graine=1000 + g)
        obs, fin = env.reset(), False
        while not fin:
            avant = dict(env.leviers)
            obs, _, fin, info = env.step(politique(obs, env))
            n += sum(avant[k] != info['leviers'][k] for k in avant)
    return n / len(graines)


def main():
    n_graines = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    etapes = int(sys.argv[2]) if len(sys.argv) > 2 else 150_000
    series = charger()
    resultats = []
    for g in range(n_graines):
        t0 = time.time()
        net, courbe = entrainer(g, etapes, series)
        (r, v, d), _ = evaluer(politique_de(net), randomiser=False, series=series)
        (rr, vr, dr), _ = evaluer(politique_de(net), randomiser=True, series=series)
        ch = compter_changements(politique_de(net), series)
        resultats.append((r, v, d, rr, vr, ch))
        np.savez(f'dqn_graine{g}.npz', **net.p)
        print(f'graine {g} : récompense test {r:6.0f} (rand. {rr:5.0f})  violation {v:5.1f} %  secours {d:5.1f} %  '
              f'changements/épisode {ch:4.1f}   [{time.time() - t0:.0f} s]   validation : '
              + ' '.join(f'{x:.0f}' for _, x in courbe), flush=True)
    a = np.array(resultats)
    print('\n=== Test sur les mêmes 5 épisodes que les références (10 min chacun) ===')
    print(f'{"politique":<20}{"récompense":>12}{"violation %":>13}{"secours %":>11}{"changements":>13}')
    for nom, pol in (('ne rien faire', politique_rien), ('règle manuelle', RegleManuelle()),
                     ('oracle (triche)', politique_oracle)):
        (r, v, d), _ = evaluer(pol, series=series)
        print(f'{nom:<20}{r:>12.0f}{v:>13.1f}{d:>11.1f}{compter_changements(pol, series):>13.1f}')
    m, s = a.mean(0), a.std(0)
    print(f'{"DQN (5 graines)":<20}{m[0]:>7.0f} ± {s[0]:<3.0f}{m[1]:>8.1f} ± {s[1]:<3.1f}{m[2]:>7.1f}{m[5]:>13.1f}')
    print(f'DQN avec jumeau randomisé ±30 % : récompense {m[3]:.0f} ± {s[3]:.0f}, violation {m[4]:.1f} %')


if __name__ == '__main__':
    main()
