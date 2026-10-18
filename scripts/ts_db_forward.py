"""Encaminha uma porta local até o Postgres na tailnet, via o SOCKS5 do tailscaled.

Usado no Railway (scripts/railway-tailscale-start.sh): o tailscaled roda em modo
userspace e não cria interface de rede, então o psycopg2 conecta em
127.0.0.1:<TS_DB_LOCAL_PORT> e este processo repassa a conexão pelo SOCKS5.
"""
import os
import socket
import struct
import threading

LISTEN_PORT = int(os.environ.get("TS_DB_LOCAL_PORT", "15432"))
SOCKS_PORT = int(os.environ.get("TS_SOCKS_PORT", "1055"))
TARGET_HOST = os.environ["TS_DB_HOST"]
TARGET_PORT = int(os.environ.get("TS_DB_PORT", "5432"))


def _recv_exact(sock, n):
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("SOCKS5 fechou a conexão")
        data += chunk
    return data


def _socks5_connect():
    sock = socket.create_connection(("127.0.0.1", SOCKS_PORT), timeout=15)
    sock.sendall(b"\x05\x01\x00")
    if _recv_exact(sock, 2) != b"\x05\x00":
        raise ConnectionError("SOCKS5 recusou o método sem autenticação")
    host = TARGET_HOST.encode()
    sock.sendall(b"\x05\x01\x00\x03" + bytes([len(host)]) + host + struct.pack(">H", TARGET_PORT))
    reply = _recv_exact(sock, 4)
    if reply[1] != 0:
        raise ConnectionError(f"SOCKS5 não conectou em {TARGET_HOST}:{TARGET_PORT} (código {reply[1]})")
    atyp = reply[3]
    if atyp == 1:
        _recv_exact(sock, 4 + 2)
    elif atyp == 4:
        _recv_exact(sock, 16 + 2)
    else:
        _recv_exact(sock, _recv_exact(sock, 1)[0] + 2)
    sock.settimeout(None)
    return sock


def _pipe(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _handle(client):
    try:
        upstream = _socks5_connect()
    except Exception as exc:
        print(f"[ts-db-forward] falha ao conectar: {exc}", flush=True)
        client.close()
        return
    threading.Thread(target=_pipe, args=(client, upstream), daemon=True).start()
    _pipe(upstream, client)
    client.close()
    upstream.close()


def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", LISTEN_PORT))
    server.listen(64)
    print(f"[ts-db-forward] 127.0.0.1:{LISTEN_PORT} -> {TARGET_HOST}:{TARGET_PORT}", flush=True)
    while True:
        client, _ = server.accept()
        threading.Thread(target=_handle, args=(client,), daemon=True).start()


if __name__ == "__main__":
    main()
