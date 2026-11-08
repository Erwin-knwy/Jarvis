#!/usr/bin/env bash
# Builds the tiny native helpers Jarvis uses for Bluetooth and screen brightness (macOS only).
set -e
cd "$(dirname "$0")"
mkdir -p bin
clang -O2 -fobjc-arc -framework Foundation -framework IOBluetooth bluetooth.m -o bin/jarvis-bluetooth
clang -O2 -framework CoreGraphics -framework IOKit -framework CoreFoundation brightness.c -o bin/jarvis-brightness
echo "built: $(ls bin | tr '\n' ' ')"
