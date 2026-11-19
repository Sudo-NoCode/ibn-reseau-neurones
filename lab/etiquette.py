import json
import os
import sys

import numpy as np
import pandas as pd

ICI = os.path.dirname(os.path.abspath(__file__))
RAW = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ICI, 'data', 'raw')
SORTIE = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ICI, 'data', 'processed')

INTENTION = {
    'lat_med_ms': ('<=', 60.0),
    'visio_pertes_pct': ('<=', 2.0),
    'visio_gigue_ms': ('<=', 15.0),
    'visio_debit_mbps': ('>=', 3.6),
}
ALERTE = {
    'lat_med_ms': ('<=', 48.0),
    'visio_pertes_pct': ('<=', 1.6),
    'visio_gigue_ms': ('<=', 12.0),
    'visio_debit_mbps': ('>=', 3.68),
}
HORIZONS = (5, 10, 30)
DELAI_MAX = 60

SPLIT = {**{f's{i:02d}': 'train' for i in range(1, 15)},
         **{f's{i:02d}': 'val' for i in (15, 16)},
         **{f's{i:02d}': 'test' for i in range(17, 21)},
         **{f'p{i:02d}': 'train' for i in range(1, 21)},
         **{f'p{i:02d}': 'val' for i in range(21, 24)},
         **{f'p{i:02d}': 'test' for i in range(24, 29)}}

ENTREES = ['lat_moy_ms', 'lat_med_ms', 'lat_max_ms', 'lat_pertes_pct',
           'visio_debit_mbps', 'visio_gigue_ms', 'visio_pertes_pct',
           'lien_occup_pct', 'file_paquets', 'file_jetes']
CONTEXTE = ['bw_mbps']
VERITE = ['scenario', 'secondaire', 'pic', 'transition', 'sc_age', 'netem_delai', 'netem_gigue',
          'netem_pertes', 'cong_mbps', 'cong_mode', 'fond_mbps']


def violation(df):
    v = pd.Series(False, index=df.index)
    for col, (op, seuil) in INTENTION.items():
        x = df[col]
        v |= x.isna() | (x > seuil if op == '<=' else x < seuil)
    return v.astype(int)


def proche_seuil(df):
    v = pd.Series(False, index=df.index)
    for col, (op, seuil) in ALERTE.items():
        x = df[col]
        v |= x.isna() | (x > seuil if op == '<=' else x < seuil)
    return v.astype(int)


def futur(viol, h):
    v = viol.to_numpy()
    n = len(v)
    out = np.full(n, np.nan)
    cum = np.concatenate([[0], np.cumsum(v)])
    for t in range(n - h):
        out[t] = 1.0 if cum[t + h + 1] - cum[t + 1] > 0 else 0.0
    return out


def delai_avant(viol):
    v = viol.to_numpy()
    debuts = np.flatnonzero((v == 1) & (np.concatenate([[0], v[:-1]]) == 0))
    out = np.full(len(v), np.nan)
    j = 0
    for t in range(len(v)):
        while j < len(debuts) and debuts[j] <= t:
            j += 1
        if j < len(debuts) and debuts[j] - t <= DELAI_MAX:
            out[t] = debuts[j] - t
    return out


def etiqueter_session(df):
    df = df.sort_values('sec').reset_index(drop=True)
    trous = int(df.sec.max() + 1 - len(df))
    if trous:
        print(f'  attention : {trous} seconde(s) manquante(s) dans {df.session.iloc[0]}')
    df['violation'] = violation(df)
    for h in HORIZONS:
        df[f'viol_futur_{h}'] = futur(df['violation'], h)
    df['delai_avant_viol'] = delai_avant(df['violation'])
    df['classe'] = df['scenario']
    df['ref_persistance'] = df['violation']
    df['ref_seuil80'] = proche_seuil(df)
    sid = df.session.iloc[0]
    df['split'] = SPLIT.get(sid, 'generalisation' if str(sid).startswith('g') else 'autre')
    return df


def rapport(df):
    print(f'\n{len(df)} lignes, {df.session.nunique()} sessions')
    print('\nlignes par split :', df.split.value_counts().to_dict())
    print('\nclasses par split (secondes) :')
    print(pd.crosstab(df.split, df.classe).to_string())
    print('\ntaux de violation par scénario (%) — normal doit être ~0, congestion ~100 :')
    print((df.groupby('classe').violation.mean() * 100).round(1).to_string())
    pics = df[df.pic == 1]
    if len(pics):
        print(f'\npics bénins : {len(pics)} s, dont {100 * pics.violation.mean():.1f} % en violation (doit rester faible)')
    for h in HORIZONS:
        col = f'viol_futur_{h}'
        print(f'{col:>14} : {100 * df[col].mean():5.1f} % de positifs   ({df[col].isna().sum()} lignes de fin de session sans étiquette)')
    print('\nvaleurs vides dans les entrées (%) :')
    print((df[ENTREES].isna().mean() * 100).round(1).to_string())


def main():
    fichiers = sorted(f for f in os.listdir(RAW) if f.endswith('.csv'))
    if not fichiers:
        sys.exit(f'aucun CSV dans {RAW}')
    morceaux = [etiqueter_session(pd.read_csv(os.path.join(RAW, f))) for f in fichiers]
    df = pd.concat(morceaux, ignore_index=True)
    os.makedirs(SORTIE, exist_ok=True)
    df.to_csv(os.path.join(SORTIE, 'dataset.csv'), index=False)
    schema = dict(
        entrees=ENTREES, contexte=CONTEXTE,
        etiquettes=['violation'] + [f'viol_futur_{h}' for h in HORIZONS] + ['delai_avant_viol', 'classe'],
        references=['ref_persistance', 'ref_seuil80'],
        verite_terrain_interdite_en_entree=VERITE,
        intention={k: f'{op} {s}' for k, (op, s) in INTENTION.items()},
        split=SPLIT,
    )
    with open(os.path.join(SORTIE, 'schema.json'), 'w') as f:
        json.dump(schema, f, indent=2, ensure_ascii=False)
    rapport(df)
    print(f'\nécrit : {SORTIE}/dataset.csv et schema.json')


if __name__ == '__main__':
    main()
