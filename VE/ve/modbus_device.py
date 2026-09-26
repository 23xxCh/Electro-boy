# -*- coding: utf-8 -*-
"""modbus_device.py —— Modbus TCP 设备服务器（模拟 Anker SOLIX Solarbank Max AC）。

这是 VE 与官方 HA 插件（ha-anker-solix-official）之间的**通信管道**：
HA 插件用 pymodbus 客户端通过 Modbus TCP(:502 风格，本模拟器用 1502/1503/1504)
读写寄存器；本服务器用标准库实现 Modbus TCP 协议（FC03/FC04/FC06/FC16），
把站点实时状态映射到寄存器（见 register_map.py，移植自官方插件）。

零第三方依赖（仅 socket + threading + struct）。
"""
from __future__ import annotations

import socket
import struct
import threading


class ModbusDeviceServer(threading.Thread):
    """一台虚拟设备的 Modbus TCP 服务器。"""

    def __init__(self, host: str, port: int, regmap, site_id: str):
        super().__init__(daemon=True, name=f"modbus-{site_id}")
        self.host = host
        self.port = port
        self.regmap = regmap
        self.site_id = site_id
        self._sock: socket.socket | None = None
        self._running = False

    def run(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        self._sock.listen(8)
        self._sock.settimeout(1.0)
        self._running = True
        while self._running:
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def stop(self) -> None:
        self._running = False
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass

    # ------------------------------------------------------------------ 连接处理
    def _handle(self, conn: socket.socket) -> None:
        conn.settimeout(10.0)
        with conn:
            while self._running:
                try:
                    hdr = self._recv_exact(conn, 7)
                except (ConnectionError, OSError, socket.timeout):
                    break
                if hdr is None:
                    break
                tid, _pid, length, uid = struct.unpack(">HHHB", hdr)
                pdu = self._recv_exact(conn, max(length - 1, 0))
                if pdu is None:
                    break
                resp_pdu = self._dispatch(pdu)
                out = struct.pack(">HHHB", tid, 0, len(resp_pdu) + 1, uid) + resp_pdu
                try:
                    conn.sendall(out)
                except OSError:
                    break

    @staticmethod
    def _recv_exact(conn: socket.socket, n: int) -> bytes | None:
        buf = b""
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf

    # ------------------------------------------------------------------ 协议分发
    def _dispatch(self, pdu: bytes) -> bytes:
        if len(pdu) < 1:
            return self._exception(0, 1)
        fc = pdu[0]
        if fc == 3:
            return self._read(pdu, "holding")
        if fc == 4:
            return self._read(pdu, "input")
        if fc == 6:
            return self._write_single(pdu)
        if fc == 16:
            return self._write_multiple(pdu)
        return self._exception(fc, 1)          # Illegal Function

    def _read(self, pdu: bytes, reg_type: str) -> bytes:
        if len(pdu) < 5:
            return self._exception(pdu[0], 3)
        addr = (pdu[1] << 8) | pdu[2]
        qty = (pdu[3] << 8) | pdu[4]
        if not (1 <= qty <= 125):
            return self._exception(pdu[0], 3)
        words = self.regmap.read(reg_type, addr, qty)
        body = bytes([qty * 2]) + b"".join(struct.pack(">H", w & 0xFFFF) for w in words)
        return bytes([pdu[0]]) + body

    def _write_single(self, pdu: bytes) -> bytes:
        if len(pdu) < 5:
            return self._exception(6, 3)
        addr = (pdu[1] << 8) | pdu[2]
        val = (pdu[3] << 8) | pdu[4]
        code = self.regmap.write(addr, [val])
        if code:
            return self._exception(6, code)
        return pdu                              # 回显

    def _write_multiple(self, pdu: bytes) -> bytes:
        if len(pdu) < 7:
            return self._exception(16, 3)
        addr = (pdu[1] << 8) | pdu[2]
        qty = (pdu[3] << 8) | pdu[4]
        bytecnt = pdu[5]
        if bytecnt != qty * 2 or len(pdu) < 6 + bytecnt:
            return self._exception(16, 3)
        values = [(pdu[6 + 2 * i] << 8) | pdu[6 + 2 * i + 1] for i in range(qty)]
        code = self.regmap.write(addr, values)
        if code:
            return self._exception(16, code)
        return bytes([16, (addr >> 8) & 0xFF, addr & 0xFF, (qty >> 8) & 0xFF, qty & 0xFF])

    @staticmethod
    def _exception(fc: int, code: int) -> bytes:
        return bytes([fc | 0x80, code])
