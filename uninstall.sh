#!/bin/sh
BASE=/data/dbus-truma-levelcontrol
SVC=/service/dbus-truma-levelcontrol
if [ -e "$SVC" ]; then
    svc -dx "$SVC" "$SVC/log" 2>/dev/null
    rm -f "$SVC"
fi
if [ -f /data/rc.local ]; then
    grep -vF "$BASE/service" /data/rc.local > /tmp/rc.local.$$
    cat /tmp/rc.local.$$ > /data/rc.local
    rm -f /tmp/rc.local.$$
fi
echo ">> Dienst entfernt. Dateien liegen weiter in $BASE."
