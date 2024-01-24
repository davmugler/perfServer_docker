# PerfServer Docker Container

Dieses Projekt enthält eine einfache Anwendung namens `perfServer.py`, die einen MQTT-Server implementiert. Der Docker-Container ermöglicht eine einfache Bereitstellung und Ausführung der Anwendung.

## Voraussetzungen

- [Docker](https://www.docker.com/get-started) muss auf Ihrem System installiert sein.

## Installation und Ausführung

1. **Docker-Image erstellen:**

   Navigieren Sie zum Verzeichnis, das dieses README.md enthält, und führen Sie den folgenden Befehl aus, um das Docker-Image zu erstellen:

   ```bash
   docker build -t perf-server .
