# Operar StagePulse

[English](operations.md) | [Español](operations.es.md) · [Inicio rápido](../README.es.md#inicio-rápido-windows-powershell) · [Arquitectura](architecture.md)

Esta guía sirve para un evento o demo controlado. StagePulse no incluye autenticación, terminación TLS, historial persistente ni coordinación entre procesos. Usá una red confiable y prepará un origen HTTPS accesible con reenvío de WebSockets si el público se conectará por internet.

## Antes del evento

1. Instalá Python 3.10+ y FFmpeg en `PATH`. Desde la raíz del repositorio ejecutá `.\scripts\setup.ps1`, editá el `.env` local con `GEMINI_API_KEY` y corré `.\scripts\doctor.ps1` **antes** de iniciar el servidor. Doctor revisa la instalación y el puerto, pero no valida la clave ni la cuota del proveedor.
2. Confirmá los IDs y nombres en [config/stages.gate3.json](../config/stages.gate3.json). Las salas del demo son `gran-sala`, `auditorio` y `sala-abasto`. Agregá o cambiá salas en el JSON y reiniciá el servidor. La ruta de audio de muestra ignorada por Git se necesita solo para ejecuciones de archivo por línea de comandos, no para Entrada en vivo ni Archivo de prueba desde el navegador.
3. Si el público escaneará códigos QR, configurá `STAGEPULSE_PUBLIC_BASE_URL` en `.env` (o en el entorno del proceso servidor) con un **origen** HTTP(S) accesible, sin ruta. El entorno del proceso tiene prioridad. Reiniciá el servidor tras cambiar `.env`. Una URL de localhost solo funciona en la computadora anfitriona. Para acceso público remoto, prepará HTTPS y reenvío de WebSockets por separado; StagePulse no los incluye.
4. Iniciá el servidor con `.\scripts\start.ps1`. Por defecto escucha en `127.0.0.1:8000`; usá `-HostAddress` y `-Port` si tu red lo requiere. Abrí `http://127.0.0.1:8000/stage` en la computadora del escenario.

Si PowerShell bloquea los helpers, ejecutá `Set-ExecutionPolicy -Scope Process Bypass -Force` en **ese proceso de PowerShell** y repetí el comando. No cambies la política de toda la máquina para esta instalación.

## Operar una sala

| Paso | Acción del operador |
| --- | --- |
| Preparar | Elegí la sala en `/stage`. Opcionalmente ingresá título, orador y resumen. Pedí sugerencias de terminología a Gemini, **revisalas/editalas** y aplicalas. Nunca se aplican solas; Talk Prep se bloquea solo mientras esa sala está activa. |
| Capturar | Elegí **Entrada en vivo** y un dispositivo habilitado, o **Archivo de prueba** con audio decodificable. Archivo de prueba usa FFmpeg y acepta `.wav`, `.mp3`, `.m4a`, `.flac`, `.ogg`, `.mp4`, `.mov` o `.webm` hasta 100 MiB. Solo una fuente puede alimentar cada sala. |
| Iniciar | Presioná **Iniciar** una vez. Confirmá que la sala elegida figure activa, haya audio reciente, el proveedor esté conectado y aparezcan subtítulos originales y en español. Distintas pestañas de Stage Console pueden operar salas diferentes. |
| Distribuir | Compartí el enlace/QR de Audience View después de comprobarlo en un dispositivo del público. `/audience` muestra las salas. Abrí `/display/{stage_id}?lang=both` en una pantalla; `original` o `es` muestran un idioma. Agregá `/overlay/{stage_id}?lang=original` o `?lang=es` como fuente transparente de navegador y revisá su vista previa. La compatibilidad con vMix/OBS deriva del diseño de browser source; no afirma una prueba dentro de esas aplicaciones. |
| Monitorear | Abrí `/control`. Revisá estado, audio reciente, proveedor, espectadores, conexiones/reconexiones, edad de sesión, último subtítulo y último error de **cada** sala. Las vistas del público y del recinto comparten subtítulos y no abren conexiones Gemini. |
| Detener | Presioná **Detener** en la sala elegida. Confirmá que cambie su estado sin detener las demás. Reiniciala con Iniciar si hace falta; no se reproduce el historial de la ejecución anterior. |

La Entrada en vivo del navegador necesita un contexto seguro (localhost o HTTPS). Una recarga de la pestaña puede reconectar el socket de audio a una sala activa dentro de la ventana de recuperación del servidor; mantené la pestaña abierta durante una reconexión recuperable del proveedor. Los archivos subidos para pruebas son temporales y se eliminan al completar normalmente o detener la sala. Si el proceso se interrumpe, puede quedar un archivo temporal para limpieza del sistema operativo.

## Interpretar las señales

| Señal | Significado y acción |
| --- | --- |
| Audio reciente / proveedor conectado | El backend recibe audio reciente y la sesión Gemini está conectada. Si falta alguna señal, revisá dispositivo elegido, permisos del navegador, red y `last_error`. |
| Traducción OK | Hay evidencia reciente de audio y salida inglesa/española. No es una puntuación de precisión ni garantía de cobertura. |
| Traducción demorada | El detector observó audio e inglés continuos sin español utilizable reciente. Revisá si el español se recupera y el estado/error del proveedor. La reconexión opcional por demora es **solo de depuración**, está desactivada normalmente y no tiene un beneficio de producción demostrado. |
| Sin indicador de traducción | Faltan datos actuales para ambos estados; no demuestra salud ni falla de la traducción. |
| Conexiones / reconexiones | Contadores acumulados por sala. Más de una conexión puede indicar recuperación secuencial, no pipelines Gemini simultáneos. |
| Reconexión del público | Al volver al primer plano, Audience View se reconecta y recibe el último subtítulo disponible por idioma. No reproduce los subtítulos perdidos en segundo plano. |

Gemini GoAway y las desconexiones inesperadas disparan recuperación secuencial acotada en el proveedor Live. Durante el corte, el buffer es limitado y puede descartarse audio pendiente antiguo; StagePulse no garantiza que nunca se pierda habla. Vigilá `last_error` y si los subtítulos vuelven a aparecer.

## Problemas frecuentes

| Síntoma | Qué revisar / hacer |
| --- | --- |
| PowerShell bloquea un script | Usá `Set-ExecutionPolicy -Scope Process Bypass -Force` en la consola actual y repetí el comando. |
| Falta FFmpeg o no funciona | Instalá FFmpeg, agregá `ffmpeg.exe` a `PATH`, reabrí la consola y repetí `setup.ps1` / `doctor.ps1`. |
| El archivo multimedia no tiene audio | Elegí un archivo con pista de audio y voz audible. Una extensión aceptada no garantiza audio decodificable; leé el error en Stage Console/Control Room. |
| Error de acceso o cuota de Gemini | Confirmá `GEMINI_API_KEY` en el `.env` local, revisá acceso/cuota del modelo y conectividad, y consultá `last_error`. Doctor verifica presencia, no validez. No expongas la clave al diagnosticar. |
| Demora temporal del español | Revisá Traducción demorada, audio/inglés recientes, proveedor y si el español se recupera. Un intervalo corto no justifica afirmar subtítulos perdidos ni activar la recuperación de depuración. |
| Una pestaña o marcador usa un ID eliminado | Volvé a abrir `/audience` o `/stage`, elegí una sala configurada y reemplazá marcadores o QR antiguos. Las rutas de sala para IDs eliminados devuelven 404. |
| Puerto 8000 ocupado | Detené el proceso que lo usa o ejecutá `doctor.ps1 -Port 8001` y `start.ps1 -Port 8001`; usá ese puerto también en el origen del público. |
| El teléfono no abre el QR | Revisá la URL mostrada en Stage Console, origen alcanzable, red/firewall, HTTPS y reenvío de WebSockets. Para una URL LAN, iniciá con `start.ps1 -HostAddress 0.0.0.0`; el servidor predeterminado solo escucha en localhost. Un QR de localhost solo apunta a la computadora anfitriona. |

El servidor es un único proceso con estado en memoria. No inicies una segunda instancia esperando que comparta salas. Para el alcance de las pruebas y las brechas de evidencia, consultá [validación](validation.md) y el [README](../README.es.md#validación-y-mediciones).
