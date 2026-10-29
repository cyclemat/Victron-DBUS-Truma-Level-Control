#!/usr/bin/env python3
"""Truma LevelControl -> Victron Venus OS (D-Bus-Tank-Service).

* BLE ausschliesslich ueber bluetoothd (BlueZ-D-Bus-API), kein bleak, kein pip
* kurze Verbindungen: verbinden, Messung ausloesen, lesen, sofort trennen
* verbindet bevorzugt dann, wenn der Sensor gerade advertised (passiv ueber
  den rein lesenden HCI-Monitor-Kanal beobachtet, wie btmon)
* mehrere Sensoren werden nacheinander gelesen, nie gleichzeitig
* erscheint als com.victronenergy.tank.truma_<mac> mit FluidType LPG

Protokoll: cyclemat/HAOS-Truma-Levelcontrol-Integration (GPL-3.0).
"""

import argparse
import configparser
import logging
import os
import re
import signal
import sys
import threading
import time

import dbus
import dbus.mainloop

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(1, os.path.join(HERE, "ext", "velib_python"))
sys.path.insert(1, "/opt/victronenergy/dbus-systemcalc-py/ext/velib_python")

import truma_protocol as tp  # noqa: E402
from bluez_gatt import BleError, BluezAdapter, DeviceUnknown, GattDevice  # noqa: E402
from hci_monitor import AdvertMonitor  # noqa: E402

VERSION = "0.1.0"
FLUID_TYPE_LPG = 8
STATUS_OK = 0
STATUS_DISCONNECTED = 1
MAC_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")

log = logging.getLogger("truma")


# --------------------------------------------------------------- Konfiguration

class ConfigError(Exception):
    pass


class Sensor:
    def __init__(self, key, sec, index):
        self.key = key
        self.mac = sec.get("mac", "").strip().upper()
        if not MAC_RE.match(self.mac):
            raise ConfigError("[%s]: mac= fehlt oder ist ungueltig (%r)" % (key, self.mac))
        self.name = sec.get("name", "Gasflasche %d" % (index + 1))
        self.bottle_kg = sec.getfloat("bottle_kg", 11.0)
        self.instance = sec.getint("device_instance", 20 + index)
        self.macid = self.mac.replace(":", "").lower()


class Config:
    def __init__(self, path):
        cp = configparser.ConfigParser()
        cp.read_dict({"general": {}})
        if not cp.read(path, encoding="utf-8"):
            raise ConfigError("Konfiguration nicht gefunden: %s" % path)
        g = cp["general"]
        self.adapter = g.get("adapter", "hci0")
        self.poll_interval = g.getint("poll_interval", 600)
        self.retry_interval = g.getint("retry_interval", 120)
        self.use_monitor = g.getboolean("use_monitor", True)
        self.advert_wait = g.getint("advert_wait", 300)
        self.hinted_connect_window = g.getint("hinted_connect_window", 45)
        self.connect_window = g.getint("connect_window", 60)
        self.allow_discovery = g.getboolean("allow_discovery", True)
        self.discovery_timeout = g.getint("discovery_timeout", 240)
        self.discovery_backoff = g.getint("discovery_backoff", 3600)
        self.lpg_density = g.getfloat("lpg_density", 0.51)
        self.stale_after = g.getint("stale_after", 3600)
        self.log_level = g.get("log_level", "info")
        names = [s for s in cp.sections() if s.lower().startswith("sensor")]
        self.sensors = [Sensor(n, cp[n], i) for i, n in enumerate(names)]
        if not self.sensors:
            raise ConfigError("kein [sensor...]-Abschnitt in %s" % path)


def adapter_index(name):
    m = re.match(r"^hci(\d+)$", name)
    return int(m.group(1)) if m else None


def blocking_bus():
    """Eigene System-Bus-Verbindung ohne Mainloop (nur blockierende Aufrufe)."""
    return dbus.SystemBus(private=True, mainloop=dbus.mainloop.NULL_MAIN_LOOP)


def start_monitor(cfg):
    if not cfg.use_monitor:
        return None
    mon = AdvertMonitor([s.mac for s in cfg.sensors], adapter_index(cfg.adapter))
    if not mon.open():
        return None
    mon.start()
    return mon


# -------------------------------------------------------------------- Messung

