"""Test changing load-balancing strategy in DNSPolicy"""

from time import sleep

import pytest
import dns.resolver

from testsuite.kuadrant.policy.dns import has_record_condition

pytestmark = [pytest.mark.multicluster]


def test_change_lb_strategy(hostname, gateway, gateway2, dns_policy2, dns_server, dns_server2, wildcard_domain):
    """Verify that removing DNSPolicy load-balancing configuration removes GEO load-balancing endpoint from the pool"""
    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = [dns_server["address"]]
    assert resolver.resolve(hostname.hostname)[0].address == gateway.external_ip().split(":")[0]

    resolver.nameservers = [dns_server2["address"]]
    assert resolver.resolve(hostname.hostname)[0].address == gateway2.external_ip().split(":")[0]

    dns_policy2.refresh().model.spec.pop("loadBalancing")
    res = dns_policy2.apply()
    assert res.status() == 0, res.err()
    assert dns_policy2.wait_until(
        has_record_condition(
            "Ready",
            "False",
            "ProviderError",
            "The DNS provider failed to ensure the record: record type conflict, cannot update endpoint "
            f"'{wildcard_domain}' with record type 'A' when endpoint already exists with record type 'CNAME'",
        )
    ), f"DNSPolicy did not reach expected record status, instead it was: {dns_policy2.model.status.recordConditions}"

    sleep(300)  # wait for DNS propagation on providers
    assert resolver.resolve(hostname.hostname)[0].address == gateway.external_ip().split(":")[0]
