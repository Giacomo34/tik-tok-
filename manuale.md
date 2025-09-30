# 📘 Manuale operativo – Piattaforma SaaS per Live Autonomi su TikTok

> **Obiettivo**: costruire e vendere una piattaforma (SaaS) che consente a creator/brand di fare **live autonomi** su TikTok: l’host virtuale legge la chat, parla con TTS, mantiene l’engagement e fornisce analytics. Questa guida copre ogni passaggio dall’ambiente di sviluppo al deploy, incluse note legali/ToS.

---

## 0) Avvertenze importanti

* **Policy/ToS**: l’ingest della chat con librerie non ufficiali può rompersi o violare policy se usato impropriamente. Mantieni disclosure chiara ("host virtuale"), evita automazioni ingannevoli o spam. Preparati a spegnere subito la live in caso di abusi.
* **Trasparenza**: mostra un badge "Virtual Host / AI" nell’overlay.
* **GDPR**: logga il minimo indispensabile, anonimizza dove possibile, gestisci i diritti degli utenti (accesso/cancellazione dati).

---

## 1) Materiale necessario

**Hardware**

* Laptop/PC per sviluppo.
* (Opzionale) VM/GPU per modalità Managed (encoder server-side).

**Software & Tool**

* VS Code / JetBrains.
* Docker + Docker Compose.
* OBS Studio (test e Self‑hosted).
* Postgres, Redis.
* GitHub/GitLab + CI/CD.
* Stripe (billing), Auth (Clerk/Auth0 o NextAuth).
* LLM (es. OpenAI GPT‑4o mini), TTS (es. ElevenLabs / Azure TTS).

**Organizzazione**

* Monorepo (frontend + backend + infra).
* Gestione segreti (Vault/Doppler/.env cifrati).
* Documentazione legale (Privacy, ToS, DPA con provider).

---

## 2) Architettura di riferimento

```
[Browser Utente]
   │ HTTPS
   ▼
[Frontend (Next.js)]  ———  [Overlay web]
   │ REST/WS (JWT)
   ▼
[API/BFF]
   ├─ Auth (Clerk/Auth0)
   ├─ Billing (Stripe)
   ├─ Orchestrator “Live Engine”
   │    ├─ Ingest Chat (Worker)
   │    ├─ Policy & Moderation
   │    ├─ Planner (LLM)
   │    └─ TTS (stream)
   ├─ DB (Postgres)
   ├─ Cache/Queue (Redis)
   └─ Storage (S3/Blob: log, audio temp)

[Encoder]
   ├─ Self‑hosted: OBS dell’utente (Browser Source = overlay)
   └─ Managed: container OBS/ffmpeg che pusha RTMP a TikTok
```

---

## 3) Setup progetto (monorepo)

**Struttura directory**

```
/apps
  /frontend  (Next.js + Tailwind + shadcn)
  /backend   (FastAPI o NestJS)
  /worker    (Python/Node: ingest chat + planner)
/infra
  docker-compose.yml
  terraform/ (se usi IaC)
/packages
  /shared    (tipi, utils)
```

**docker-compose.yml (base)**

```yaml
version: "3.9"
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_PASSWORD: postgres
    ports: ["5432:5432"]
    volumes: ["db:/var/lib/postgresql/data"]

  redis:
    image: redis:7
    ports: ["6379:6379"]

  backend:
    build: ./apps/backend
    env_file: .env
    depends_on: [db, redis]
    ports: ["8000:8000"]

  frontend:
    build: ./apps/frontend
    env_file: .env
    depends_on: [backend]
    ports: ["3000:3000"]

  worker:
    build: ./apps/worker
    env_file: .env
    depends_on: [redis, backend]

volumes:
  db:
```

**Variabili d’ambiente (.env esempio)**

```
DATABASE_URL=postgresql://postgres:postgres@db:5432/postgres
REDIS_URL=redis://redis:6379
JWT_SECRET=changeme
OPENAI_API_KEY=...
TTS_PROVIDER=elevenlabs
TTS_API_KEY=...
STRIPE_SECRET=...
STRIPE_WEBHOOK_SECRET=...
AUTH_ISSUER=...
AUTH_AUDIENCE=...
```

---

## 4) Database & multitenancy

**Schema minimo (SQL)**