def read_sensor(adapter, sensor, cfg, monitor=None, stop=None, track=None, may_discover=True):
    """Eine Messung: (Discovery) -> auf Advertisement warten -> verbinden ->
    Messung ausloesen -> lesen -> trennen."""
    stop = stop or threading.Event()
    if not adapter.powered():
        raise BleError("Bluetooth-Adapter %s ist aus oder fehlt" % cfg.adapter)

    if not adapter.device_known(sensor.mac):
        if not (cfg.allow_discovery and may_discover):
            raise DeviceUnknown("bluetoothd kennt %s (noch) nicht" % sensor.mac)
        log.info("%s ist bluetoothd unbekannt, kurze Discovery", sensor.mac)
        adapter.discover(cfg.discovery_timeout,
                         done=lambda: adapter.device_known(sensor.mac), stop=stop)
        if not adapter.device_known(sensor.mac):
            raise DeviceUnknown("%s bei der Discovery nicht gefunden" % sensor.mac)

    window = cfg.connect_window
    if monitor is not None and monitor.active:
        if monitor.wait_for_advert(sensor.mac, cfg.advert_wait, stop):
            window = cfg.hinted_connect_window
            log.debug("%s advertised gerade, verbinde", sensor.mac)
        else:
            log.debug("%s: kein Advertisement in %d s gesehen", sensor.mac, cfg.advert_wait)
    if stop.is_set():
        raise BleError("abgebrochen")
    if window <= 0:
        raise BleError("kein Advertisement gesehen und connect_window=0")

    dev = GattDevice(adapter, sensor.mac)
    if track:
        track(dev)
    try:
        dev.connect(window, stop)
        chars = dev.find_characteristics(
            (tp.UUID_PASSWORD, tp.UUID_STORE_NV, tp.UUID_MEASUREMENT))
        password = dev.read(chars[tp.UUID_PASSWORD])
        # Frische Messung erzwingen: init (0x03), dann trigger (0x02)
        for value in (tp.TRIGGER_INIT, tp.TRIGGER_GO):
            try:
                dev.write_command(chars[tp.UUID_STORE_NV], bytes([value]))
            except dbus.DBusException as err:
                log.debug("%s: Trigger 0x%02x fehlgeschlagen: %s", sensor.mac, value, err)
            time.sleep(tp.TRIGGER_GAP_S)
        time.sleep(tp.MEASURE_WAIT_S)
        raw = dev.read(chars[tp.UUID_MEASUREMENT])
        reading = tp.decode_measurement(password, raw)
        log.debug("%s: roh=%s entschluesselt=%s", sensor.mac, raw.hex(), reading.raw)
        return reading
    finally:
        dev.disconnect()
        if track:
            track(None)


def idle(fn, *args):
    from gi.repository import GLib

    def _cb():
        fn(*args)
        return False
    GLib.idle_add(_cb)


class Worker(threading.Thread):
    """Liest alle Sensoren nacheinander; Ergebnisse gehen per idle_add an den Mainloop."""

    def __init__(self, cfg, on_reading, on_error):
        super().__init__(name="ble-worker", daemon=True)
        self.cfg = cfg
        self.on_reading = on_reading
        self.on_error = on_error
        self.stop = threading.Event()
        self._lock = threading.Lock()
        self._current = None

    def _track(self, dev):
        with self._lock:
            self._current = dev

    def run(self):
        cfg = self.cfg
        adapter = BluezAdapter(blocking_bus(), cfg.adapter)
        monitor = start_monitor(cfg)
        due = {s.mac: 0.0 for s in cfg.sensors}
        next_discovery = {s.mac: 0.0 for s in cfg.sensors}
        while not self.stop.is_set():
            sensor = min(cfg.sensors, key=lambda s: due[s.mac])
            wait = due[sensor.mac] - time.monotonic()
            if wait > 0:
                self.stop.wait(wait)
                continue
            may_discover = time.monotonic() >= next_discovery[sensor.mac]
            try:
                reading = read_sensor(adapter, sensor, cfg, monitor, self.stop,
                                      self._track, may_discover)
            except (BleError, dbus.DBusException, ValueError) as err:
                if isinstance(err, DeviceUnknown) and may_discover:
                    next_discovery[sensor.mac] = time.monotonic() + cfg.discovery_backoff
                if not self.stop.is_set():
                    log.warning("%s: keine Messung: %s", sensor.name, err)
                due[sensor.mac] = time.monotonic() + cfg.retry_interval
                idle(self.on_error, sensor)
                continue
            except Exception:  # Programmierfehler sichtbar machen, aber weiterlaufen
                log.exception("%s: unerwarteter Fehler", sensor.name)
                due[sensor.mac] = time.monotonic() + cfg.retry_interval
                continue
            log.info("%s: %d %% / %.1f kg, Batterie %d %%, %d C, Qualitaet %d",
                     sensor.name, reading.percent, reading.kg, reading.battery,
                     reading.temperature, reading.quality)
            due[sensor.mac] = time.monotonic() + cfg.poll_interval
            idle(self.on_reading, sensor, reading)

    def shutdown(self):
        self.stop.set()
        with self._lock:
            dev = self._current
        if dev is not None:
            dev.disconnect()  # bricht auch einen laufenden Connect ab


