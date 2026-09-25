# StagePulse

StagePulse es una infraestructura open source de subtítulos en vivo construida para la **Nerdearla Vibeathon 2026**. En una conferencia, el navegador de cada escenario captura su audio una sola vez. StagePulse transcribe el inglés original, lo traduce al español y distribuye los mismos eventos de subtítulos a la consola del escenario, los teléfonos del público, la sala de control y los overlays de transmisión. Las vistas del público no crean pipelines de IA adicionales.

El proyecto es un prototipo funcional para eventos locales. La [documentación inglesa](README.md) es la versión canónica; consultá la [guía de operación](docs/operations.es.md) para un checklist corto.

## Requisitos

- Windows y Python con `venv` (probado con Python 3.13); acceso de red a la API de Gemini.
- Una `GEMINI_API_KEY` válida en un `.env` local. Nunca incluyas este archivo en Git.
- Un navegador con soporte de micrófono y AudioWorklet. La captura requiere un contexto seguro: localhost en la computadora del escenario o HTTPS.
- FFmpeg en `PATH` para la herramienta separada de transcripción de archivos y las pruebas de escenarios con archivos. La captura del navegador no usa FFmpeg.
- Una entrada de audio autorizada en el evento. Los audios de muestra del desafío se excluyen de Git.

## Instalación e inicio

Ejecutá estos comandos en PowerShell desde la raíz del proyecto:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Editá `.env` localmente y configurá `GEMINI_API_KEY`. Iniciá el servidor manualmente o con el helper de Windows:

```powershell
.\.venv\Scripts\python.exe backend\serve.py
# O bien:
.\scripts\start.ps1
```

El helper comprueba `.venv`, `.env` y el puerto antes de iniciar `backend/serve.py`. Acepta `-HostAddress`, `-Port` y `-Config`. El comando manual sigue disponible y muestra los errores directamente.

## Configuración de escenarios y terminología

Editá `config/stages.gate3.json`. Cada escenario configurado tiene su propio `StageWorker` y pipeline Gemini Live Translate. Agregá escenarios con nuevas entradas JSON, sin cambiar el código de la aplicación. `audio_file` sirve para pruebas con archivos; al iniciar Stage Console, la entrada elegida en el navegador reemplaza esa fuente.

El mapa opcional `terminology` aplica reemplazos explícitos dentro de límites de palabra antes de publicar subtítulos, por separado para cada idioma:

```json
{
  "id": "main",
  "name": "Main Stage",
  "source_language": "en",
  "target_language": "es",
  "audio_file": "../samples/nerdearla-freedos-60s.wav",
  "terminology": {
    "en": {"Word Perfect": "WordPerfect"},
    "es": {"Word Perfect": "WordPerfect"}
  }
}
```

El ejemplo se configura sólo para `main`; un escenario sin `terminology` conserva su texto original. Es un reemplazo determinístico, sin otro modelo ni coincidencia aproximada.

## Operación de un escenario

1. Abrí `http://127.0.0.1:8000/stage` en la computadora del escenario. Elegí escenario y entrada de audio y presioná **Iniciar**. La consola muestra subtítulos originales y en español.
2. Abrí `/control` para ver todos los escenarios configurados, con datos de audio, proveedor, conexiones, errores, subtítulos y suscriptores.
3. Compartí el QR o enlace copiado a `/audience/{stage_id}`. El público puede cambiar el **idioma de los subtítulos** entre Original y Español.
4. Agregá `/overlay/{stage_id}?lang=original` o `?lang=es` al sistema de transmisión. En vMix usá Browser Input de 1920×1080; en OBS, Browser Source de 1920×1080. El overlay tiene fondo transparente y coloca los subtítulos cerca del área segura inferior.
5. Presioná **Detener** en Stage Console al finalizar.

Stage Console, Audience View y Control Room tienen un selector independiente de **idioma de la interfaz** (English/Español), guardado en `localStorage`. No modifica el idioma de los subtítulos. El overlay no muestra controles. La [guía de operación](docs/operations.es.md) contiene el checklist y soluciones básicas.

## Enlaces para público en LAN

El QR usa el origen de la solicitud recibida salvo que se configure `STAGEPULSE_PUBLIC_BASE_URL`. Un QR generado desde localhost no funciona en otros dispositivos; Stage Console lo advierte. Para teléfonos en la LAN, usá un origen accesible para ellos:

```powershell
$env:STAGEPULSE_PUBLIC_BASE_URL = "http://192.168.1.20:8000"
.\.venv\Scripts\python.exe backend\serve.py --host 0.0.0.0
```

Reemplazá la IP de ejemplo por la dirección LAN real de la computadora. La variable debe ser un origen HTTP(S) sin ruta. El acceso al micrófono desde otra computadora normalmente requiere HTTPS; localhost sirve en la computadora del escenario. StagePulse aún no tiene autenticación: mantené el servidor en una red confiable. El QR se genera localmente, sin servicio externo.

## Confiabilidad y evidencia de validación

Live Translate usa handles de reanudación de sesión y compresión de contexto con ventana deslizante. StagePulse rota la conexión Gemini ante GoAway y reintenta desconexiones inesperadas con backoff acotado, conservando el mismo worker y bus de subtítulos. Durante una reconexión almacena hasta 102.400 bytes (3,2 segundos) de PCM, descarta los frames pendientes más antiguos si se llena y no reenvía frames cuya entrega es incierta. La entrada del navegador tiene otro límite de 102.400 bytes. No persiste audio ni subtítulos.

Una validación anterior con audio real ejercitó dos escenarios simultáneos. Una corrida continua de audio Nerdearla de 13 minutos y 1 segundo recibió un GoAway con 50 segundos restantes, reanudó en una segunda conexión y terminó por la detención programada sin error ni PCM descartado registrado. Otra prueba de vistas de producción mostró cinco suscriptores de subtítulos en un escenario activo sin conexiones Gemini adicionales; cerrar y reabrir una vista cambió los suscriptores sin cambiar el contador de conexiones del proveedor. Estas pruebas no demuestran disponibilidad sin cortes, sesiones ilimitadas ni una cifra general de precisión.

Para una prueba controlada de confiabilidad, `backend/serve.py` y `backend/run_stages.py` aceptan `--debug-reconnect-after SECONDS`. Está desactivado por defecto y es sólo para pruebas.

## Otras herramientas y límites

`backend/transcribe_file.py ruta\al\archivo-de-audio-o-video` es la herramienta separada de Gate 1. Usa FFmpeg para enviar PCM mono de 16 bits y 16 kHz a Gemini Live e imprime transcripciones originales provisionales y finales. Su comportamiento en sesiones largas es independiente del proveedor de traducción por escenario.

StagePulse funciona actualmente en un solo proceso servidor con un bus de subtítulos en memoria. No tiene autenticación, base de datos, historial de subtítulos ni distribución entre procesos. El uso como Browser Source en vMix y OBS está documentado, pero no se probó dentro de esas aplicaciones. El modelo Gemini es una dependencia preview y requiere acceso y cuota de API. La [arquitectura](docs/architecture.md) está documentada en inglés.

## Licencia

Apache-2.0; consultá [LICENSE](LICENSE).
