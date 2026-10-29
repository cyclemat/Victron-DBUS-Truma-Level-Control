"""Truma LevelControl - BLE-Protokoll (Entschluesselung + Parser).

Protokoll aus cyclemat/HAOS-Truma-Levelcontrol-Integration uebernommen
(reverse-engineered aus der LevelControl-App 2.2.1). GPL-3.0.
Nur Standardbibliothek, damit es auf Venus OS ohne pip laeuft.
"""

from dataclasses import dataclass

_BASE = "-6afe-4cc5-b3ff-3e140baedeb6"
UUID_SERVICE = "22bfa601" + _BASE
UUID_MEASUREMENT = "22bfa610" + _BASE  # read / write / notify
UUID_PASSWORD = "22bfa619" + _BASE     # read -> 5 Bytes
UUID_STORE_NV = "22bfa617" + _BASE     # write -> Messung ausloesen

MANUFACTURER_ID = 0x5000  # Hersteller-ID im Advertisement

TRIGGER_INIT = 0x03
TRIGGER_GO = 0x02
TRIGGER_GAP_S = 0.4
MEASURE_WAIT_S = 3.0


@dataclass
class TrumaReading:
    percent: int
    grams: int
    battery: int
    temperature: int
    quality: int
    raw: str

    @property
    def kg(self) -> float:
        return self.grams / 1000.0


def rc4(key: bytes, data: bytes) -> bytes:
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) & 0xFF
        s[i], s[j] = s[j], s[i]
    out = bytearray()
    i = j = 0
    for b in data:
        i = (i + 1) & 0xFF
        j = (j + s[i]) & 0xFF
        s[i], s[j] = s[j], s[i]
        out.append(b ^ s[(s[i] + s[j]) & 0xFF])
    return bytes(out)


def derive_key(password: bytes) -> bytes:
    """RC4-Key = 5-Byte-Passwort XOR ASCII "TRUMA"."""
    if len(password) != 5:
        raise ValueError("Passwort muss 5 Bytes lang sein, ist %d" % len(password))
    return bytes(p ^ m for p, m in zip(password, b"TRUMA"))


def parse(dec: bytes) -> TrumaReading:
    if len(dec) < 5:
        raise ValueError("Messwert zu kurz (%d Bytes)" % len(dec))
    # Annahme: Temperatur ist ein vorzeichenbehaftetes Byte (Werte 0..127
    # sind identisch zur HA-Integration, Minusgrade werden so korrekt).
    temp = dec[4] - 256 if dec[4] > 127 else dec[4]
    reading = TrumaReading(
        percent=dec[0],
        grams=int.from_bytes(dec[1:3], "little") * 100,
        battery=dec[3],
        temperature=temp,
        quality=(dec[6] >> 6) & 0b11 if len(dec) > 6 else 0,
        raw=" ".join("%02x" % b for b in dec),
    )
    if reading.percent > 100:
        raise ValueError("unplausibler Fuellstand %d %%" % reading.percent)
    return reading


def decode_measurement(password: bytes, raw: bytes) -> TrumaReading:
    return parse(rc4(derive_key(password), raw))
