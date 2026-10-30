<p align="center">
  <img src="Truma-Victron-Banner.png" alt="Truma LevelControl für Victron Energy" width="100%">
</p>

<h1 align="center">🔥 dbus-truma-level-control</h1>
<h3 align="center">Truma LevelControl als LPG-Tanksensor in Victron Venus OS – direkt über BlueZ</h3>

<p align="center">
  <img src="https://img.shields.io/badge/Platform-Victron%20Venus%20OS-0089D0" alt="Venus OS">
  <img src="https://img.shields.io/badge/Interface-Bluetooth%20LE-0075A9" alt="Bluetooth LE">
  <img src="https://img.shields.io/badge/Integration-D--Bus-orange" alt="D-Bus">
  <img src="https://img.shields.io/badge/Status-Beta%20%2F%20Testing-yellow" alt="Beta / Testing">
  <img src="https://img.shields.io/badge/Dependencies-No%20pip-brightgreen" alt="No pip">
</p>

<p align="center">
  <strong>🇩🇪 Deutsch</strong> · <a href="README_EN.md">🇬🇧 English</a>
</p>

---

## 🚧 Beta – Tester und Ideen willkommen!

**Das Projekt befindet sich noch in der Testphase.** Der Bluetooth-Stack von Venus OS ist nicht immer einfach zu handhaben. Teste gern mit und teile Fehlerberichte, Erfahrungen oder Ideen!

Langfristig sollen sich weitere Bluetooth-Sensoren und -Geräte **nativ in Venus OS** anbinden und steuern lassen – ohne Node-RED und ähnliche Umwege. Diese Erweiterung ist ein Ziel, noch keine vorhandene Funktion.

> ⚠️ Experimentelle Software: Bitte zunächst mit unkritischen Setups testen. Bestehende Bluetooth-Sensoren auf dem GX-Gerät im Auge behalten.

## 🚀 Über das Projekt

**dbus-truma-level-control** bindet Truma LevelControl als **LPG-Tank** auf Victron-GX-Geräten mit **Venus OS** ein. Der Dienst verwendet den bereits vorhandenen Bluetooth-Stack (`bluetoothd` / BlueZ): **kein bleak, kein pip, keine zusätzlichen Python-Pakete**.

Die Messwerte erscheinen über einen regulären Tank-D-Bus-Dienst und können in der lokalen GUI, Remote Console sowie über die jeweiligen Schnittstellen von Venus OS genutzt werden.

