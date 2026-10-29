"""Minimaler GATT-Client ueber die BlueZ-D-Bus-API (org.bluez).

Spricht ausschliesslich mit bluetoothd: keine rohen HCI-Befehle und keine
Aenderung an Adapter-Einstellungen (Powered, Discoverable, Scan-Parameter).
Alle Aufrufe sind blockierend und fuer einen Worker-Thread gedacht, der
eine eigene Bus-Verbindung ohne Mainloop benutzt.
"""

import logging
import time

import dbus

BLUEZ = "org.bluez"
OM_IFACE = "org.freedesktop.DBus.ObjectManager"
PROPS_IFACE = "org.freedesktop.DBus.Properties"
ADAPTER_IFACE = "org.bluez.Adapter1"
DEVICE_IFACE = "org.bluez.Device1"
CHAR_IFACE = "org.bluez.GattCharacteristic1"

log = logging.getLogger("bluez")


class BleError(Exception):
    pass


class DeviceUnknown(BleError):
    """bluetoothd hat (noch) kein Device1-Objekt fuer diese Adresse."""


def device_path(adapter, mac):
    return "/org/bluez/%s/dev_%s" % (adapter, mac.upper().replace(":", "_"))


def _no_options():
    return dbus.Dictionary({}, signature="sv")


class BluezAdapter:
    def __init__(self, bus, adapter="hci0"):
        self.bus = bus
        self.name = adapter
        self.path = "/org/bluez/" + adapter

    def iface(self, path, iface):
        return dbus.Interface(self.bus.get_object(BLUEZ, path), iface)

    def managed_objects(self):
        return self.iface("/", OM_IFACE).GetManagedObjects()

    def powered(self):
        try:
            return bool(self.iface(self.path, PROPS_IFACE).Get(ADAPTER_IFACE, "Powered"))
        except dbus.DBusException as err:
            log.debug("Adapter %s nicht verfuegbar: %s", self.name, err)
            return False

    def device_known(self, mac):
        try:
            self.iface(device_path(self.name, mac), PROPS_IFACE).Get(DEVICE_IFACE, "Address")
            return True
        except dbus.DBusException:
            return False

    def discover(self, timeout_s, done=None, stop=None):
        """Kurze, eigene Discovery-Session bei bluetoothd.

        bluetoothd fuehrt Discovery pro Client: unser StopDiscovery beendet
        nur unsere Session, nicht die anderer Dienste. Stirbt der Prozess,
        raeumt bluetoothd die Session selbst auf.
        done(): True beendet die Discovery vorzeitig.
        """
        adapter = self.iface(self.path, ADAPTER_IFACE)
        try:
            adapter.SetDiscoveryFilter(dbus.Dictionary({
                "Transport": dbus.String("le"),
                "DuplicateData": dbus.Boolean(False),
            }, signature="sv"))
        except dbus.DBusException as err:
            log.debug("SetDiscoveryFilter nicht moeglich: %s", err)
        adapter.StartDiscovery()
        log.info("Discovery auf %s gestartet (max. %d s)", self.name, timeout_s)
        try:
            deadline = time.monotonic() + timeout_s
            while time.monotonic() < deadline:
                if stop is not None and stop.is_set():
                    break
                if done is not None and done():
                    return True
                time.sleep(1.0)
            return bool(done()) if done is not None else True
        finally:
            try:
                adapter.StopDiscovery()
            except dbus.DBusException as err:
                log.debug("StopDiscovery: %s", err)
            log.info("Discovery auf %s beendet", self.name)

    def devices_with_manufacturer(self, company_id):
        """[(Adresse, Name, RSSI)] aller bekannten Geraete mit dieser Hersteller-ID."""
        result = []
        prefix = self.path + "/"
        for path, ifaces in self.managed_objects().items():
            dev = ifaces.get(DEVICE_IFACE)
            if dev is None or not str(path).startswith(prefix):
                continue
            mdata = dev.get("ManufacturerData", {})
            if any(int(k) == company_id for k in mdata.keys()):
                rssi = int(dev["RSSI"]) if "RSSI" in dev else None
                result.append((str(dev.get("Address", "")),
                               str(dev.get("Name", dev.get("Alias", ""))), rssi))
        return result


class GattDevice:
    """Eine kurzlebige GATT-Verbindung."""

    def __init__(self, adapter, mac):
        self.adapter = adapter
        self.mac = mac.upper()
        self.path = device_path(adapter.name, mac)

    def _prop(self, name):
        return self.adapter.iface(self.path, PROPS_IFACE).Get(DEVICE_IFACE, name)

    def is_connected(self):
        try:
            return bool(self._prop("Connected"))
        except dbus.DBusException:
            return False

    def connect(self, window_s, stop=None, attempt_timeout_s=40):
        """Verbindet innerhalb von window_s Sekunden (ggf. mehrere Versuche).

        Bei LE wartet der Kernel pro Versuch auf ein Advertisement des
        Geraets (ca. 20 s). Danach meldet BlueZ einen Fehler und wir
        versuchen es erneut, bis das Fenster abgelaufen ist.
        """
        dev = self.adapter.iface(self.path, DEVICE_IFACE)
        deadline = time.monotonic() + window_s
        attempt = 0
        last_err = None
        while True:
            if stop is not None and stop.is_set():
                raise BleError("abgebrochen")
            attempt += 1
            try:
                dev.Connect(timeout=attempt_timeout_s)
                self._wait_services_resolved(20)
                log.debug("%s verbunden (Versuch %d)", self.mac, attempt)
                return
            except (dbus.DBusException, BleError) as err:
                last_err = err
                log.debug("%s Verbindungsversuch %d: %s", self.mac, attempt, err)
                self.disconnect()  # halb aufgebaute Verbindung sauber abbauen
            if time.monotonic() + 2 >= deadline:
                break
            time.sleep(2)
        raise BleError("keine Verbindung nach %d Versuch(en): %s" % (attempt, last_err))

    def _wait_services_resolved(self, timeout_s):
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if bool(self._prop("ServicesResolved")):
                return
            if not bool(self._prop("Connected")):
                raise BleError("Verbindung waehrend der Service-Discovery verloren")
            time.sleep(0.2)
        raise BleError("Timeout bei der Service-Discovery")

    def find_characteristics(self, uuids):
        wanted = {u.lower() for u in uuids}
        found = {}
        prefix = self.path + "/"
        for path, ifaces in self.adapter.managed_objects().items():
            if not str(path).startswith(prefix):
                continue
            char = ifaces.get(CHAR_IFACE)
            if char is None:
                continue
            uuid = str(char.get("UUID", "")).lower()
            if uuid in wanted:
                found[uuid] = str(path)
        missing = wanted - set(found)
        if missing:
            raise BleError("Characteristic fehlt: %s" % ", ".join(sorted(missing)))
        return found

    def read(self, char_path, timeout_s=10):
        char = self.adapter.iface(char_path, CHAR_IFACE)
        return bytes(char.ReadValue(_no_options(), timeout=timeout_s, byte_arrays=True))

    def write_command(self, char_path, data, timeout_s=10):
        """Write Without Response (entspricht response=False bei bleak)."""
        char = self.adapter.iface(char_path, CHAR_IFACE)
        char.WriteValue(
            dbus.Array([dbus.Byte(b) for b in data], signature="y"),
            dbus.Dictionary({"type": dbus.String("command")}, signature="sv"),
            timeout=timeout_s,
        )

    def disconnect(self):
        try:
            self.adapter.iface(self.path, DEVICE_IFACE).Disconnect(timeout=10)
        except dbus.DBusException as err:
            log.debug("%s Disconnect: %s", self.mac, err)
