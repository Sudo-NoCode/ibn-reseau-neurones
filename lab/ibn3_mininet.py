import csv
import os
import re
import sys

from env3 import ACTIONS, AGENTS, GENEUR_DE
from ibn3 import DEMO, DEMO_PROGRESSIF, DUREE_DEMO, AgentEnsemble, Journal, accepter, boucle
from reseau3 import Reseau3
from valide3 import EnvReel
from valide_mininet import GardeGel

GEL_MS = 20


def main():
    from mininet.log import setLogLevel
    setLogLevel('warning')
    texte = next((x for x in sys.argv[1:] if not x.startswith('--')), 'visio excellente ; voix correcte, sans passer par c2')
    agent = AgentEnsemble('.')
    contrats, cout = accepter(texte, agent, confirmer=None if '--oui' in sys.argv else (lambda q: input(q).strip().lower().startswith('o')))
    if contrats is None:
        print('\nIntention ' + cout)
        return
    r, garde = Reseau3(), GardeGel()
    os.makedirs('valid', exist_ok=True)
    n = 1 + max([int(x) for f in os.listdir('valid') for x in re.findall(r'^ibn3_(\d+)\.csv$', f)] or [0])
    f = open(f'valid/ibn3_{n}.csv', 'w', newline='')
    colonnes = ['t', 'situation', 'gel_ms'] + [f'{s}_{k}' for s in AGENTS for k in ('action', 'chemin', 'prio', 'bride', 'ok', 'lat', 'pertes', 'gigue', 'debit')]
    w = csv.DictWriter(f, fieldnames=colonnes)
    w.writeheader()
    geles = [0]

    def noter(env, actions, rec, info):
        gel = garde.seconde(r.T)
        geles[0] += gel > GEL_MS
        ligne = dict(t=env.t, situation=info['situation'].split('|')[0], gel_ms=round(gel))
        for s in AGENTS:
            m = info['mesure'][s]
            ligne.update({f'{s}_action': ACTIONS[actions[s]] if info['changes'][s] else '', f'{s}_chemin': info['chemin'][s],
                          f'{s}_prio': int(info['prio'][s]), f'{s}_bride': int(info['limite'][GENEUR_DE[s]]), f'{s}_ok': int(info['ok'][s]),
                          f'{s}_lat': round(m['lat'], 1), f'{s}_pertes': round(m['pertes'], 2), f'{s}_gigue': round(m['gigue'], 2),
                          f'{s}_debit': round(m['debit'], 3)})
        w.writerow(ligne)

    print(f'\n--- boucle IBN sur Mininet : {DUREE_DEMO} s de pannes réelles ---', flush=True)
    try:
        env = EnvReel(r, duree=DUREE_DEMO, randomiser=False, graine=7, intentions=contrats)
        prog = DEMO_PROGRESSIF if '--progressif' in sys.argv else DEMO
        journal = boucle(env, agent, Journal(contrats), programme=prog, chaque_seconde=noter)
    finally:
        garde.stop.set(); f.close()
        r.arreter()
    rap = journal.rapport(texte, cout)
    rap += f'\n\nQualité de mesure : {100 * geles[0] / DUREE_DEMO:.1f} % de secondes perturbées par un gel de la VM'
    print('\n'.join(journal.lignes))
    print('\n' + rap)
    open(f'valid/ibn3_{n}_rapport.txt', 'w', encoding='utf-8').write(rap + '\n\nJOURNAL\n' + '\n'.join(journal.lignes) + '\n')
    print(f'écrit : valid/ibn3_{n}.csv, valid/ibn3_{n}_rapport.txt\nFIN', flush=True)


if __name__ == '__main__':
    main()
