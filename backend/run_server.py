"""Un proceso GPU, con acceso por Tailscale y loopback para el proxy HTTPS."""
import argparse
import socket

import uvicorn


def listen_addresses(host, also_loopback):
    hosts = [host]
    if also_loopback and host not in ("127.0.0.1", "localhost", "0.0.0.0", "::", "::1"):
        hosts.append("127.0.0.1")
    return hosts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--also-loopback", action="store_true")
    args = parser.parse_args()
    sockets = []
    try:
        for host in listen_addresses(args.host, args.also_loopback):
            listener = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM)
            sockets.append(listener)
            listener.bind((host, args.port))
            listener.listen(socket.SOMAXCONN)
            listener.setblocking(False)
        uvicorn.Server(uvicorn.Config("app.main:app", workers=1, port=args.port)).run(sockets=sockets)
    finally:
        for listener in sockets:
            listener.close()


if __name__ == "__main__":
    main()
