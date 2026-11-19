# Lector de chat de Kick

Aplicación portable para Windows que muestra los mensajes nuevos de un chat en vivo de Kick y, si está activada la opción, los lee en voz alta. También omite los emotes de Kick al mostrar y pronunciar el mensaje.

## Usar la aplicación

Descarga `Lector de Kick.exe` y ábrelo con doble clic. Escribe el nombre del canal y pulsa **Iniciar**. Necesitas conexión a internet y una voz instalada en Windows.

## Ejecutar desde el código

Requiere Python 3 en Windows. Desde esta carpeta, abre PowerShell y ejecuta:

```powershell
py -m pip install -r requirements.txt
pyw lector_kick.pyw
```

## Dependencias

- `requests`: consulta los datos públicos del canal.
- `websocket-client`: recibe los mensajes del chat en tiempo real.
- `pyttsx3`: voz local de Windows. Se fija en la versión 2.98 por un problema de reproducción repetida reportado en Windows con la versión 2.99.

## Notas

La aplicación solo lee mensajes; no inicia sesión ni envía mensajes. La lectura en tiempo real utiliza el protocolo público que emplea el sitio web de Kick, que puede cambiar sin aviso. Si Kick cambia ese protocolo, puede requerirse una actualización del programa.

El ejecutable portable está incluido para Windows. El archivo `LEEME.txt` contiene instrucciones rápidas para usarlo.