```sql
create table tenants (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  plan text not null check (plan in ('self','managed')),
  created_at timestamptz default now()
);

create table users (
  id uuid primary key default gen_random_uuid(),
  email text unique not null,
  role text not null check (role in ('owner','member')),
  tenant_id uuid references tenants(id) on delete cascade,
  created_at timestamptz default now()
);

create table subs (
  tenant_id uuid primary key references tenants(id) on delete cascade,
  stripe_customer_id text,
  stripe_sub_id text,
  plan text,
  status text,
  updated_at timestamptz default now()
);

create table personas (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid references tenants(id),
  name text, system_prompt text, style jsonb, lang text default 'it'
);

create table voice_profiles (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid references tenants(id),
  provider text, voice_id text, speed numeric default 1.0, pitch numeric default 0
);

create table policies (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid references tenants(id),
  blocklist jsonb default '[]', safe_mode boolean default true
);

create table live_sessions (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid references tenants(id),
  mode text check (mode in ('self','managed')),
  status text check (status in ('PENDING','ACTIVE','ENDED')),
  overlay_token text,
  started_at timestamptz, ended_at timestamptz
);

create table chat_events (
  id bigserial primary key,
  session_id uuid references live_sessions(id) on delete cascade,
  event jsonb not null,
  ts timestamptz default now()
);

create table metrics_daily (
  tenant_id uuid references tenants(id) on delete cascade,
  date date,
  watch_time int default 0,
  msg_count int default 0,
  gifts_count int default 0,
  avg_latency_ms int default 0,
  primary key (tenant_id, date)
);
```

**Nota**: ogni query applicativa deve filtrare per `tenant_id` (ACL multitenant).

---

## 5) Autenticazione & autorizzazione

1. Scegli provider (Clerk/Auth0/NextAuth). Configura **JWT** con `tenant_id` in `custom claims`.
2. Al primo login: crea `tenant` + collega utente come `owner`.
3. Middleware: rifiuta richieste senza JWT valido; estrae `tenant_id` e lo propaga ai servizi.

---

## 6) Billing con Stripe (abbonamenti)

1. Crea su Stripe due prodotti: **Self‑hosted** e **Managed** (mensili).
2. Frontend: bottone **Abbonati** → Stripe Checkout Session.
3. Webhook `/stripe/webhook`:

   * Su `checkout.session.completed`: salva `subs` (customer_id, sub_id, plan, status=active).
   * Su `invoice.payment_failed` o `customer.subscription.deleted`: aggiorna `status`.
4. Customer Portal: link in dashboard per upgrade/downgrade/cancel.

---

## 7) Backend API (FastAPI esempio)

**Struttura minimale**

```python
# apps/backend/main.py
from fastapi import FastAPI, Depends, WebSocket
from auth import require_user
from sessions import start_session, stop_session
from stripe_webhook import handle_webhook

app = FastAPI()

@app.post('/sessions/start')
def start(user=Depends(require_user)):
    return start_session(user)

@app.post('/sessions/stop')
def stop(user=Depends(require_user)):
    return stop_session(user)

@app.post('/stripe/webhook')
def stripe_webhook(payload: dict):
    return handle_webhook(payload)

@app.websocket('/overlay/ws')
async def overlay_ws(ws: WebSocket):
    await ws.accept()
    # invio chunk audio ai client overlay
```

**Concetti chiave**

* `/sessions/start` crea `live_sessions`, genera `overlay_token` (usa JWT JTI), avvia worker ingest tramite coda/Redis.
* `/overlay/ws` invia audio TTS (chunk) e messaggi di stato all’overlay.

---

## 8) Ingest della chat TikTok (Worker)

> Librerie non ufficiali (Python **TikTokLive** o equivalenti Node). Usale responsabilmente e attenditi rotture.

**Esempio Python (ridotto)**

```python
# apps/worker/ingest.py
from TikTokLive import TikTokLiveClient
from TikTokLive.types.events import CommentEvent, GiftEvent
import json, redis

r = redis.Redis.from_url(os.environ['REDIS_URL'])
client = TikTokLiveClient(unique_id='@handle_utente')

@client.on(CommentEvent)
async def on_comment(ev):
    evt = {"type":"comment","user":ev.user.nickname,"text":ev.comment}
    r.publish('chat_events', json.dumps(evt))

@client.on(GiftEvent)
async def on_gift(ev):
    evt = {"type":"gift","user":ev.user.nickname,"gift":ev.gift.name,
           "count":ev.gift.repeat_count}
    r.publish('chat_events', json.dumps(evt))

client.run()
```

**Normalizzazione eventi**: in un process separato leggi `chat_events` (Redis pub/sub), valida, arricchisci (timestamp, session_id) e salva su `chat_events` (DB) + inoltra al Planner.

---

## 9) Policy & Moderation

