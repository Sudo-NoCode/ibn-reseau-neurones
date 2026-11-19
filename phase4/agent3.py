import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'phase2'))
import dqn
from dqn import BATCH, CIBLE_TOUS, DEBUT, EPS_DEBUT, EPS_DUREE, EPS_FIN, GAMMA, MEMOIRE, VALID_TOUS, Memoire, Reseau

from env3 import ACTIONS, AGENTS, DIM_OBS, INTENTION_DEFAUT, Env3, Regle, evaluer, masque_obs, obs_pour, politique_rien
PROGRESSIF = 0.5

dqn.CACHE = 128
N_ACTIONS = len(ACTIONS)


def choisir(net, obs, eps, rng):
    permis = masque_obs(obs)
    if rng.random() < eps:
        return int(rng.choice(np.flatnonzero(permis)))
    q = net.avant(obs[None])[0]
    return int(np.argmax(np.where(permis, q, -1e9)))


def entrainer(graine, etapes):
    rng = np.random.default_rng(graine)
    env = Env3(randomiser=True, graine=graine, progressif=PROGRESSIF)
    net, cible = Reseau(DIM_OBS, N_ACTIONS, rng), Reseau(DIM_OBS, N_ACTIONS, rng)
    cible.copie_de(net)
    mem = Memoire(MEMOIRE, DIM_OBS)
    obs = env.reset()
    meilleur, meilleurs_poids, courbe = -1e9, None, []
    for t in range(1, etapes + 1):
        eps = max(EPS_FIN, EPS_DEBUT - (EPS_DEBUT - EPS_FIN) * t / EPS_DUREE)

        actions = {s: choisir(net, obs[s], eps, rng) for s in AGENTS}
        obs2, rec, fin, _ = env.step(actions)
        for s in AGENTS:
            mem.ajouter(obs[s], actions[s], rec[s], obs2[s], fin)
        obs = env.reset() if fin else obs2

        if t >= DEBUT:
            for _ in AGENTS:
                bs, ba, br, bs2, bfin = mem.tirer(BATCH, rng)
                q2 = np.where(masque_obs(bs2), net.avant(bs2), -1e9)
                a_suiv = np.argmax(q2, 1)
                q_suiv = cible.avant(bs2)[np.arange(BATCH), a_suiv]
                net.apprendre(bs, ba, br + GAMMA * (1 - bfin) * q_suiv)

        if t % CIBLE_TOUS == 0:
            cible.copie_de(net)
        if t % VALID_TOUS == 0:
            rv = evaluer(politique_de(net), graines=range(500, 503), randomiser=True, progressif=PROGRESSIF)[:, 0].mean()
            courbe.append(rv)
            if rv > meilleur:
                meilleur, meilleurs_poids = rv, {k: v.copy() for k, v in net.p.items()}
    net.p = meilleurs_poids
    return net, courbe


def politique_de(net):
    def pol(obs, env, s):
        q = net.avant(obs_pour(net.p['W1'].shape[0], obs)[None])[0]
        return int(np.argmax(np.where(masque_obs(obs), q, -1e9)))
    return pol


def verifier(morceau):
    rng = np.random.default_rng(0)
    env = Env3(graine=5, intentions={'visio': dict(interdits={'priorite', 'chemin1', 'chemin3'}), 'voix': dict(interdits={'brider'})})
    obs = env.reset()
    net = Reseau(DIM_OBS, N_ACTIONS, rng)
    if morceau == 'A':
        o = obs['visio']
        permis = masque_obs(o)
        q = net.avant(o[None])[0]
        tirages = [choisir(net, o, 1.0, rng) for _ in range(2000)]
        assert all(isinstance(a, (int, np.integer)) for a in tirages), 'choisir() doit renvoyer un entier'
        assert all(permis[a] for a in tirages), f'exploration : action interdite tirée ({ACTIONS[next(a for a in tirages if not permis[a])]})'
        assert set(tirages) == set(np.flatnonzero(permis)), 'exploration : toutes les actions permises doivent pouvoir sortir'
        a0 = choisir(net, o, 0.0, rng)
        attendu = int(np.argmax(np.where(permis, q, -1e9)))
        assert a0 == attendu, f'exploitation : tu renvoies « {ACTIONS[a0]} », la meilleure action permise est « {ACTIONS[attendu]} »'
        interdit = int(np.flatnonzero(~permis)[0])
        net.p['b3'][interdit] += 100
        assert permis[choisir(net, o, 0.0, rng)], 'exploitation : tu as choisi une action interdite (masque oublié ?)'
        print('morceau A : OK — exploration et exploitation respectent le masque.')
    elif morceau in ('B', 'C'):
        t0 = time.time()
        try:
            net, courbe = entrainer(0, 3000 if morceau == 'B' else 12000)
        except NotImplementedError as e:
            if morceau == 'B' and 'morceau C' in str(e):
                print('morceau B : OK — la boucle joue et range ses souvenirs jusqu\'au début de l\'apprentissage.')
                return
            raise
        print(f'morceau C : OK — 12 000 secondes apprises en {time.time() - t0:.0f} s, validation {courbe[0]:.0f} points '
              f'(sur ces mêmes épisodes : ne rien faire 239, règle manuelle 553).')


def main():
    if len(sys.argv) < 2:
        print("usage : voir README.md"); return
    if sys.argv[1] == 'verifier':
        verifier(sys.argv[2].upper()); return
    if sys.argv[1] == 'entrainer':
        g = int(sys.argv[2]); etapes = int(sys.argv[3]) if len(sys.argv) > 3 else 150_000
        t0 = time.time()
        net, courbe = entrainer(g, etapes)
        np.savez(f'agent3_graine{g}.npz', **net.p)
        print(f'graine {g} [{time.time() - t0:.0f} s]  validation : ' + ' '.join(f'{x:.0f}' for x in courbe), flush=True)
        return
    pols = [('ne rien faire', politique_rien), ('règle manuelle', Regle())]
    for f in sorted(Path('.').glob('agent3_graine*.npz')):
        net = Reseau(DIM_OBS, N_ACTIONS, np.random.default_rng(0)); net.p = dict(np.load(f))
        pols.append((f.stem, politique_de(net)))
    for titre, intentions in (('contrat par défaut', INTENTION_DEFAUT), ('intentions au hasard', None)):
        print(f'\n=== {titre} : 5 épisodes de 10 min ===')
        print(f'{"politique":<22}{"récompense":>12}{"visio hors contrat":>22}{"voix hors contrat":>20}{"changements":>14}')
        for nom, pol in pols:
            m = evaluer(pol, intentions=intentions).mean(0)
            print(f'{nom:<22}{m[0]:>12.0f}{m[1]:>20.1f} %{m[2]:>18.1f} %{m[3]:>14.0f}')


if __name__ == '__main__':
    main()