# --------------------------------------------------------------- Venus D-Bus

def _fmt(pattern):
    def cb(path, value):
        return "" if value is None else pattern % value
    return cb


class TankService:
    """Ein com.victronenergy.tank-Service pro Sensor (wird erst nach der
    ersten erfolgreichen Messung registriert)."""

    def __init__(self, sensor, cfg):
        from settingsdevice import SettingsDevice
        self.sensor = sensor
        self.cfg = cfg
        self.svc = None
        self.last_success = None
        self.bus = dbus.SystemBus(private=True)
        base = "/Settings/Devices/truma_%s" % sensor.macid
        self.settings = SettingsDevice(self.bus, {
            "instance": [base + "/ClassAndVrmInstance", "tank:%d" % sensor.instance, 0, 0],
            "customname": [base + "/CustomName", sensor.name, 0, 0],
        }, self._on_setting)
        self.servicename = "com.victronenergy.tank.truma_" + sensor.macid

    def _instance(self):
        try:
            return int(str(self.settings["instance"]).split(":")[1])
        except (IndexError, ValueError):
            return self.sensor.instance

    def _on_setting(self, setting, old, new):
        if self.svc is None:
            return
        if setting == "instance":
            self.svc["/DeviceInstance"] = self._instance()
        elif setting == "customname":
            self.svc["/CustomName"] = new

    def _on_customname(self, path, value):
        self.settings["customname"] = value
        return True

    def _register(self):
        from vedbus import VeDbusService
        try:
            svc = VeDbusService(self.servicename, bus=self.bus, register=False)
            deferred = True
        except TypeError:  # aeltere velib_python ohne register-Parameter
            svc = VeDbusService(self.servicename, bus=self.bus)
            deferred = False
        svc.add_mandatory_paths(__file__, VERSION, "Bluetooth LE " + self.sensor.mac,
                                self._instance(), 0xFFFF, "Truma LevelControl",
                                "-", "-", 1)
        svc.add_path("/CustomName", str(self.settings["customname"]), writeable=True,
                     onchangecallback=self._on_customname)
        svc.add_path("/FluidType", FLUID_TYPE_LPG)
        svc.add_path("/Status", STATUS_OK)
        svc.add_path("/Level", None, gettextcallback=_fmt("%.0f %%"))
        svc.add_path("/Capacity", None, gettextcallback=_fmt("%.4f m3"))
        svc.add_path("/Remaining", None, gettextcallback=_fmt("%.4f m3"))
        svc.add_path("/Temperature", None, gettextcallback=_fmt("%.0f C"))
        svc.add_path("/Truma/GasKg", None, gettextcallback=_fmt("%.1f kg"))
        svc.add_path("/Truma/BatteryPercent", None, gettextcallback=_fmt("%.0f %%"))
        svc.add_path("/Truma/Quality", None)
        svc.add_path("/Truma/LastReading", None)
        svc.add_path("/Truma/Raw", None)
        if deferred:
            svc.register()
        self.svc = svc
        log.info("%s registriert (Instanz %d)", self.servicename, self._instance())

    def update(self, r):
        if self.svc is None:
            self._register()
        rho = self.cfg.lpg_density  # kg/l fluessig
        s = self.svc
        s["/Level"] = r.percent
        s["/Capacity"] = round(self.sensor.bottle_kg / rho / 1000.0, 5)
        s["/Remaining"] = round(r.kg / rho / 1000.0, 5)
        s["/Temperature"] = r.temperature
        s["/Truma/GasKg"] = round(r.kg, 1)
        s["/Truma/BatteryPercent"] = r.battery
        s["/Truma/Quality"] = r.quality
        s["/Truma/LastReading"] = int(time.time())
        s["/Truma/Raw"] = r.raw
        s["/Status"] = STATUS_OK
        self.last_success = time.monotonic()

    def check_stale(self):
        if self.svc is None or self.last_success is None:
            return
        stale = time.monotonic() - self.last_success > self.cfg.stale_after
        want = STATUS_DISCONNECTED if stale else STATUS_OK
        if self.svc["/Status"] != want:
            self.svc["/Status"] = want


