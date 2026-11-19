import json

import numpy as np
import pandas as pd

DOSSIER = str(__import__("pathlib").Path(__file__).resolve().parent.parent / "data" / "processed")
CIBLE = "viol_futur_10"
CACHES = 8
TAUX = 0.5
EPOQUES = 4000

df = pd.read_csv(DOSSIER + "/dataset.csv")
entrees = json.load(open(DOSSIER + "/schema.json", encoding="utf-8"))["entrees"]
df = df[df[CIBLE].notna()]
train, val = df[df.split == "train"].copy(), df[df.split == "val"].copy()
pire = train[entrees].max()
for d in (train, val):
    d[entrees] = d[entrees].fillna(pire)
moy, ecart = train[entrees].mean(), train[entrees].std() + 1e-9
X_train = ((train[entrees] - moy) / ecart).to_numpy()
X_val = ((val[entrees] - moy) / ecart).to_numpy()
y_train, y_val = train[CIBLE].to_numpy(), val[CIBLE].to_numpy()


def sigmoide(z):
    return 1 / (1 + np.exp(-z))


def perte(p, y):
    return -np.mean(y * np.log(p + 1e-9) + (1 - y) * np.log(1 - p + 1e-9))


rng = np.random.default_rng(0)
n = X_train.shape[1]
W1 = rng.normal(0, np.sqrt(1 / n), (n, CACHES))
b1 = np.zeros(CACHES)
W2 = rng.normal(0, np.sqrt(1 / CACHES), CACHES)
b2 = 0.0


def avant(X):
    A1 = np.tanh(X @ W1 + b1)
    p = sigmoide(A1 @ W2 + b2)
    return A1, p


meilleure, sauvegarde = np.inf, None
for epoque in range(EPOQUES):
    A1, p = avant(X_train)

    d_sortie = (p - y_train) / len(y_train)
    gW2 = A1.T @ d_sortie
    gb2 = d_sortie.sum()
    d_cache = np.outer(d_sortie, W2) * (1 - A1 ** 2)
    gW1 = X_train.T @ d_cache
    gb1 = d_cache.sum(axis=0)

    W1 -= TAUX * gW1
    b1 -= TAUX * gb1
    W2 -= TAUX * gW2
    b2 -= TAUX * gb2

    pv = perte(avant(X_val)[1], y_val)
    if pv < meilleure:
        meilleure, sauvegarde, ep_best = pv, (W1.copy(), b1.copy(), W2.copy(), b2), epoque
    if epoque % 500 == 0:
        print(f"époque {epoque:4d}   perte train {perte(p, y_train):.4f}   perte val {pv:.4f}")

W1, b1, W2, b2 = sauvegarde
print(f"\nmeilleure perte val {meilleure:.4f} à l'époque {ep_best} (version gardée)")


def anticipation(d, pred):
    vus, avances, total = 0, [], 0
    for _, s in d.assign(pred=pred).groupby("session"):
        v, pr = s.violation.to_numpy(), s.pred.to_numpy()
        for i in range(10, len(v)):
            if v[i] == 1 and v[i - 1] == 0 and v[i - 10:i].sum() == 0:
                total += 1
                alertes = np.flatnonzero(pr[i - 10:i])
                if len(alertes):
                    vus += 1
                    avances.append(10 - alertes[0])
    return vus, total, (np.mean(avances) if avances else 0)


pred_mlp = (avant(X_val)[1] > 0.5).astype(int)
pred_pers = val["ref_persistance"].to_numpy()
sains = y_val == 0
print(f"\n{'':22}{'précision':>10}{'fausses alertes':>17}{'pannes vues venir':>20}{'avance moy.':>13}")
for nom, pr in [("MLP (8 cachés)", pred_mlp), ("règle persistance", pred_pers)]:
    vus, total, av = anticipation(val, pr)
    print(f"{nom:22}{(pr == y_val).mean():>10.1%}{pr[sains].mean():>17.1%}{f'{vus}/{total}':>20}{av:>11.1f} s")
