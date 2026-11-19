import collections
import subprocess
import sys
import time

from mininet.log import setLogLevel

from scenarios import Moteur
from topo import GOULOT_IF, construire

DUREE = int(sys.argv[1]) if len(sys.argv) > 1 else 180
GRAINE = int(sys.argv[2]) if len(sys.argv) > 2 else 7
if '--combos' in sys.argv:
    Moteur.P_COMBO = 1.0

setLogLevel('warning')
net = construire()
net.start()
try:
    m = Moteur(net, graine=GRAINE, bw_mbps=20, duree_s=DUREE, duree_scenario=(15, 30))
    m.start()
    m.pret.wait(10)
    t0 = time.time()
    vus, secondaires, modes, pics, fond_max, transitions = collections.Counter(), set(), set(), 0, 0.0, set()
    zombies_max, dernier_pic = 0, 0
    while m.is_alive():
        e = m.etat()
        vus[e['scenario']] += 1
        if e['secondaire']:
            secondaires.add(f"{e['scenario']}+{e['secondaire']}")
        if e['cong_mode']:
            modes.add(e['cong_mode'])
        if e['pic'] and not dernier_pic:
            pics += 1
        dernier_pic = e['pic']
        fond_max = max(fond_max, e['fond_mbps'])
        transitions.add(e['transition'])
        z = subprocess.run(['ps', '-eo', 'stat,comm'], capture_output=True, text=True).stdout
        zombies_max = max(zombies_max, sum(1 for l in z.splitlines() if l.startswith('Z') and 'iperf3' in l))
        if int(time.time() - t0) % 15 == 0:
            print(f"t={time.time()-t0:5.0f}s  {e}")
        time.sleep(1)
    m.join(10)
    print('\n--- BILAN ---')
    print('secondes par scénario :', dict(vus))
    print('combinaisons vues     :', secondaires or 'aucune')
    print('modes de congestion   :', modes or 'aucun')
    print('pics bénins           :', pics)
    print('trafic de fond max    :', fond_max, 'Mbps')
    print('transitions           :', transitions)
    print('zombies iperf3 max    :', zombies_max)
finally:
    time.sleep(1)
    reste = subprocess.run(['pgrep', '-c', 'iperf3'], capture_output=True, text=True).stdout.strip()
    tc = subprocess.run(['tc', 'qdisc', 'show', 'dev', GOULOT_IF], capture_output=True, text=True).stdout
    print('iperf3 restants après nettoyage :', reste or '0')
    print('netem après nettoyage :', [l for l in tc.splitlines() if 'netem' in l])
    net.stop()