def run_service(cfg):
    import dbus.mainloop.glib
    from gi.repository import GLib

    dbus.mainloop.glib.threads_init()
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

    services = {s.mac: TankService(s, cfg) for s in cfg.sensors}
    worker = Worker(cfg,
                    on_reading=lambda s, r: services[s.mac].update(r),
                    on_error=lambda s: services[s.mac].check_stale())
    mainloop = GLib.MainLoop()

    def tick():
        for svc in services.values():
            svc.check_stale()
        return True

    def on_signal(*_):
        log.info("Signal empfangen, beende")
        mainloop.quit()
        return False

    GLib.timeout_add_seconds(60, tick)
    for sig in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_HIGH, sig, on_signal)

    log.info("dbus-truma-levelcontrol %s, %d Sensor(en), Adapter %s",
             VERSION, len(cfg.sensors), cfg.adapter)
    worker.start()
    try:
        mainloop.run()
    finally:
        worker.shutdown()
        worker.join(timeout=15)


# ------------------------------------------------------------------------ CLI

def cmd_find(adapter_name, seconds):
    adapter = BluezAdapter(blocking_bus(), adapter_name)
    if not adapter.powered():
        print("Adapter %s ist aus oder fehlt." % adapter_name)
        return 1
    print("Suche %d s nach Truma LevelControl (Hersteller-ID 0x%04X) auf %s ..."
          % (seconds, tp.MANUFACTURER_ID, adapter_name))
    print("Der Sensor advertised nur alle paar Minuten, also Geduld.", flush=True)
    seen = set()

    def progress():
        for addr, name, rssi in adapter.devices_with_manufacturer(tp.MANUFACTURER_ID):
            if addr not in seen:
                seen.add(addr)
                print("  gefunden: %s  %s  RSSI %s" % (addr, name or "-",
                      rssi if rssi is not None else "?"), flush=True)
        return False

    adapter.discover(seconds, done=progress)
    if not seen:
        print("Nichts gefunden. Truma-App geschlossen? Sensor in Reichweite?")
        return 1
    print("MAC in config.ini unter [sensor1] eintragen.")
    return 0


def cmd_once(cfg):
    adapter = BluezAdapter(blocking_bus(), cfg.adapter)
    monitor = start_monitor(cfg)
    rc = 0
    for s in cfg.sensors:
        try:
            r = read_sensor(adapter, s, cfg, monitor)
        except (BleError, dbus.DBusException, ValueError) as err:
            print("%s (%s): FEHLER: %s" % (s.name, s.mac, err))
            rc = 1
            continue
        print("%s (%s): %d %%, %.1f kg, Batterie %d %%, %d C, Qualitaet %d, roh %s"
              % (s.name, s.mac, r.percent, r.kg, r.battery, r.temperature, r.quality, r.raw))
    return rc


def setup_logging(level):
    logging.basicConfig(level=getattr(logging, str(level).upper(), logging.INFO),
                        format="%(levelname)s %(name)s: %(message)s")


def main():
    ap = argparse.ArgumentParser(description="Truma LevelControl fuer Venus OS")
    ap.add_argument("-c", "--config", default=os.path.join(HERE, "config.ini"))
    ap.add_argument("--find", nargs="?", const=300, type=int, metavar="SEKUNDEN",
                    help="nach Sensoren suchen (Standard 300 s)")
    ap.add_argument("--once", action="store_true",
                    help="alle Sensoren einmal lesen und ausgeben (Dienst vorher stoppen)")
    ap.add_argument("--adapter", help="Adapter ueberschreiben, z. B. hci1")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    if args.find is not None:
        setup_logging("debug" if args.debug else "info")
        return cmd_find(args.adapter or "hci0", args.find)

    try:
        cfg = Config(args.config)
    except ConfigError as err:
        setup_logging("info")
        log.error("%s", err)
        if not args.once:
            time.sleep(300)  # nicht im Sekundentakt von daemontools neu starten
        return 1
    if args.adapter:
        cfg.adapter = args.adapter
    setup_logging("debug" if args.debug else cfg.log_level)

    if args.once:
        return cmd_once(cfg)
    run_service(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
