#!/bin/sh
# Richtet dbus-truma-levelcontrol als Dienst auf Venus OS ein.
set -e
BASE=/data/dbus-truma-levelcontrol
SVC=/service/dbus-truma-levelcontrol
LINE="ln -sfn $BASE/service $SVC"

cd "$BASE"
chmod +x dbus_truma_levelcontrol.py install.sh uninstall.sh service/run service/log/run

if [ ! -f config.ini ]; then
    cp config.example.ini config.ini
    echo ">> config.ini angelegt. Bitte MAC eintragen, Sensor suchen mit:"
    echo "   python3 $BASE/dbus_truma_levelcontrol.py --find"
fi

# /service wird bei jedem Boot neu angelegt -> Symlink ueber /data/rc.local
if [ ! -f /data/rc.local ]; then
    printf '#!/bin/sh\n' > /data/rc.local
fi
chmod +x /data/rc.local
grep -qxF "$LINE" /data/rc.local || echo "$LINE" >> /data/rc.local

ln -sfn "$BASE/service" "$SVC"
echo ">> Dienst eingerichtet. Log:"
echo "   tail -F /var/log/dbus-truma-levelcontrol/current | tai64nlocal"
