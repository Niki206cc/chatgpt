# Automazione articoli Montagne & Paesi - Portainer Raspberry

Dashboard web Flask per gestire comunicati stampa da email, generare articoli con AI e inviarli alla redazione.

## Avvio con Portainer

1. Apri Portainer.
2. Vai su **Stacks**.
3. Clicca **Add stack**.
4. Scegli **Repository**.
5. Inserisci:

```text
https://github.com/Niki206cc/chatgpt.git
```

6. Nel campo Compose path inserisci:

```text
docker-compose.yml
```

7. Clicca **Deploy the stack**.

La dashboard sarà disponibile su:

```text
http://IP_DEL_RASPBERRY:8088
```

## Funzioni incluse

- configurazione mail IMAP/POP3 salvata
- configurazione SMTP salvata
- Gemini/OpenAI API key salvate
- lettura mail
- apertura comunicati
- salvataggio allegati PDF, DOCX, TXT e immagini
- generazione articolo HTML per WordPress
- invio email finale
- conferma opzionale al mittente
- pulsante per svuotare la cartella allegati

## Cartelle persistenti

Il compose crea due volumi Docker:

- `automazione_articoli_data`: configurazione e log
- `automazione_articoli_attachments`: allegati scaricati

## Accesso remoto

Per usarlo fuori casa, pubblica la porta 8088 con cautela oppure, meglio, usa Cloudflare Tunnel, Tailscale o VPN. Non esporre la dashboard direttamente su Internet senza protezione.
