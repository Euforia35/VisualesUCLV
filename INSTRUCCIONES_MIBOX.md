# Visuales UCLV 1.7.1 — integración MiBox / Chromecast

Esta carpeta contiene el código fuente integrado. La sintaxis de todos los módulos
Python fue comprobada con `py_compile`.

## Ejecutar desde el entorno virtual
```bash
source venv/bin/activate
python3 -m pip install -r requirements.txt
python3 main.py
```

## Crear la aplicación macOS
En macOS, desde esta carpeta y con el entorno virtual activado:
```bash
python3 setup.py py2app
```
La aplicación generada quedará en `dist/Visuales UCLV.app` (según la configuración
de py2app, puede aparecer como `dist/VisualesUCLV.app`).

## Enviar a MiBox
1. Asegúrate de que la Mac y el MiBox/Chromecast estén en la misma red local.
2. En el menú contextual de un vídeo, elige «Configurar MiBox…» e introduce la IP.
3. Usa «Enviar a MiBox…».
4. Para detener la transmisión, abre el menú contextual de un vídeo y elige
   «Detener transmisión MiBox» mientras la conexión esté activa.

## Subtítulos (novedad en 1.7.1)
Los subtítulos se sirven desde tu Mac (convertidos a .vtt y con CORS) porque
el servidor remoto no lo permite. Consecuencias:
- Mantén la Mac encendida y despierta mientras se proyectan subtítulos.
- Acepta el permiso del firewall la primera vez que la app sirva un subtítulo.
- Si un subtítulo no aparece, reenvía el vídeo; el mensaje de éxito indica
  siempre el estado de la pista.

La dirección del vídeo debe ser accesible desde el dispositivo de destino. El
código fuente no incluye una app `.app` ya compilada: py2app debe ejecutarse en
macOS para producir un paquete nativo de esa plataforma.
