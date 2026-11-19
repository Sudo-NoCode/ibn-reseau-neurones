import json

import numpy as np
import pandas as pd

DOSSIER = str(__import__("pathlib").Path(__file__).resolve().parent.parent / "data" / "processed")
CIBLE = "viol_futur_10"

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
print(f"train : {X_train.shape}   val : {X_val.shape}   positifs train : {y_train.mean():.1%}")


def sigmoide(z):
    return 1 / (1 + np.exp(-z))


def predire(X, w, b):
    return sigmoide(X @ w + b)


def perte(p, y):
    return -np.mean(y * np.log(p + 1e-9) + (1 - y) * np.log(1 - p + 1e-9))


w = np.zeros(X_train.shape[1])
b = 0.0
taux = 0.1

for epoque in range(300):
    p = predire(X_train, w, b)
    erreur = p - y_train
    grad_w = X_train.T @ erreur / len(y_train)
    grad_b = erreur.mean()
    w = w - taux * grad_w
    b = b - taux * grad_b

    if epoque % 50 == 0:
        print(f"époque {epoque:3d}   perte train {perte(p, y_train):.4f}   perte val {perte(predire(X_val, w, b), y_val):.4f}")

pred_val = (predire(X_val, w, b) > 0.5).astype(int)
print(f"\nprécision du neurone sur val      : {(pred_val == y_val).mean():.1%}")
print(f"précision de la règle persistance : {(val['ref_persistance'].to_numpy() == y_val).mean():.1%}")
print("\npoids appris (quelle mesure compte le plus) :")
for nom, poids in sorted(zip(entrees, w), key=lambda t: -abs(t[1])):
    print(f"  {nom:<18} {poids:+.3f}")
