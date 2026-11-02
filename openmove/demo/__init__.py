# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Demo mode: a built-in mock cloud with made-up data, so the tool can be shown without a real cloud.

Everything the demonstration shows is synthetic, the screens say so, and reports made in demo
mode carry a notice on their first page. Demo mode always uses a temporary data folder, so it never
touches the journal of a real installation. Several scenarios are available (see scenarios.py); the
interface can switch between them, which resets the demonstration.
"""
from ..connectors.openstack import OpenStackSession
from ..journal import Journal
from . import scenarios
from .mock_cloud import PASSWORD, demo_cloud

DEFAULT = scenarios.DEFAULT


def start(state, port=0, scenario=DEFAULT):
    """Start the demonstration cloud and connect the application to it. The cloud is kept on state.demo_cloud."""
    scenarios.get(scenario)
    cloud = demo_cloud(scenario).start(port)
    session = OpenStackSession(cloud.auth_url, "demo-admin", PASSWORD, "demo")
    info = session.authenticate()
    with state.lock:
        state.session = session
        state.demo = True
        state.demo_cloud = cloud
        state.demo_scenario = scenario
        state.access = "full"      # the demonstration simulates the staged method too, so it pretends the hosts and arrays are reachable
    state.journal.record("demo", "start", {"note": "demonstration cloud with synthetic data", "scenario": scenario,
                                           "roles": info["roles"]})
    state.journal.record("connect", "demo", {"auth_url": session.auth_url, "user": session.username,
                                             "tls_verification": session.verify, "roles": info["roles"], "demo": True})
    return cloud


def stop(state):
    if getattr(state, "sim", None):
        state.sim.stop()
        state.sim = None
    if state.demo_cloud:
        state.demo_cloud.stop()
        state.demo_cloud = None


def switch(state, scenario):
    """Change the demonstration to another scenario and start it from a clean slate (new journal, nothing read yet)."""
    scenarios.get(scenario)
    state.disconnect()
    stop(state)  # also stops a running simulation
    path = state.home / "journal.db"
    state.journal.close()
    for suffix in ("", "-wal", "-shm"):
        try:
            (state.home / ("journal.db" + suffix)).unlink()
        except FileNotFoundError:
            pass
    state.journal = Journal(path)
    return start(state, scenario=scenario)
