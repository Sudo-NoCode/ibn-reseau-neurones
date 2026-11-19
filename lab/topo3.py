import itertools
import subprocess
import sys

from mininet.link import TCLink
from mininet.net import Mininet
from mininet.node import OVSSwitch

COEUR, DISTRIB, ACCES = ['c1', 'c2'], ['d1', 'd2'], ['a1', 'a2', 'a3', 'a4']
COMMUTATEURS = COEUR + DISTRIB + ACCES
HOTES = {'h1': 'a1', 'h2': 'a1', 'h3': 'a2', 'h4': 'a2',
         'h5': 'a3', 'h6': 'a3', 'h7': 'a4', 'h8': 'a4'}
CLIENTS, SERVEURS = ('a1', 'a2'), ('a3', 'a4')
LIENS = [('a1', 'd1'), ('a1', 'd2'), ('a2', 'd1'), ('a2', 'd2'),
         ('d1', 'c1'), ('d1', 'c2'), ('d2', 'c1'), ('d2', 'c2'),
         ('c1', 'a3'), ('c2', 'a3'), ('c1', 'a4'), ('c2', 'a4')]
LIEN_MBPS = 20
LIEN_DELAI = '1.5ms'


def sh(cmd, verifier=True):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if verifier and r.returncode != 0:
        raise RuntimeError(f'{cmd}\n{r.stderr}')
    return r.stdout


def ip(hote):
    return f'10.0.0.{int(hote[1:])}'


def voisins(sw):
    return [b if a == sw else a for a, b in LIENS if sw in (a, b)]


def chemins(src, dst):
    if src == dst:
        return [[src]]
    cote = lambda a: 'A' if a in CLIENTS else 'B'
    if cote(src) == cote(dst):
        return [[src, m, dst] for m in (DISTRIB if cote(src) == 'A' else COEUR)]
    if cote(src) == 'A':
        return [[src, d, c, dst] for d, c in itertools.product(DISTRIB, COEUR)]
    return [list(reversed(p)) for p in chemins(dst, src)]


def construire(bw=LIEN_MBPS):
    net = Mininet(switch=OVSSwitch, link=TCLink, controller=None, autoSetMacs=True, autoStaticArp=True)
    for i, sw in enumerate(COMMUTATEURS, 1):
        net.addSwitch(sw, dpid=f'{i:016x}', failMode='secure', protocols='OpenFlow13')
    for h, sw in HOTES.items():
        net.addLink(net.addHost(h, ip=ip(h) + '/24'), net.get(sw))
    for a, b in LIENS:
        net.addLink(net.get(a), net.get(b), bw=bw, delay=LIEN_DELAI)
    net.bw = bw
    return net


def demarrer(net):
    net.start()
    net.port, net.intf = {}, {}
    for l in net.links:
        n1, n2 = l.intf1.node, l.intf2.node
        if n1.name in COMMUTATEURS:
            net.port[n1.name, n2.name] = n1.ports[l.intf1]; net.intf[n1.name, n2.name] = l.intf1.name
        if n2.name in COMMUTATEURS:
            net.port[n2.name, n1.name] = n2.ports[l.intf2]; net.intf[n2.name, n1.name] = l.intf2.name
    _routes_normales(net)


def _flux(sw, regle):
    sh(f'ovs-ofctl -O OpenFlow13 add-flow {sw} "{regle}"')


def _routes_normales(net):
    for sw in COMMUTATEURS:
        sh(f'ovs-ofctl -O OpenFlow13 del-flows {sw}')
    for h, acces in HOTES.items():
        dist = {acces: 0}
        file = [acces]
        while file:
            x = file.pop(0)
            for v in voisins(x):
                if v not in dist:
                    dist[v] = dist[x] + 1; file.append(v)
        for sw in COMMUTATEURS:
            suivant = h if sw == acces else next(v for v in voisins(sw) if dist[v] == dist[sw] - 1)
            _flux(sw, f'priority=100,ip,nw_dst={ip(h)},actions=output:{net.port[sw, suivant]}')


