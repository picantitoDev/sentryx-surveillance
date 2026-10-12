"""Listeners del servidor: conservar Tailscale y evitar puertos duplicados."""

def test_loopback_listener_keeps_tailscale_address():
    from run_server import listen_addresses
    assert listen_addresses("100.79.63.46", True) == ["100.79.63.46", "127.0.0.1"]
    assert listen_addresses("127.0.0.1", True) == ["127.0.0.1"]
    assert listen_addresses("0.0.0.0", True) == ["0.0.0.0"]
