from collections import deque
from pathlib import Path

import numpy as np
import pandas as pd

ICI = Path(__file__).resolve().parent
DONNEES = ICI.parent / 'data' / 'processed'
POIDS = ICI / 'detecteur.npz'
W, CACHES, TAUX = 10, 16, 0.3
ENTREES = ['lat_moy_ms', 'lat_med_ms', 'lat_max_ms', 'lat_pertes_pct', 'visio_debit_mbps', 'visio_gigue_ms',
           'visio_pertes_pct', 'lien_occup_pct', 'file_paquets', 'file_jetes']
T_C = np.arange(W) - (W - 1) / 2
LAT = ('lat_moy_ms', 'lat_med_ms', 'lat_max_ms')


def _entrainer(Xt, yt, Xe, ye, K, epoques, l2):
    rng = np.random.default_rng(0)
    n = Xt.shape[1]
    W1, b1 = rng.normal(0, np.sqrt(1 / n), (n, CACHES)), np.zeros(CACHES)
    W2, b2 = rng.normal(0, np.sqrt(1 / CACHES), (CACHES, K)), np.zeros(K)
    Y = yt[:, None].astype(float) if K == 1 else np.eye(K)[yt]

    def avant(X, W1, b1, W2, b2):
        A = np.tanh(X @ W1 + b1)
        Z = A @ W2 + b2
        if K == 1:
            return A, 1 / (1 + np.exp(-Z))
        E = np.exp(Z - Z.max(1, keepdims=True))
        return A, E / E.sum(1, keepdims=True)

    def perte(P, y):
        if K == 1:
            return -np.mean(y * np.log(P[:, 0] + 1e-9) + (1 - y) * np.log(1 - P[:, 0] + 1e-9))
        return -np.mean(np.log(P[np.arange(len(y)), y] + 1e-9))

    meilleure, garde = np.inf, None
    for _ in range(epoques):
        A1, P = avant(Xt, W1, b1, W2, b2)
        D = (P - Y) / len(yt)
        Dc = (D @ W2.T) * (1 - A1 ** 2)
        W2, b2 = W2 - TAUX * (A1.T @ D + l2 * W2), b2 - TAUX * D.sum(0)
        W1, b1 = W1 - TAUX * (Xt.T @ Dc + l2 * W1), b1 - TAUX * Dc.sum(0)
        pe = perte(avant(Xe, W1, b1, W2, b2)[1], ye)
        if pe < meilleure:
            meilleure, garde = pe, (W1.copy(), b1.copy(), W2.copy(), b2.copy())
    return garde


def exporter():
    from numpy.lib.stride_tricks import sliding_window_view as swv
    brut = pd.read_csv(DONNEES / 'dataset.csv').sort_values(['session', 'sec'])
    p = {}

    d = brut.copy()
    tr = d.split == 'train'
    rempl = d.loc[tr, ENTREES].max()
    d[ENTREES] = d[ENTREES].fillna(rempl)
    moy, ec = d.loc[tr, ENTREES].mean(), d.loc[tr, ENTREES].std() + 1e-9
    d[ENTREES] = (d[ENTREES] - moy) / ec

    def fen(split):
        Xs, ys = [], []
        for _, s in d[d.split == split].groupby('session'):
            M = s[ENTREES].to_numpy()
            X = swv(M, (W, M.shape[1]))[:, 0].reshape(len(M) - W + 1, -1)
            fin = s.iloc[W - 1:]
            g = fin.viol_futur_10.notna().to_numpy()
            Xs.append(X[g]); ys.append(fin.viol_futur_10.to_numpy()[g])
        return np.vstack(Xs), np.concatenate(ys)

    (Xt, yt), (Xv, yv) = fen('train'), fen('val')
    p['b_W1'], p['b_b1'], p['b_W2'], p['b_b2'] = _entrainer(Xt, yt, Xv, yv, 1, 3000, 0.0)
    p['b_rempl'], p['b_moy'], p['b_ec'] = rempl.to_numpy(), moy.to_numpy(), ec.to_numpy()

    d = brut.copy()
    for c in LAT:
        d[c] = d[c].fillna(1500.0)
    d['classe4'] = d.classe.replace({'degrade': 'lien', 'panne': 'lien'})
    classes = sorted(d.classe4.unique())

    def resumes(split):
        Xs, ys = [], []
        for _, s in d[d.split == split].groupby('session'):
            F = swv(s[ENTREES].to_numpy(), (W, len(ENTREES)))[:, 0]
            pente = (F * T_C[None, :, None]).sum(1) / (T_C ** 2).sum()
            Xs.append(np.hstack([F.mean(1), F.std(1), pente, F.max(1)]))
            ys.append(s.classe4.map(classes.index).to_numpy()[W - 1:])
        return np.vstack(Xs), np.concatenate(ys)

    (Xt, yt), (Xv, yv) = resumes('train'), resumes('val')
    moy, ec = Xt.mean(0), Xt.std(0) + 1e-9
    p['a_W1'], p['a_b1'], p['a_W2'], p['a_b2'] = _entrainer((Xt - moy) / ec, yt, (Xv - moy) / ec, yv, len(classes), 1500, 1e-3)
    p['a_moy'], p['a_ec'], p['a_classes'] = moy, ec, np.array(classes)
    np.savez(POIDS, **p)
    print(f'poids exportés : {POIDS.name}  (classes {classes})')


