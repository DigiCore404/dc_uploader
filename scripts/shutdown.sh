#!/bin/bash

set -e

if screen -list dc-uploader | grep -q "No Sockets found"; then
    echo "No web app sessions found, nothing to shutdown."
    exit 0
else
    echo "Gracefully shutting down dc-uploader"
    screen -S dc-uploader -X stuff $'\003'
fi