* **Blocklist** per parole vietate configurabile per tenant.
* **Classificazione rischio** (low/medium/high). Se `high` → non rispondere, invia messaggio neutro o ignora.
* **Rate limit**: max 1 risposta parlata ogni 3–5s; rallenta quando la chat accelera.
* **Kill‑switch**: endpoint/admin per mettere in **mute** o terminare live.

**Esempio semplice (Python)**

```python
BLOCK = {"odio","insulto1","insulto2"}
COOLDOWN = 4
last_ts = 0

def safe(text: str) -> bool:
    t = text.lower()
    return not any(b in t for b in BLOCK)

def should_talk(now):
    global last_ts
    if now - last_ts < COOLDOWN: return False
    last_ts = now
    return True
```

---

## 10) Planner (LLM) – generazione risposte brevi

**Logica**

1. Router: `gift` → ringrazia; `comment` → rispondi se `safe`; `command` → esegui (es. `!quiz`).
2. Persona: prompt con tono/stile; memoria breve (ultime N interazioni).
3. Output: **≤ 2 frasi**, niente claim rischiosi; CTA ogni 5–7 min.

**Esempio pseudo‑codice**

```python
def plan(evt, memory, persona):
    if evt['type'] == 'gift':
        return f"Grazie {evt['user']} per il regalo {evt['gift']}!"
    if evt['type'] == 'comment' and safe(evt['text']):
        prompt = f"Persona: {persona}. Domanda: {evt['text']}. Rispondi in 1-2 frasi."
        return llm_complete(prompt)
    return None
```

---

## 11) TTS realtime

**Requisiti**

* Latenza P95 < ~1,2s.
* Buffer 300–700ms per evitare tagli parole.
* Normalizzazione loudness (target ~ -16 LUFS) e de‑click.

**Esempio (REST → WAV → chunk WS)**

```python
# pseudo: ottieni bytes WAV dal provider e spezzali in chunk PCM da 200ms
wav = tts_synthesize(text, voice_id)
for chunk in split_into_chunks(wav, 0.2):
    ws.send(chunk)  # websocket verso overlay
```

> Per provider come ElevenLabs/Azure puoi usare modalità streaming per ridurre la latenza. Conserva solo metadati minimi (per GDPR).

---

## 12) Overlay Web (Browser Source in OBS)

**Funzioni**

* Riceve audio TTS via WebSocket e lo riproduce con WebAudio API.
* Mostra chat e badge “Virtual Host”.
* Pulsante **Mute** locale.

**Esempio client (vanilla JS)**

```html
<!doctype html>
<html>
  <body style="margin:0;background:#0b0b0b;color:#fff;font:14px Inter,sans-serif;">
    <div id="chat" style="position:absolute;left:12px;bottom:12px;max-width:40vw"></div>
    <div id="badge" style="position:absolute;right:12px;top:12px;padding:6px 10px;background:#111;border-radius:8px;">Virtual Host</div>
    <script>
      const audioCtx = new (window.AudioContext)();
      const ws = new WebSocket("wss://api.tuo‑dominio/overlay/ws?token=...");
      ws.binaryType = "arraybuffer";
      ws.onmessage = async (ev) => {
        if (typeof ev.data !== 'string') {
          const buf = await audioCtx.decodeAudioData(ev.data);
          const src = audioCtx.createBufferSource();
          src.buffer = buf; src.connect(audioCtx.destination); src.start();
        } else {
          const m = JSON.parse(ev.data);
          if (m.type === 'chat') {
            const el = document.getElementById('chat');
            el.innerHTML = `<div>${m.user}: ${m.text}</div>` + el.innerHTML;
          }
        }
      }
    </script>
  </body>
</html>
```

**Uso in OBS (Self‑hosted)**

* Aggiungi **Browser Source** → URL dell’overlay (es. `https://overlay.tuo‑dominio/?s=SESSION_TOKEN`).
* Imposta risoluzione/trasparenza, riduci il volume delle altre sorgenti se necessario.

---

## 13) Modalità Managed (encoder server‑side)

**Quando serve**: offrire servizio “chiavi in mano”.

**Approccio semplice (ffmpeg)**

* Video: immagine di sfondo/avatar statico o canvas animato headless.
* Audio: stream TTS mixato + musica di sottofondo (royalty‑free!).

**Comando base (esempio)**

```bash
ffmpeg -re \
  -stream_loop -1 -i background.mp4 \
  -i tts_mix.wav \
  -c:v libx264 -preset veryfast -b:v 2500k -pix_fmt yuv420p \
  -c:a aac -b:a 128k -ar 44100 \
  -f flv rtmp://live.tiktok.com/live/STREAM_KEY
```

> Nota: usa un **mixer** audio (es. ffmpeg `amix` o un micro‑servizio) per fondere TTS e musica; gestisci start/stop da API.

