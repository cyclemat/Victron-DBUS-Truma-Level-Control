"""Passiver Advertisement-Beobachter ueber den HCI-Monitor-Kanal.

Das ist derselbe Kanal, den btmon benutzt. Er ist rein lesend: Es werden
keine HCI-Befehle gesendet und der Scan-Zustand wird nicht veraendert.
Wir sehen damit die Advertisements, die ohnehin empfangen werden (z. B.
weil Victrons Bluetooth-Sensor-Dienst scannt) und wissen so, wann der
LevelControl gerade sendet und ein Verbindungsversuch Sinn ergibt.
"""

import ctypes
import logging
import os
import socket
import struct
import threading
import time

log = logging.getLogger("hcimon")

AF_BLUETOOTH = 31
BTPROTO_HCI = 1
HCI_DEV_NONE = 0xFFFF
HCI_CHANNEL_MONITOR = 2
HCI_MON_EVENT_PKT = 3
EVT_LE_META = 0x3E
LE_ADV_REPORT = 0x02
LE_EXT_ADV_REPORT = 0x0D


def mac_to_bytes(mac):
    """'AA:BB:CC:DD:EE:FF' -> Bytes in HCI-Reihenfolge (little endian)."""
    return bytes(int(x, 16) for x in reversed(mac.split(":")))


def parse_adv_addresses(event):
    """Adressen (je 6 Byte, little endian) aus einem HCI-LE-Advertising-Event."""
    if len(event) < 4 or event[0] != EVT_LE_META:
        return []
    params = event[2:2 + event[1]]
    if len(params) < 2:
        return []
    sub, num = params[0], params[1]
    pos = 2
    out = []
    try:
        for _ in range(num):
            if sub == LE_ADV_REPORT:
                # evt_type(1) addr_type(1) addr(6) len(1) data(len) rssi(1)
                addr = params[pos + 2:pos + 8]
                dlen = params[pos + 8]
                pos += 9 + dlen + 1
            elif sub == LE_EXT_ADV_REPORT:
                # evt_type(2) addr_type(1) addr(6) phys(2) sid(1) tx(1) rssi(1)
                # interval(2) direct_type(1) direct_addr(6) len(1) data(len)
                addr = params[pos + 3:pos + 9]
                dlen = params[pos + 23]
                pos += 24 + dlen
            else:
                return []
            if len(addr) == 6:
                out.append(bytes(addr))
    except IndexError:
        pass
    return out


class AdvertMonitor(threading.Thread):
    def __init__(self, macs, adapter_index=None):
        super().__init__(name="hci-monitor", daemon=True)
        self._watch = {mac_to_bytes(m): m.upper() for m in macs}
        self._index = adapter_index
        self._last_seen = {}
        self._cond = threading.Condition()
        self._sock = None
        self.active = False

    def open(self):
        """Oeffnet den Monitor-Kanal. False, wenn das nicht geht."""
        try:
            sock = socket.socket(AF_BLUETOOTH, socket.SOCK_RAW, BTPROTO_HCI)
            # Python kann den HCI-Kanal nicht selbst setzen -> bind() per ctypes
            addr = struct.pack("=HHH", AF_BLUETOOTH, HCI_DEV_NONE, HCI_CHANNEL_MONITOR)
            buf = ctypes.create_string_buffer(addr, len(addr))
            libc = ctypes.CDLL(None, use_errno=True)
            if libc.bind(sock.fileno(), buf, len(addr)) != 0:
                err = ctypes.get_errno()
                sock.close()
                raise OSError(err, os.strerror(err))
        except OSError as err:
            log.warning("HCI-Monitor nicht verfuegbar (%s), nutze nur Verbindungsfenster", err)
            return False
        self._sock = sock
        self.active = True
        return True

    def run(self):
        while True:
            try:
                pkt = self._sock.recv(4096)
            except OSError as err:
                log.warning("HCI-Monitor beendet: %s", err)
                self.active = False
                with self._cond:
                    self._cond.notify_all()
                return
            if len(pkt) < 8:
                continue
            opcode, index, plen = struct.unpack_from("<HHH", pkt, 0)
            if opcode != HCI_MON_EVENT_PKT or pkt[6] != EVT_LE_META:
                continue
            if self._index is not None and index != self._index:
                continue
            for addr in parse_adv_addresses(pkt[6:6 + plen]):
                mac = self._watch.get(addr)
                if mac is not None:
                    with self._cond:
                        self._last_seen[mac] = time.monotonic()
                        self._cond.notify_all()

    def wait_for_advert(self, mac, timeout_s, stop=None):
        """True, sobald ein frisches Advertisement von mac gesehen wurde."""
        mac = mac.upper()
        start = time.monotonic()
        deadline = start + timeout_s
        with self._cond:
            while True:
                if self._last_seen.get(mac, 0.0) >= start - 1.0:
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self.active or (stop is not None and stop.is_set()):
                    return False
                self._cond.wait(min(remaining, 1.0))