class Chemins:

    def __init__(self, net):
        self.net, self.choix = net, {}

    def _regles(self, src, dst, chemin):
        for sens, (a, b), p in (('aller', (src, dst), chemin), ('retour', (dst, src), list(reversed(chemin)))):
            for i, sw in enumerate(p):
                suivant = b if i == len(p) - 1 else p[i + 1]
                yield sw, f'ip,nw_src={ip(a)},nw_dst={ip(b)}', self.net.port[sw, suivant]

    def retirer(self, src, dst):
        if (src, dst) in self.choix:
            for sw, filtre, _ in self._regles(src, dst, self.choix.pop((src, dst))):
                sh(f'ovs-ofctl -O OpenFlow13 --strict del-flows {sw} "priority=200,{filtre}"')

    def poser(self, src, dst, chemin):
        assert chemin in chemins(HOTES[src], HOTES[dst]), chemin
        ancien = self.choix.get((src, dst))
        if ancien == chemin:
            return
        nouvelles = {(sw, f): port for sw, f, port in self._regles(src, dst, chemin)}
        for (sw, f), port in nouvelles.items():
            _flux(sw, f'priority=200,{f},actions=output:{port}')
        if ancien:
            for sw, f, _ in self._regles(src, dst, ancien):
                if (sw, f) not in nouvelles:
                    sh(f'ovs-ofctl -O OpenFlow13 --strict del-flows {sw} "priority=200,{f}"')
        self.choix[src, dst] = chemin

    def paquets(self, sw, src, dst):
        out = sh(f'ovs-ofctl -O OpenFlow13 dump-flows {sw} "ip,nw_src={ip(src)},nw_dst={ip(dst)}"')
        return sum(int(l.split('n_packets=')[1].split(',')[0]) for l in out.splitlines() if 'priority=200' in l)


def tester():
    import json
    import re
    import time
    from mininet.log import setLogLevel
    setLogLevel('warning')
    net = construire()
    demarrer(net)
    try:
        perdus = net.pingAll(timeout='1')
        print('connectivité des 8 machines :', 'OK' if perdus == 0 else f'ÉCHEC ({perdus:.0f} % de pings perdus)')
        h1 = net.get('h1')
        rtt = lambda dst: float(re.search(r'= [\d.]+/([\d.]+)/', h1.cmd(f'ping -c 10 -i 0.2 {ip(dst)}')).group(1))
        print(f'RTT h1→h5 (client → serveur, 3 liens) : {rtt("h5"):.1f} ms (attendu ~9)')
        print(f'RTT h1→h3 (même bloc, 2 liens)        : {rtt("h3"):.1f} ms (attendu ~6)')
        print(f'RTT h1→h2 (même commutateur)          : {rtt("h2"):.1f} ms (attendu ~0)')
        ch = Chemins(net)
        print('chemins possibles a1 → a3 :', [' → '.join(p) for p in chemins('a1', 'a3')])
        for p in chemins('a1', 'a3'):
            ch.poser('h1', 'h5', p)
            time.sleep(1.5)
            avant = {sw: ch.paquets(sw, 'h1', 'h5') for sw in DISTRIB + COEUR}
            r = rtt('h5')
            time.sleep(1.5)
            passe = [sw for sw in DISTRIB + COEUR if ch.paquets(sw, 'h1', 'h5') - avant[sw] >= 10]
            print(f'  flux h1→h5 posé sur {" → ".join(p)} : RTT {r:.1f} ms, vu par {passe}', 'OK' if passe == sorted(p[1:3], key=(DISTRIB + COEUR).index) else 'ÉCHEC')
        ch.retirer('h1', 'h5')
        print(f'chemin retiré : RTT h1→h5 {rtt("h5"):.1f} ms, règles restantes : {sum(ch.paquets(sw, "h1", "h5") for sw in COMMUTATEURS)} paquet(s) sur des règles de chemin')
        srv = net.get('h5'); srv.cmd('iperf3 -s -D')
        time.sleep(1)
        j = json.loads(h1.cmd(f'iperf3 -c {ip("h5")} -t 5 -J'))
        print(f'débit TCP h1→h5 : {j["end"]["sum_received"]["bits_per_second"] / 1e6:.1f} Mb/s (liens à {LIEN_MBPS})')
        srv.cmd('pkill iperf3')
    finally:
        net.stop()


if __name__ == '__main__':
    if '--test' in sys.argv:
        tester()
