## 1.7.1
- **Subtítulos en MiBox arreglados**: el servidor remoto no envía cabeceras
  CORS, y el Chromecast rechazaba los subtítulos externos en silencio. Ahora
  la app descarga el .srt/.vtt, lo convierte a WebVTT y lo sirve desde la
  propia Mac por la red local con CORS correcto (`_SubtitlesRelay`). El
  dispositivo toma el vídeo de internet y el subtítulo de la Mac.
- Aviso en el mensaje de éxito: la Mac debe permanecer encendida mientras se
  proyectan subtítulos (si se interrumpe el relé, el vídeo sigue pero el
  subtítulo desaparece hasta reenviar).
- Si el relé local falla, se recae automáticamente en la URL remota original.

## 1.7.0
- **Enviar a MiBox / Chromecast**: opción en el menú contextual de vídeos y
  botones en la vista de detalle. La reproducción se solicita directamente
  desde el dispositivo Cast, por lo que la Mac queda libre.
- Configuración de IP y nombre del dispositivo guardada en la configuración
  local. Se intenta activar el primer subtítulo asociado cuando está disponible.
- `pychromecast` se declara como dependencia opcional de esta función; si no
  está instalada, el resto de la aplicación sigue funcionando.

# Changelog — Visuales UCLV

## 1.6.1
- **Arreglo**: cuando mpv arrancaba bien pero fallaba al cargar el
  video (ventana vacía "Drop files or URLs to play here", típico con
  el sitio lento), no se avisaba nada. Ahora la app detecta ese caso
  (evento `end-file` con error de mpv) y muestra un aviso claro.
- El timeout de red de mpv subió de 30s a 60s, para tolerar mejor un
  servidor lento al empezar a reproducir.

## 1.6.0
- **Búsqueda global**: botón 🔍 (o Enter en el buscador) abre un
  diálogo que busca por título en TODO el servidor, no solo la
  carpeta actual. La primera vez indexa recursivamente todo el árbol
  de carpetas en segundo plano (puede tardar varios minutos según el
  tamaño del sitio) y lo guarda en caché para las próximas veces.
  Botón "🔄 Reindexar" para cuando se agregue contenido nuevo.

## 1.5.0
- **Descargar carpeta completa**: clic derecho sobre una carpeta →
  "⬇ Descargar toda esta carpeta". Recorre todas las subcarpetas,
  muestra cuántos archivos encontró y el tamaño total estimado antes
  de arrancar, y guarda todo respetando la estructura de carpetas del
  sitio dentro de `~/Downloads/VisualesUCLV/`.

## 1.4.2
- **Arreglo crítico (de nuevo)**: el fix 1.4.1 capturaba la salida de
  error de mpv con un pipe que nadie leía de forma continua — si mpv
  escribía suficiente ahí, se quedaba trabado esperando poder escribir
  (deadlock clásico), y el video nunca llegaba a reproducirse. Ahora
  esa salida se manda a un archivo temporal (nunca bloquea) en vez de
  un pipe.
- Además, ya no se depende de acertarle al nombre exacto de los flags
  de caché de mpv: si el arranque con esos flags falla por lo que sea,
  la app reintenta automáticamente sin ellos — mejor reproducir sin la
  optimización de caché que no reproducir nada.

## 1.4.1
- **Arreglo crítico**: el fix de caché de la versión anterior usaba
  `--cache-secs`, una opción de mpv vieja/renombrada en versiones
  recientes — hacía que NINGÚN video arrancara (mpv abría la ventana
  pero se quedaba en la pantalla de "Drop files", sin reproducir
  nada). Reemplazado por `--demuxer-readahead-secs`, el nombre actual
  vigente.
- Ya no se esconde la salida de error de mpv: si vuelve a fallar por
  cualquier motivo (flag inválido, archivo no encontrado, etc.), ahora
  se muestra el mensaje real de mpv en vez de fallar en silencio con
  una ventana vacía.
- El breadcrumb y los nombres guardados en Favoritos ahora decodifican
  bien los caracteres especiales de la URL (antes mostraban cosas como
  "El%20ser%20querido" en vez de "El ser querido").

## 1.4.0
- Nueva pestaña **Historial** ("Continuar viendo"): lista las series y
  películas con reproducción en progreso, con carátula, porcentaje
  visto y tiempo restante. Ahora es la pantalla que recibe al abrir la
  app (antes era "Inicio"). Clic derecho para quitar un ítem.
- Arreglo importante: al pasar la reproducción de mpv embebido a
  proceso aparte (v1.1.0), se perdieron sin querer los parámetros de
  caché/buffer para streaming. Esto hacía que videos largos empezaran
  bien pero se pusieran lentos/entrecortados después de un rato. Ya
  están de vuelta (colchón de 5 min, hasta 150MB de demuxer cache).

## 1.3.0
- La vista de detalle estilo Netflix (carátula + sinopsis + play) ahora
  también aplica a la carpeta "Series", no solo "Peliculas".
- La búsqueda de sinopsis en TMDb ahora sabe distinguir película vs.
  serie (usa el endpoint /search/tv para series, con su propio
  parámetro de año).
- Para series organizadas como `Serie/Temporada 1/`, la búsqueda en
  TMDb usa el nombre de la serie (carpeta padre) en vez del nombre de
  la temporada, para que encuentre el resultado correcto.

## 1.2.1
- La búsqueda de sinopsis ahora usa TMDb en vez de OMDb, porque TMDb sí
  devuelve el texto en español (OMDb solo tiene el original de IMDb,
  casi siempre en inglés, sin forma de traducirlo). Requiere una API
  Key de TMDb (distinta a la de OMDb si ya habías cargado una).

## 1.2.0
- Auto-avance: al terminar un video, si hay otro en la misma carpeta
  (orden alfabético), lo reproduce solo. Pensado para series.
- Botón "🌐 Subtítulos" en el reproductor: busca y descarga subtítulos
  online desde OpenSubtitles (requiere API Key gratuita propia).
- Botón "🎬 Buscar sinopsis en IMDb" en la vista de detalle: trae la
  sinopsis (y póster si falta) vía OMDb (requiere API Key gratuita
  propia).
- Se sacó el acceso directo a MP3s del sidebar.
- Base para la futura app de música (lectura de metadatos ID3/carátulas
  embebidas, cola de reproducción, búsqueda de letras) — todavía no
  conectada a la UI principal.

## 1.1.0
- Vista de detalle estilo Netflix para carpetas de Películas (carátula,
  sinopsis, botón de reproducir), sin el fondo panorámico.
- Categorías de acceso directo en el sidebar (Películas, Series,
  Infantiles).
- Favoritos y Descargas más compactos en el sidebar.
- Reemplazo de la reproducción embebida de mpv (causaba crashes en
  macOS) por mpv como proceso aparte, controlado por IPC — mucho más
  estable.
- Carga automática de subtítulos locales (misma carpeta, mismo nombre).
- Resume: recordar el punto de reproducción de cada video.
- Vista de grilla con carátulas, gestor de descargas.
- Validación de imágenes con Pillow antes de mostrarlas (evita crashes
  nativos de macOS con archivos corruptos).

## 1.0.0
- Primera versión: explorador de `visuales.uclv.cu`, reproducción vía
  mpv embebido, favoritos, búsqueda, empaquetado como `.app` de doble
  clic.
