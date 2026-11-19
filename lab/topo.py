import json
import re
import sys
import time

from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import OVSBridge

GOULOT_MBPS = 20
GOULOT_DELAI = '5ms'
GOULOT_IF = 's1-eth3'


def construire(bw=GOULOT_MBPS, delai=GOULOT_DELAI):
    net = Mininet(switch=OVSBridge, link=TCLink, controller=None, autoSetMacs=True, autoStaticArp=True)
    h1 = net.addHost('h1', ip='10.0.0.1/24')
    h2 = net.addHost('h2', ip='10.0.0.2/24')
    h3 = net.addHost('h3', ip='10.0.0.3/24')
    h4 = net.addHost('h4', ip='10.0.0.4/24')
    s1 = net.addSwitch('s1', failMode='standalone')
    s2 = net.addSwitch('s2', failMode='standalone')
    net.addLink(h1, s1)
    net.addLink(h2, s1)
    net.addLink(h3, s2)
    net.addLink(h4, s2)
    net.addLink(s1, s2, bw=bw, delay=delai)
    return net


def tester(net):
    h1, h3 = net.get('h1', 'h3')
    s1 = net.get('s1')

    print('\n[1] Connectivité entre les 4 hôtes')
    perte = net.pingAll(timeout='1')

    print('\n[2] Latence h1 -> h3 (attendu : ~10 ms aller-retour)')
    sortie = h1.cmd('ping -c 10 -i 0.2 10.0.0.3')
    m = re.search(r'= [\d.]+/([\d.]+)/', sortie)
    rtt = float(m.group(1)) if m else None
    print(f'    RTT moyen : {rtt} ms')

    print(f'\n[3] Débit max h1 -> h3 (attendu : ~{GOULOT_MBPS} Mbps)')
    h3.cmd('iperf3 -s -1 > /dev/null 2>&1 &')
    time.sleep(0.5)
    res = json.loads(h1.cmd('iperf3 -c 10.0.0.3 -t 4 -J'))
    debit = res['end']['sum_received']['bits_per_second'] / 1e6
    print(f'    Débit mesuré : {debit:.1f} Mbps')

    print(f'\n[4] File d\'attente sur {GOULOT_IF} (où agiront les pannes)')
    print(s1.cmd(f'tc qdisc show dev {GOULOT_IF}'))

    ok = perte == 0 and rtt is not None and 8 <= rtt <= 15 and debit >= GOULOT_MBPS * 0.8
    print('RÉSULTAT :', 'OK, le réseau est prêt' if ok else 'PROBLÈME, voir les valeurs ci-dessus')
    return ok


if __name__ == '__main__':
    setLogLevel('warning')
    net = construire()
    net.start()
    try:
        ok = tester(net)
        if '--cli' in sys.argv:
            CLI(net)
    finally:
        net.stop()
    sys.exit(0 if ok else 1)
