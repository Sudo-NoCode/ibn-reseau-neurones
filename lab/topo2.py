import subprocess
import sys
from subprocess import DEVNULL

from mininet.link import TCLink
from mininet.net import Mininet
from mininet.node import OVSSwitch

from topo import GOULOT_DELAI, GOULOT_IF, GOULOT_MBPS

SECOURS_MBPS = 6
SECOURS_DELAI = '8ms'
SECOURS_IF = 's1-eth4'
NETEM_CIBLES = [('5:10', '10:'), ('5:20', '20:')]
H2_IF = 's1-eth2'
LIMITE_H2_KBPS = 5000


def sh(cmd, verifier=True):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if verifier and r.returncode != 0:
        raise RuntimeError(f'{cmd}\n{r.stderr}')
    return r.stdout


def construire(bw=GOULOT_MBPS):
    net = Mininet(switch=OVSSwitch, link=TCLink, controller=None, autoSetMacs=True, autoStaticArp=True)
    h1, h2, h3, h4 = [net.addHost(f'h{i}', ip=f'10.0.0.{i}/24') for i in range(1, 5)]
    s1, s2, s3 = [net.addSwitch(f's{i}', failMode='secure', protocols='OpenFlow13') for i in range(1, 4)]
    net.addLink(h1, s1)
    net.addLink(h2, s1)
    net.addLink(h3, s2)
    net.addLink(h4, s2)
    net.addLink(s1, s2, bw=bw, delay=GOULOT_DELAI)
    net.addLink(s1, s3, bw=SECOURS_MBPS, delay=SECOURS_DELAI)
    net.addLink(s3, s2, bw=SECOURS_MBPS, delay=SECOURS_DELAI)
    net.bw = bw
    return net


def demarrer(net):
    net.start()
    _routes()
    _goulot_a_deux_classes(net.bw)


def _flux(switch, regle):
    sh(f'ovs-ofctl -O OpenFlow13 add-flow {switch} "{regle}"')


def _routes():
    for sw in ('s1', 's2', 's3'):
        sh(f'ovs-ofctl -O OpenFlow13 del-flows {sw}')
    for ip, port in (('10.0.0.1', 1), ('10.0.0.2', 2), ('10.0.0.3', 3), ('10.0.0.4', 3)):
        _flux('s1', f'priority=100,ip,nw_dst={ip},actions=output:{port}')
    for ip, port in (('10.0.0.3', 1), ('10.0.0.4', 2), ('10.0.0.1', 3), ('10.0.0.2', 3)):
        _flux('s2', f'priority=100,ip,nw_dst={ip},actions=output:{port}')
    for ip, port in (('10.0.0.1', 1), ('10.0.0.2', 1), ('10.0.0.3', 2), ('10.0.0.4', 2)):
        _flux('s3', f'priority=100,ip,nw_dst={ip},actions=output:{port}')


def _goulot_a_deux_classes(bw):
    visio = min(6.0, bw / 2)
    d = GOULOT_IF
    sh(f'tc qdisc del dev {d} root', verifier=False)
    sh(f'tc qdisc add dev {d} root handle 5: htb default 20')
    sh(f'tc class add dev {d} parent 5: classid 5:1 htb rate {bw}mbit')
    sh(f'tc class add dev {d} parent 5:1 classid 5:10 htb rate {visio}mbit ceil {bw}mbit prio 0')
    sh(f'tc class add dev {d} parent 5:1 classid 5:20 htb rate {bw - visio}mbit ceil {bw}mbit prio 1')
    for parent, handle in NETEM_CIBLES:
        sh(f'tc qdisc add dev {d} parent {parent} handle {handle} netem limit 1000 delay {GOULOT_DELAI}')


def etat_secours(delai_ms=8.0, pertes_pct=0.0):
    perte = f' loss {pertes_pct:.2f}%' if pertes_pct >= 0.01 else ''
    sh(f'tc qdisc change dev {SECOURS_IF} parent 5:1 handle 10: netem limit 1000 delay {delai_ms:.1f}ms{perte}')


class Actions:

    NOMS = ['rien', 'limiter_h2', 'priorite_visio', 'deroute_visio', 'reinitialiser']

    def __init__(self):
        self.limite = self.priorite = self.deroute = False

    def appliquer(self, a):
        nom = self.NOMS[a]
        if nom == 'limiter_h2':
            self.set_limite(True)
        elif nom == 'priorite_visio':
            self.set_priorite(True)
        elif nom == 'deroute_visio':
            self.set_deroute(True)
        elif nom == 'reinitialiser':
            self.set_limite(False)
            self.set_priorite(False)
            self.set_deroute(False)

    def set_limite(self, on):
        if on != self.limite:
            r, b = (LIMITE_H2_KBPS, LIMITE_H2_KBPS // 10) if on else (0, 0)
            sh(f'ovs-vsctl set interface {H2_IF} ingress_policing_rate={r} ingress_policing_burst={b}')
            self.limite = on

    def set_priorite(self, on):
        if on != self.priorite:
            if on:
                sh(f'tc filter add dev {GOULOT_IF} parent 5: protocol ip prio 1 u32 '
                   f'match ip src 10.0.0.1/32 match ip dst 10.0.0.3/32 flowid 5:10')
            else:
                sh(f'tc filter del dev {GOULOT_IF} parent 5: prio 1')
            self.priorite = on

    def set_deroute(self, on):
        if on != self.deroute:
            aller, retour = 'ip,nw_src=10.0.0.1,nw_dst=10.0.0.3', 'ip,nw_src=10.0.0.3,nw_dst=10.0.0.1'
            if on:
                _flux('s1', f'priority=200,{aller},actions=output:4')
                _flux('s2', f'priority=200,{retour},actions=output:4')
            else:
                sh(f'ovs-ofctl -O OpenFlow13 --strict del-flows s1 "priority=200,{aller}"')
                sh(f'ovs-ofctl -O OpenFlow13 --strict del-flows s2 "priority=200,{retour}"')
            self.deroute = on

    def etat(self):
        return dict(limite=int(self.limite), priorite=int(self.priorite), deroute=int(self.deroute))


def tester():
    import re
    from mininet.log import setLogLevel
    setLogLevel('warning')
    net = construire()
    demarrer(net)
    try:
        h1 = net.get('h1')
        print('connectivité :', 'OK' if net.pingAll(timeout='1') == 0 else 'ÉCHEC')
        rtt = lambda: float(re.search(r'= [\d.]+/([\d.]+)/', h1.cmd('ping -c 10 -i 0.2 10.0.0.3')).group(1))
        a = Actions()
        print(f'RTT h1->h3 chemin principal : {rtt():.1f} ms (attendu ~10)')
        a.set_deroute(True)
        print(f'RTT h1->h3 chemin de secours : {rtt():.1f} ms (attendu ~32)')
        a.set_deroute(False)
        print(f'RTT h1->h3 retour au principal : {rtt():.1f} ms')
        print('netem du goulot :', [l.split(' limit')[0] for l in sh(f'tc qdisc show dev {GOULOT_IF}').splitlines() if 'netem' in l])
        a.set_priorite(True); a.set_limite(True)
        print('filtre priorité posé :', 'flowid 5:10' in sh(f'tc filter show dev {GOULOT_IF} parent 5:'))
        print('limite h2 posée :', sh(f'ovs-vsctl get interface {H2_IF} ingress_policing_rate').strip(), 'kbps')
        a.appliquer(4)
        print('après réinitialiser :', a.etat())
    finally:
        net.stop()


if __name__ == '__main__':
    if '--test' in sys.argv:
        tester()
