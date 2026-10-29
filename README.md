# dbus-truma-level-control

Momentan alles Beta Bin noch am Testen der Bluetooth stack von venus os bringt mich um den verstand einfach testen und vieleicht hat ja noch einer eine idee ! Früher oder Später sollen native steuerungen anderer Bluetooth sensoren und geräte möglich werden ohne Nodered und Co

Truma LevelControl als LPG-Tank auf Victron-GX-Geräten (Venus OS), über den originalen Bluetooth-Stack (bluetoothd/BlueZ). Kein bleak, kein pip, nur was Venus OS mitbringt.

Protokoll: [cyclemat/HAOS-Truma-Levelcontrol-Integration](https://github.com/cyclemat/HAOS-Truma-Levelcontrol-Integration).

## So arbeitet der Dienst

- **Nur bluetoothd:** Verbindung, GATT-Zugriffe und eine eventuelle Discovery laufen über die BlueZ-D-Bus-API. Keine rohen HCI-Befehle, keine Änderung an Adapter-Einstellungen.
- **Im richtigen Moment verbinden:** Der Dienst liest über den HCI-Monitor-Kanal (wie `btmon`, rein lesend) mit, wann der Sensor advertised, und verbindet genau dann.
- **Kurze Verbindung:** verbinden, Messung auslösen, lesen, sofort trennen. Die Verbindung steht nur wenige Sekunden.
- **Ein Sensor nach dem anderen:** Mehrere Flaschen werden nacheinander gelesen, nie gleichzeitig.
- **Discovery nur bei Bedarf:** Eine eigene Discovery gibt es nur, wenn bluetoothd den Sensor noch nicht kennt (normalerweise einmalig). Sie endet, sobald der Sensor gefunden ist, und nach einem Fehlschlag folgt ein Backoff.
- **Standard-Tank:** Er erscheint als `com.victronenergy.tank.truma_<mac>` mit FluidType LPG und damit in GUI, VRM, MQTT und Modbus wie jeder andere Tank.

## Installation

Voraussetzung: Root-Zugang per SSH auf dem GX.

```sh
# vom Rechner aus
scp dbus-truma-levelcontrol.tar.gz root@<gx-ip>:/data/
# auf dem GX
cd /data && tar xzf dbus-truma-levelcontrol.tar.gz
cd /data/dbus-truma-levelcontrol
cp config.example.ini config.ini

python3 dbus_truma_levelcontrol.py --find           # bis 5 min, Truma-App schließen
vi config.ini                                       # MAC unter [sensor1] eintragen
python3 dbus_truma_levelcontrol.py --once --debug   # Testmessung
sh install.sh                                       # als Dienst einrichten (überlebt Updates)
```

**Log:** `tail -F /var/log/dbus-truma-levelcontrol/current | tai64nlocal`

**Steuerung:** `svc -d|-u|-t /service/dbus-truma-levelcontrol`

**Entfernen:** `sh uninstall.sh`

Vor einem `--once` den laufenden Dienst stoppen, denn der Sensor erlaubt nur eine Verbindung.

## Gegentest: stört er andere Bluetooth-Sensoren?

Das lässt sich nur auf dem echten GX klären, weil es davon abhängt, wie Victrons eigener Sensor-Dienst in deiner Venus-Version scannt.

1. Andere BLE-Sensoren (Ruuvi, Mopeka, SmartShunt-Instant-Readout …) aktiv lassen und deren Werte beobachten, z. B. mit `dbus -y` oder in der GUI.
2. Dienst laufen lassen und parallel `btmon` mitschneiden.
3. Beim Verbinden auf `LE Set Scan Enable` und `LE Set Scan Parameters` achten. Außerdem prüfen, ob die anderen Sensoren danach weiter aktualisieren.

Falls andere Sensoren aussetzen, gibt es zwei Stellschrauben:

- `connect_window = 0` setzen. Dann wird nur noch verbunden, wenn gerade ein Advertisement gesehen wurde.
- `hinted_connect_window` verkleinern, z. B. auf 25.

## Konfiguration

Alle Optionen sind in `config.example.ini` kommentiert. Für eine zweite Flasche einfach `[sensor2]` ergänzen.

## D-Bus-Pfade

| Pfad | Inhalt |
| --- | --- |
| `/Level` | Füllstand in % (vom Sensor) |
| `/Remaining`, `/Capacity` | Volumen in m³ (kg ÷ `lpg_density`) |
| `/FluidType` | 8 = LPG |
| `/Temperature` | °C |
| `/Status` | 0 = ok, 1 = länger als `stale_after` keine Messung |
| `/Truma/GasKg` | Gasmenge in kg |
| `/Truma/BatteryPercent` | Sensorbatterie in % |
| `/Truma/Quality` | Messqualität 0–3 |
| `/Truma/LastReading` | Unix-Zeit der letzten Messung |
| `/Truma/Raw` | entschlüsselte Rohbytes (Debug) |

## Bekannte Stolpersteine

- **Nur eine Verbindung gleichzeitig.** Die Truma-App muss beim Lesen geschlossen sein.
- **Funkmodul.** Das HA-Projekt empfiehlt einen RTL8761B-USB-Dongle. Andere berichten von GATT-Problemen mit dem eingebauten Bluetooth des Cerbo.
- **vesmart-server.** Victrons BLE-Dienst für VictronConnect kann fremde BLE-Verbindungen trennen. Dafür gibt es einen Community-Patch (TechBlueprints/victron-bluetooth-safety).
- **Temperatur.** Sie wird als vorzeichenbehaftetes Byte gelesen. Das ist eine Annahme und bei Minusgraden gegenzuprüfen.
- **Nach einem Neustart.** Kennt bluetoothd den Sensor dann nicht mehr, führt der Dienst einmal eine kurze Discovery durch.
