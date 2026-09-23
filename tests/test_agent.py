import ipaddress
import socket
from types import SimpleNamespace as NS
from unittest.mock import patch
import pytest
import agente_v2 as agent

@pytest.mark.parametrize('cidr,ip,limit,expected', [('10.2.0.0/16','10.2.3.4',64,True),('192.168.1.0/24','192.168.1.5',512,False),('10.2.3.4/31','10.2.3.4',64,False),('bad','10.0.0.1',64,False)])
def test_scan_bound(monkeypatch,cidr,ip,limit,expected):
    monkeypatch.setattr(agent,'MAX_NETWORK_SCAN_HOSTS',limit)
    network,limited=agent._rede_scan_segura(cidr,ip)
    assert limited==expected
    if network:
        assert len(list(network.hosts()))<=limit
        assert ipaddress.ip_address(ip) in network


def test_missing_mask_is_unknown(monkeypatch):
    monkeypatch.setattr(agent,'IS_WIN',False)
    with patch.object(agent.subprocess,'check_output',return_value='[{"dev":"eth0","gateway":"10.0.0.1","prefsrc":"10.0.0.2"}]'),patch.object(agent.psutil,'net_if_addrs',return_value={}),patch.object(agent.psutil,'net_if_stats',return_value={}):
        context=agent.get_network_context()
    assert context['cidr']=='' and context['scan_rede']==''

@pytest.mark.parametrize('windows',[False,True])
def test_active_interface(monkeypatch,windows):
    monkeypatch.setattr(agent,'IS_WIN',windows)
    route='0.0.0.0 0.0.0.0 192.168.20.1 192.168.20.7 10' if windows else '[{"dev":"Ethernet","gateway":"192.168.20.1","prefsrc":"192.168.20.7"}]'
    addresses={'Ethernet':[NS(family=socket.AF_INET,address='192.168.20.7',netmask='255.255.254.0'),NS(family=NS(name='AF_LINK'),address='AA-BB-CC-DD-EE-FF')], 'Other':[NS(family=socket.AF_INET,address='10.0.0.2',netmask='255.255.255.0')]}
    with patch.object(agent.subprocess,'check_output',return_value=route),patch.object(agent.psutil,'net_if_addrs',return_value=addresses),patch.object(agent.psutil,'net_if_stats',return_value={'Ethernet':NS(isup=True,speed=1000)}):
        context=agent.get_network_context()
    assert context['interface']=='Ethernet'
    assert context['cidr']=='192.168.20.0/23'
    assert context['mac_interface']=='AA:BB:CC:DD:EE:FF'
    assert context['link_speed_mbps']==1000


def test_neighbor_states(monkeypatch):
    monkeypatch.setattr(agent,'IS_WIN',False)
    output='10.0.0.2 dev eth0 lladdr aa:bb:cc:dd:ee:01 REACHABLE\n10.0.0.3 dev eth0 lladdr aa:bb:cc:dd:ee:02 STALE\n10.0.0.4 dev eth0 lladdr aa:bb:cc:dd:ee:03 FAILED\nfe80::1 dev eth0 lladdr aa:bb:cc:dd:ee:04 STALE'
    with patch.object(agent.subprocess,'check_output',return_value=output),patch.object(agent,'ping',side_effect=lambda ip:0.3 if ip=='10.0.0.2' else 0):
        rows=agent.get_topologia_arp({'ip':'10.0.0.1','interface':'eth0','cidr':'10.0.0.0/24'})
    assert [row['status'] for row in rows]==['online','sem_icmp','desconhecido']


def test_ping_does_not_invoke_shell():
    with patch.object(agent.subprocess,'check_output',return_value=b'time=0.3 ms') as run:
        assert agent.ping('10.0.0.1')==0.3
        assert run.call_args.kwargs['shell'] is False
        assert agent.ping('127.0.0.1;touch /tmp/attack')==0
        assert run.call_count==1
