import json

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

DOSSIER = str(__import__("pathlib").Path(__file__).resolve().parent.parent / "data" / "processed")
CIBLE = "viol_futur_10"
FENETRES = [1, 10, 30]
CACHES = 16
TAUX = 0.3
EPOQUES = 3000

df = pd.read_csv(DOSSIER + "/dataset.csv").sort_values(["session", "sec"])
entrees = json.load(open(DOSSIER + "/schema.json", encoding="utf-8"))["entrees"]
est_train = df.split == "train"
pire = df.loc[est_train, entrees].max()
df[entrees] = df[entrees].fillna(pire)
moy, ecart = df.loc[est_train, entrees].mean(), df.loc[est_train, entrees].std() + 1e-9
df[entrees] = (df[entrees] - moy) / ecart


def fenetres(split, W):
    Xs, ys, infos = [], [], []
    for _, s in df[df.split == split].groupby("session"):
        M = s[entrees].to_numpy()
        if len(M) < W:
            continue
        X = sliding_window_view(M, (W, M.shape[1]))[:, 0].reshape(len(M) - W + 1, -1)
        fin = s.iloc[W - 1:]
        garde = fin[CIBLE].notna().to_numpy()
        Xs.append(X[garde])
        ys.append(fin[CIBLE].to_numpy()[garde])
        infos.append(fin[garde][["session", "sec", "violation", "ref_persistance"]])
    return np.vstack(Xs), np.concatenate(ys), pd.concat(infos)


def sigmoide(z):
    return 1 / (1 + np.exp(-z))


def perte(p, y):
    return -np.mean(y * np.log(p + 1e-9) + (1 - y) * np.log(1 - p + 1e-9))


def entrainer(Xt, yt, Xv, yv):
    rng = np.random.default_rng(0)
    n = Xt.shape[1]
    W1, b1 = rng.normal(0, np.sqrt(1 / n), (n, CACHES)), np.zeros(CACHES)
    W2, b2 = rng.normal(0, np.sqrt(1 / CACHES), CACHES), 0.0
    avant = lambda X, W1, b1, W2, b2: (lambda A1: (A1, sigmoide(A1 @ W2 + b2)))(np.tanh(X @ W1 + b1))
    meilleure, garde = np.inf, None
    for ep in range(EPOQUES):
        A1, p = avant(Xt, W1, b1, W2, b2)
        d = (p - yt) / len(yt)
        dc = np.outer(d, W2) * (1 - A1 ** 2)
        W2, b2 = W2 - TAUX * A1.T @ d, b2 - TAUX * d.sum()
        W1, b1 = W1 - TAUX * Xt.T @ dc, b1 - TAUX * dc.sum(axis=0)
        pv = perte(avant(Xv, W1, b1, W2, b2)[1], yv)
        if pv < meilleure:
            meilleure, garde, ep_best = pv, (W1.copy(), b1.copy(), W2.copy(), b2), ep
    return (lambda X: avant(X, *garde)[1]), meilleure, ep_best


def lisser(infos, pred, k):
    s = pd.Series(pred, index=infos.index)
    return s.groupby(infos.session.to_numpy()).transform(lambda x: x.rolling(k, min_periods=k).min().fillna(0)).to_numpy().astype(int)


def anticipation(infos, pred):
    vus, avances, total = 0, [], 0
    for _, s in infos.assign(pred=pred).groupby("session"):
        v, pr, sec = s.violation.to_numpy(), s.pred.to_numpy(), s.sec.to_numpy()
        for i in range(10, len(v)):
            if v[i] == 1 and v[i - 1] == 0 and v[i - 10:i].sum() == 0 and sec[i] - sec[i - 10] == 10:
                total += 1
                alertes = np.flatnonzero(pr[i - 10:i])
                if len(alertes):
                    vus += 1
                    avances.append(10 - alertes[0])
    return vus, total, (np.mean(avances) if avances else 0.0)


def ligne(nom, infos, y, pred):
    vus, total, av = anticipation(infos, pred)
    sains = y == 0
    print(f"{nom:28}{(pred == y).mean():>10.1%}{pred[sains].mean():>17.1%}{f'{vus}/{total}':>20}{av:>11.1f} s")


print(f"{'':28}{'précision':>10}{'fausses alertes':>17}{'pannes vues venir':>20}{'avance moy.':>13}")
for W in FENETRES:
    Xt, yt, _ = fenetres("train", W)
    Xv, yv, iv = fenetres("val", W)
    modele, pv, ep = entrainer(Xt, yt, Xv, yv)
    pred = (modele(Xv) > 0.5).astype(int)
    ligne(f"MLP fenêtre {W:>2} s ({Xt.shape[1]} entrées)", iv, yv, pred)
    ligne(f"   + 3 « oui » de suite", iv, yv, lisser(iv, pred, 3))
_, yv, iv = fenetres("val", 30)
ligne("règle persistance", iv, yv, iv.ref_persistance.to_numpy())
