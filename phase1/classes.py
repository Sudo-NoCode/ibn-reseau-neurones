import json

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

DOSSIER = str(__import__("pathlib").Path(__file__).resolve().parent.parent / "data" / "processed")
W = 10
CACHES = 16
TAUX = 0.3
EPOQUES = 3000

df = pd.read_csv(DOSSIER + "/dataset.csv").sort_values(["session", "sec"])
entrees = json.load(open(DOSSIER + "/schema.json", encoding="utf-8"))["entrees"]
CLASSES = sorted(df.classe.unique())
est_train = df.split == "train"
df[entrees] = df[entrees].fillna(df.loc[est_train, entrees].max())
moy, ecart = df.loc[est_train, entrees].mean(), df.loc[est_train, entrees].std() + 1e-9
df[entrees] = (df[entrees] - moy) / ecart


def fenetres(split):
    Xs, ys, infos = [], [], []
    for _, s in df[df.split == split].groupby("session"):
        M = s[entrees].to_numpy()
        Xs.append(sliding_window_view(M, (W, M.shape[1]))[:, 0].reshape(len(M) - W + 1, -1))
        fin = s.iloc[W - 1:]
        ys.append(fin.classe.map(CLASSES.index).to_numpy())
        infos.append(fin[["session", "sec", "classe", "sc_age"]])
    return np.vstack(Xs), np.concatenate(ys), pd.concat(infos)


def softmax(Z):
    E = np.exp(Z - Z.max(axis=1, keepdims=True))
    return E / E.sum(axis=1, keepdims=True)


def perte(P, y):
    return -np.mean(np.log(P[np.arange(len(y)), y] + 1e-9))


Xt, yt, it = fenetres("train")
Xv, yv, iv = fenetres("val")
K = len(CLASSES)
Yt = np.eye(K)[yt]

rng = np.random.default_rng(0)
n = Xt.shape[1]
W1, b1 = rng.normal(0, np.sqrt(1 / n), (n, CACHES)), np.zeros(CACHES)
W2, b2 = rng.normal(0, np.sqrt(1 / CACHES), (CACHES, K)), np.zeros(K)


def avant(X, W1, b1, W2, b2):
    A1 = np.tanh(X @ W1 + b1)
    return A1, softmax(A1 @ W2 + b2)


meilleure = np.inf
for ep in range(EPOQUES):
    A1, P = avant(Xt, W1, b1, W2, b2)
    D = (P - Yt) / len(yt)
    Dc = (D @ W2.T) * (1 - A1 ** 2)
    W2, b2 = W2 - TAUX * A1.T @ D, b2 - TAUX * D.sum(axis=0)
    W1, b1 = W1 - TAUX * Xt.T @ Dc, b1 - TAUX * Dc.sum(axis=0)
    pv = perte(avant(Xv, W1, b1, W2, b2)[1], yv)
    if pv < meilleure:
        meilleure, garde, ep_best = pv, (W1.copy(), b1.copy(), W2.copy(), b2.copy()), ep
    if ep % 500 == 0:
        print(f"époque {ep:4d}   perte train {perte(P, yt):.4f}   perte val {pv:.4f}")
print(f"version gardée : époque {ep_best}, perte val {meilleure:.4f}\n")

pred = avant(Xv, *garde)[1].argmax(axis=1)

print(f"précision globale            : {(pred == yv).mean():.1%}")
print(f"référence « toujours normal » : {(yv == CLASSES.index('normal')).mean():.1%}\n")

print("matrice de confusion (lignes = vraie classe, colonnes = prédite) :")
mc = pd.crosstab(pd.Series([CLASSES[i] for i in yv], name="vraie"), pd.Series([CLASSES[i] for i in pred], name="prédite"))
print(mc.to_string(), "\n")

print(f"{'classe':<12}{'précision':>10}{'rappel':>9}{'F1':>7}{'diagnostic (médiane)':>23}")
f1s = []
for k, c in enumerate(CLASSES):
    vp = ((pred == k) & (yv == k)).sum()
    prec = vp / max(1, (pred == k).sum())
    rap = vp / max(1, (yv == k).sum())
    f1 = 2 * prec * rap / max(1e-9, prec + rap)
    f1s.append(f1)
    delais = []
    if c != "normal":
        for _, s in iv.assign(ok=(pred == yv)).groupby("session"):
            s = s[s.classe == c]
            debuts = s.index[(s.sc_age < 1)]
            for d0 in debuts:
                ep = s.loc[d0:].iloc[:200]
                ep = ep[ep.sc_age >= ep.sc_age.iloc[0]]
                bons = ep[ep.ok]
                if len(bons):
                    delais.append(bons.sc_age.iloc[0])
    diag = f"{np.median(delais):.0f} s ({len(delais)} épisodes)" if delais else "-"
    print(f"{c:<12}{prec:>10.1%}{rap:>9.1%}{f1:>7.2f}{diag:>23}")
print(f"\nF1 moyen (macro) : {np.mean(f1s):.2f}")