**Costi**: considera ~2–4 Mbps per stream (720p/1080p), traffico mensile ~0,6–1,2 TB se 30h/mese.

---

## 14) Analytics & metriche

**Eventi**: conta `comment`, `gift`, risposte pronunciate, latenza TTS.

**Query esempio**

```sql
-- Messaggi e gift per sessione
select session_id,
       sum((event->>'type')='comment')::int as msg,
       sum((event->>'type')='gift')::int as gifts
from chat_events
where ts >= now() - interval '30 days'
group by session_id;

-- Latenza media
select tenant_id, date, avg_latency_ms from metrics_daily order by date desc;
```

**Dashboard**: Grafana/Metabase con viste per tenant.

---

## 15) Sicurezza & GDPR

* **Minimizzazione dati**: non salvare testi sensibili; tronca/anonimizza username (es. hash + salt).
* **DPA** con provider (LLM, TTS, hosting).
* **Registro trattamenti**: elenca finalità, basi giuridiche, retention.
* **Privacy Policy** chiara e link in dashboard/overlay.
* **Incident response**: piano per data breach e log accessi.

---

## 16) Deploy (esempio con Fly.io)

1. Installa `flyctl`, esegui `fly launch` per **backend** e **frontend** (app separate).
2. Configura **Postgres gestito** su Fly o esterno (RDS/Neon).
3. Imposta **segreti**:

   ```bash
   fly secrets set DATABASE_URL=... REDIS_URL=... OPENAI_API_KEY=... TTS_API_KEY=...
   ```
4. Configura **certificati** e domini (CNAME → *.fly.dev o custom domain).
5. Setup **autoscaling** (min 1, max 3) e health checks.
6. CI/CD: GitHub Actions → build & deploy su main.

**Alternative**: Render, Railway, Hetzner (Docker Compose su VPS), o Kubernetes (GKE/EKS) quando cresci.

---

## 17) Qualità, logging e monitoring

* **Observability**: OpenTelemetry → collector → Grafana Tempo/Loki, metriche Prometheus.
* **Error Tracking**: Sentry con release tagging.
* **SLO**: P95 TTS < 1,2s; uptime API ≥ 99,5%.
* **Alerting**: fallimenti TTS/LLM, coda eventi alta, errore Stripe webhook.

---

## 18) Test & collaudo

* **Dry‑run**: 20–30 min su account di test/secondario.
* **Load**: simulatore chat (riproduci 20–50 msg/min) e verifica latenza TTS.
* **Chaos**: disabilita TTS e verifica fallback (messaggio testo + retry).
* **Moderation**: prova termini vietati e escalation.

**Checklist Go‑Live**

* [ ] Badge “Virtual Host” attivo nell’overlay.
* [ ] Kill‑switch funzionante.
* [ ] Webhook Stripe firmati e testati (success/failure).
* [ ] Backup DB (restore provato).
* [ ] Piani e limiti d’uso configurati.

---

## 19) Pricing & unit economics (riassunto)

* **Self‑hosted** (utente usa OBS): costo per te ~€6–12/utente/mese (TTS+infra). Prezzo consigliato: **€29–49**.
* **Managed** (encoder tuo): costo ~€50–70/utente/mese. Prezzo consigliato: **€199–299**.
* Add‑on: voci premium, avatar animato, analytics avanzate.

---

## 20) Roadmap suggerita (4 settimane → MVP)

**Settimana 1**: Auth, Stripe, multitenant DB, skeleton dashboard & overlay.

**Settimana 2**: Worker ingest chat, planner v1, TTS streaming, policy base.

**Settimimana 3**: Analytics base, onboarding wizard, test E2E Self‑hosted.

**Settimana 4**: Hardening (ratelimit, kill‑switch, logs), pagina pricing, beta con 3–5 creator.

---

## 21) Allegati utili (template)

**Persona (IT, energico) – prompt**

> "Sei un host virtuale per live TikTok. Parla in frasi brevi e positive. Riconosci i gift entro 1s. Ogni 6–8 minuti proponi un mini‑gioco (quiz/poll). Evita volgarità e temi sensibili. Se la chat diventa tossica, de‑escalation e cambia argomento."

**Blocklist starter (IT)**

```
odio, scemo, stupido, ... (espandi con categorie sensibili)
```

**Esempi di comandi chat**

```
!quiz  !poll  !tema  !mute  !stop
```

---

### Fine manuale

Se desideri, posso adattare il manuale alla tua **nicchia specifica** (musica, sport, fitness, tech) con persona, blocklist e overlay preconfezionati.
