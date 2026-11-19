import sys

import pandas as pd

df = pd.read_csv(sys.argv[1] if len(sys.argv) > 1 else '/tmp/test_mesure.csv')
pd.set_option('display.width', 200)

print('secondes sans visio :', df.loc[df.visio_debit_mbps.isna(), 'sec'].tolist())
print('scénarios de ces secondes :', df.loc[df.visio_debit_mbps.isna(), 'scenario'].value_counts().to_dict())
print('secondes sans latence :', df.loc[df.lat_med_ms.isna(), 'sec'].tolist())
print()

sauts = df.index[(df.netem_delai.diff().abs() > 15) | (df.scenario != df.scenario.shift())].tolist()
cols = ['sec', 'scenario', 'secondaire', 'netem_delai', 'cong_mbps', 'lat_med_ms', 'lat_pertes_pct',
        'visio_pertes_pct', 'lien_occup_pct', 'file_paquets']
for i in sauts[1:]:
    print(df.loc[max(0, i - 2):i + 3, cols].to_string(index=False, header=(i == sauts[1])))
    print('-' * 40)