Das zugrunde liegende Protokoll ist hier dokumentiert: [cyclemat/HAOS-Truma-Levelcontrol-Integration](https://github.com/cyclemat/HAOS-Truma-Levelcontrol-Integration).

## ✨ Funktionen und Architektur

- 🔵 **Native BlueZ-Anbindung:** Verbindungsaufbau, GATT-Zugriffe und gegebenenfalls Discovery über die BlueZ-D-Bus-API; keine rohen HCI-Steuerbefehle und keine Änderungen an Adapter-Einstellungen.
- 📡 **Advertisement-gesteuert:** Der HCI-Monitor-Kanal wird nur lesend beobachtet (ähnlich `btmon`), um den passenden Verbindungszeitpunkt zu erkennen.
- ⏱️ **Kurze Verbindungen:** Verbinden → Messung auslösen → auslesen → trennen. Die Verbindung bleibt nur wenige Sekunden bestehen.
- 🧴 **Mehrere Gasflaschen:** Sensoren werden nacheinander, nicht gleichzeitig, ausgelesen.
- 🔎 **Discovery nur bei Bedarf:** Wenn `bluetoothd` den Sensor noch nicht kennt; nach einem Fehlschlag mit Backoff.
- ⚡ **Normale Venus-OS-Tankintegration:** `com.victronenergy.tank.truma_<mac>` mit FluidType LPG für GUI, VRM, MQTT und Modbus, soweit die jeweilige Venus-OS-Konfiguration diese bereitstellt.

## 📋 Voraussetzungen

- Ein Victron-GX-Gerät mit Venus OS und funktionierendem Bluetooth/BlueZ.
- Root-Zugang per SSH.
- Ein Truma LevelControl-Sensor, der für die Einrichtung erreichbar ist.
- Die Truma-App darf während einer Messung keine Verbindung zum Sensor halten.

## 📦 Installation

Die folgenden Befehle setzen voraus, dass du das Projektarchiv `dbus-truma-levelcontrol.tar.gz` bereits auf deinem Rechner hast.

```sh
# Auf deinem Rechner: Archiv auf das GX-Gerät kopieren
scp dbus-truma-levelcontrol.tar.gz root@<gx-ip>:/data/

# Auf dem GX-Gerät: entpacken und konfigurieren
cd /data && tar xzf dbus-truma-levelcontrol.tar.gz
cd /data/dbus-truma-levelcontrol
cp config.example.ini config.ini

# Sensor finden (bis zu 5 Minuten, Truma-App schließen)
python3 dbus_truma_levelcontrol.py --find

# MAC-Adresse unter [sensor1] eintragen
vi config.ini

# Einmalige Testmessung
python3 dbus_truma_levelcontrol.py --once --debug

# Dauerhaften Dienst installieren
sh install.sh
```

`install.sh` richtet den Dienst so ein, dass er auch nach regulären Venus-OS-Updates wieder gestartet werden kann.

### 🛠️ Wartung und Diagnose

| Aktion | Befehl |
| --- | --- |
| Log live verfolgen | `tail -F /var/log/dbus-truma-levelcontrol/current \| tai64nlocal` |
| Dienst stoppen | `svc -d /service/dbus-truma-levelcontrol` |
| Dienst starten | `svc -u /service/dbus-truma-levelcontrol` |
| Dienst neu starten | `svc -t /service/dbus-truma-levelcontrol` |
| Einmalig messen | `python3 dbus_truma_levelcontrol.py --once --debug` |
| Deinstallieren | `sh uninstall.sh` |

**Wichtig:** Vor `--once` den laufenden Dienst stoppen. Der Sensor lässt jeweils nur **eine Verbindung gleichzeitig** zu.

## ⚙️ Konfiguration

Alle Optionen sind in `config.example.ini` erläutert. Bearbeite deine eigene `config.ini`.

- Unter `[sensor1]` die MAC-Adresse des ersten Sensors eintragen.
- Für eine zweite Gasflasche einen Abschnitt `[sensor2]` ergänzen.
- Bei Konflikten mit anderen BLE-Sensoren die Verbindungsfenster gemäß dem folgenden Abschnitt anpassen.

## 📶 Verträglichkeit mit anderen Bluetooth-Sensoren testen

Ob andere BLE-Geräte beeinflusst werden, lässt sich nur auf dem tatsächlichen GX-Gerät mit der installierten Venus-OS-Version zuverlässig beurteilen.

1. Andere BLE-Sensoren (z. B. Ruuvi, Mopeka oder SmartShunt-Instant-Readout) aktiv lassen und ihre Aktualisierungen in der GUI bzw. über `dbus -y` beobachten.
2. `dbus-truma-level-control` laufen lassen und parallel mit `btmon` mitschneiden.
3. Auf `LE Set Scan Enable` und `LE Set Scan Parameters` achten und prüfen, ob die anderen Sensoren weiterhin aktuelle Werte liefern.

Wenn andere Sensoren aussetzen, können diese Einstellungen helfen:

```ini
connect_window = 0
hinted_connect_window = 25
```

`connect_window = 0` beschränkt den Verbindungsversuch auf den Moment eines beobachteten Advertisements. Ein kleineres `hinted_connect_window` reduziert das entsprechende Zeitfenster. **Bitte auf deinem System testen; das ist keine Garantie gegen Bluetooth-Konflikte.**

## 🔌 D-Bus-Integration

Je Sensor wird ein Dienst nach dem Muster `com.victronenergy.tank.truma_<mac>` bereitgestellt.

| D-Bus-Pfad | Bedeutung |
| --- | --- |
| `/Level` | Füllstand in % laut Sensor |
| `/Remaining` | Verbleibendes Volumen in m³ (aus kg und `lpg_density` berechnet) |
| `/Capacity` | Tankvolumen in m³ (aus kg und `lpg_density` berechnet) |
| `/FluidType` | `8` = LPG |
| `/Temperature` | Temperatur in °C |
| `/Status` | `0` = OK, `1` = seit mehr als `stale_after` keine neue Messung |
| `/Truma/GasKg` | Gasmenge in kg |
| `/Truma/BatteryPercent` | Batterie des Sensors in % |
| `/Truma/Quality` | Messqualität von 0 bis 3 |
| `/Truma/LastReading` | Unix-Zeitstempel der letzten Messung |
| `/Truma/Raw` | Entschlüsselte Rohbytes (Debug) |

## ⚠️ Bekannte Einschränkungen

- **Nur eine Verbindung:** Truma-App während der Messung schließen; keinen parallelen `--once`-Aufruf bei laufendem Dienst starten.
- **`vesmart-server`:** Victrons BLE-Dienst für VictronConnect kann fremde BLE-Verbindungen trennen. Dazu existiert das Community-Projekt [TechBlueprints/victron-bluetooth-safety](https://github.com/TechBlueprints/victron-bluetooth-safety). Externe Patches bitte eigenständig prüfen.
- **Temperatur:** Wird als vorzeichenbehaftetes Byte interpretiert; Messungen bei Minusgraden müssen noch verifiziert werden.
- **Nach einem Neustart:** Ist der Sensor `bluetoothd` unbekannt, wird eine kurze Discovery gestartet.
- **Beta:** Bluetooth-Verhalten kann je nach Venus-OS-Version und GX-Hardware variieren.

## 🧩 Protokoll & verwandte Projekte

Die Grundlagen des Truma-LevelControl-Protokolls stammen aus dem Projekt [HAOS-Truma-Levelcontrol-Integration](https://github.com/cyclemat/HAOS-Truma-Levelcontrol-Integration).

## 🛠️ Feedback & Beiträge

Fehlerberichte, Tests auf verschiedenen GX-Geräten und Vorschläge sind willkommen. Bitte bei Bluetooth-Problemen möglichst die Venus-OS-Version, GX-Hardware, verwendete Sensoren und relevante (von persönlichen Daten bereinigte) Logs angeben.

- [Issues eröffnen](https://github.com/cyclemat)
- [CycleMat auf GitHub](https://github.com/cyclemat)

## ❤️ Support & Spenden

Das Projekt entsteht in der Freizeit und kann kostenlos genutzt werden. Wer die Entwicklung unterstützen möchte, kann freiwillig etwas spenden:

**[☕ PayPal – CycleMat unterstützen](https://paypal.me/cyclemat)**

Keine Verpflichtung, keine Paywall – einfach ein offenes Community-Projekt. ❤️

## 📄 Markenhinweis

Dieses unabhängige Community-Projekt steht in keiner offiziellen Verbindung zu Truma oder Victron Energy. Alle Marken gehören ihren jeweiligen Rechteinhabern.

---

<p align="center"><strong>CycleMat · Connecting Bluetooth sensors to Venus OS.</strong></p>
