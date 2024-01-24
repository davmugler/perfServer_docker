# Verwenden Sie ein offizielles Python-Image als Basis
FROM python:3.8

# Setzen Sie das Arbeitsverzeichnis im Container
WORKDIR /app

# Kopieren Sie die lokale Anwendung in das Container-Arbeitsverzeichnis
COPY ./app /app

# Installieren Sie die erforderlichen Python-Abhängigkeiten
RUN pip install -r requirements.txt

# Öffnen Sie den Port, auf dem der Server lauschen soll
EXPOSE 1883

# Geben Sie den Befehl an, um Ihre Anwendung auszuführen
CMD ["python", "perfServer.py"]