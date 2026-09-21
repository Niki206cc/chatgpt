FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /data /attachments

EXPOSE 8080

# Le generazioni Ollama locali, soprattutto con fonti lunghe, possono richiedere
# diversi minuti. Il timeout predefinito di Gunicorn (30 s) terminava il worker
# durante la generazione causando "Internal Server Error" nel browser.\n# Due worker permettono al browser di interrogare il monitor di avanzamento\n# mentre l'altro worker resta occupato nella chiamata Ollama.
CMD ["gunicorn", "-w", "1", "--threads", "4", "--timeout", "600", "--graceful-timeout", "30", "-b", "0.0.0.0:8080", "quality_patch:app"]
