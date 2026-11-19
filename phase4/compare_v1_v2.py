import numpy as np

from env3 import INTENTION_DEFAUT, Regle, evaluer
from ibn3 import AgentEnsemble

agents = [('règle manuelle', Regle()), ('agent v1', AgentEnsemble('v1')), ('agent v2', AgentEnsemble('v2'))]
for titre, p in (('pannes brusques (comme avant)', 0.0), ('une panne sur deux progressive', 0.5), ('toutes les pannes progressives', 1.0)):
    print(f'\n=== {titre} : 10 épisodes de 10 min, contrat par défaut ===')
    print(f'{"politique":<18}{"récompense":>12}{"visio hors contrat":>21}{"voix hors contrat":>20}{"changements":>14}')
    for nom, pol in agents:
        m = evaluer(pol, graines=range(10), intentions=INTENTION_DEFAUT, progressif=p).mean(0)
        print(f'{nom:<18}{m[0]:>12.0f}{m[1]:>19.2f} %{m[2]:>18.2f} %{m[3]:>14.0f}', flush=True)