class Detecteur:
    def __init__(self, chemin=POIDS):
        self.p = dict(np.load(chemin))
        self.classes = [str(c) for c in self.p['a_classes']]
        self.reset()

    def reset(self):
        self.fen_b, self.fen_a, self.oui = deque(maxlen=W), deque(maxlen=W), deque(maxlen=3)

    def ajouter(self, mesures):
        p = self.p
        x = np.array([np.nan if mesures.get(k) in (None, '') else float(mesures[k]) for k in ENTREES])
        xb = np.where(np.isnan(x), p['b_rempl'], x)
        self.fen_b.append((xb - p['b_moy']) / p['b_ec'])
        xa = x.copy()
        for i, k in enumerate(ENTREES):
            if k in LAT and np.isnan(xa[i]):
                xa[i] = 1500.0
        self.fen_a.append(xa)
        if len(self.fen_b) < W:
            return None
        X = np.concatenate(self.fen_b)[None]
        pf = float(1 / (1 + np.exp(-(np.tanh(X @ p['b_W1'] + p['b_b1']) @ p['b_W2'] + p['b_b2'])))[0, 0])
        self.oui.append(pf > 0.5)
        alerte = len(self.oui) == 3 and all(self.oui)
        F = np.array(self.fen_a)
        res = np.hstack([F.mean(0), F.std(0), (F * T_C[:, None]).sum(0) / (T_C ** 2).sum(), F.max(0)])
        Z = np.tanh(((res - p['a_moy']) / p['a_ec'])[None] @ p['a_W1'] + p['a_b1']) @ p['a_W2'] + p['a_b2']
        E = np.exp(Z - Z.max())
        pc = (E / E.sum())[0]
        return dict(p_futur=pf, alerte=alerte, classe='inconnu' if np.isnan(pc).any() else self.classes[int(np.argmax(pc))],
                    p_classes={c: float(v) for c, v in zip(self.classes, pc)})


def verifier():
    brut = pd.read_csv(DONNEES / 'dataset.csv').sort_values(['session', 'sec'])
    brut['classe4'] = brut.classe.replace({'degrade': 'lien', 'panne': 'lien'})
    det = Detecteur()
    jeux = {'test': brut.split == 'test', 'généralisation 10 Mbps': brut.session.str.startswith('g10'),
            'généralisation 50 Mbps': brut.session.str.startswith('g50')}
    for nom, masque in jeux.items():
        ok_b = n_b = ok_a = n_a = 0
        for _, s in brut[masque].groupby('session'):
            det.reset()
            for r in s.to_dict('records'):
                out = det.ajouter({k: (None if pd.isna(r[k]) else r[k]) for k in ENTREES})
                if out is None:
                    continue
                ok_a += out['classe'] == r['classe4']; n_a += 1
                if not pd.isna(r['viol_futur_10']):
                    ok_b += out['alerte'] == bool(r['viol_futur_10']); n_b += 1
        print(f'{nom:<24} prévision 10 s : {100 * ok_b / n_b:5.1f} %    classe du problème : {100 * ok_a / n_a:5.1f} %')
    print('(phase 1 : test 93,2 % / 86,2 % ; 10 Mbps 89,0 % / 78,8 % ; 50 Mbps 92,6 % / 90,6 %)')


if __name__ == '__main__':
    if not POIDS.exists():
        exporter()
    verifier()
