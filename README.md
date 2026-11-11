# Agente de WhatsApp de la Camara de Comercio

Responde en italiano, español o portugués usando SOLO los documentos de la carpeta `knowledge/`.
Deriva a una persona cuando hace falta, y el administrador controla todo desde su propio WhatsApp.

## Cuenta de WhatsApp Business
- Nombre: Cámara de Comercio Italiana para Colombia
- Identificador de la cuenta: `1423751313262138`
- Propiedad de: DeepSolutions
- [Abrir la cuenta en Meta Business](https://business.facebook.com/latest/settings/whatsapp_account/?business_id=1638573431087425&nav_ref=bizweb_settings_overview_assets_card&selected_asset_id=1423751313262138&selected_asset_type=whatsapp-business-account#)

## Puesta en marcha (Windows)
1. `py -3.13 -m venv .venv` · `.venv\Scripts\activate` · `pip install -r requirements.txt`
2. `copy .env.example .env` y completa las credenciales de Meta DE LA CAMARA, `ANTHROPIC_API_KEY`, `ADMIN_NUMBERS` y `CHAMBER_NAME`. Configura `VERIFY_TOKEN` (el mismo valor en Meta) y `APP_SECRET` (el secreto de la aplicacion de Meta; obligatorio para validar la firma de cada webhook). `PHONE_NUMBER_ID` puede aceptar varios IDs de WhatsApp para validar webhooks entrantes; `PRIMARY_PHONE_NUMBER_ID` es el ID que usa el bot para enviar mensajes salientes.
3. Carga documentos: `python kb_ingest.py servicios.pdf eventos.md`  (revisa los .md generados en `knowledge/`)
4. `.venv\Scripts\python.exe -m uvicorn main:app --port 8001`
5. ngrok `http 8001` y registra `https://TU-DIRECCION/webhook` en Meta (campo messages).

## Pruebas
Ejecuta las pruebas locales con `.venv\Scripts\python.exe -m unittest discover -s tests -v`.
La prueba real y de solo lectura de Graph API se habilita en PowerShell con
`$env:RUN_META_INTEGRATION="1"` antes del mismo comando. No envía mensajes.
Los IDs de mensajes entrantes se guardan en `camara.db` para evitar procesar webhooks duplicados
incluso después de reiniciar la aplicación; se conservan según `RETENTION_DAYS` (90 días por defecto).

## Comandos del administrador (por WhatsApp, desde un numero en ADMIN_NUMBERS)
/ayuda · /estado · /pendientes · /chat N [k] · /responder N texto · /pausa N · /reanudar N ·
/recargar

El agente nunca bloquea contactos. Las derivaciones a un asesor y las respuestas manuales no pausan
al agente: para detener sus respuestas en un chat, el administrador debe usar `/pausa N`; para
reactivarlas, usa `/reanudar N`.

Importante: el aviso "Solicitud de asesor" le llega al administrador solo si este escribio al bot en las
ultimas 24 horas (regla de Meta). Escribele /estado cada manana para mantener la ventana abierta.
