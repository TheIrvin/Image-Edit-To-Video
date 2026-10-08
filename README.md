# Imagen a Historia

App local para convertir una carpeta de imágenes y una narración TXT en un video MP4. Interfaz en español, biblioteca de voces, editor por escena, vista previa, exportación y historial. Se ejecuta en tu PC y abre una interfaz en el navegador.

## Empezar en Windows

1. Instala **Python 3.12** desde [python.org](https://www.python.org/downloads/) si todavía no lo tienes.
2. Descarga o clona este repositorio.
3. Haz doble clic en **`Iniciar.bat`**. La primera vez instala las dependencias; abre [http://127.0.0.1:8765](http://127.0.0.1:8765).
4. Crea un proyecto y selecciona la carpeta de imágenes y el TXT.
5. Revisa las parejas imagen–texto, selecciona una voz y genera una vista previa.
6. Elige formato y resolución, y pulsa **Exportar video**.

Mantén abierta la ventana del servidor mientras utilizas la app. FFmpeg está incluido mediante `imageio-ffmpeg`; no hace falta instalarlo por separado. Si el puerto está ocupado, ejecuta `.venv\Scripts\python.exe run.py --port 8766`.

Los selectores abren diálogos nativos de Windows. También puedes pegar las rutas completas. Se admiten PNG, JPG, JPEG, WEBP y BMP de la carpeta seleccionada, sin recorrer subcarpetas. Los archivos originales se conservan; cada proyecto copia sus imágenes a su propio directorio.

## Dos formatos de TXT

### Con etiquetas

```text
img1:
La primera vez que Nico murió, tenía la mano de un desconocido entre los dedos.

img2:
Nico alcanzó a empujar al hombre hacia la salida. Después, el interior quedó a oscuras.
```

Las etiquetas nunca se envían al sintetizador. El orden de los bloques del TXT es el orden del video. Se rechazan etiquetas repetidas, bloques vacíos y texto antes de la primera etiqueta. Puedes usar varios párrafos dentro de un bloque etiquetado.

### Sin etiquetas

```text
La primera vez que Nico murió, tenía la mano de un desconocido entre los dedos.

Nico alcanzó a empujar al hombre hacia la salida. Después, el interior quedó a oscuras.
```

Una **línea en blanco** separa escenas: dos saltos de línea, o más. Un salto de línea simple continúa el mismo párrafo. Debe haber exactamente tantos párrafos como imágenes, para evitar desplazamientos accidentales entre narración e imagen. Se admiten UTF-8, UTF-8 con BOM y Windows-1252.

## Nombres y orden de imágenes

El modo predeterminado es **Por orden**. Ordena los archivos de forma natural y asigna la primera imagen a `img1`, la segunda a `img2`, etc. No exige renombrar archivos:

| Archivo original | Escena |
| --- | --- |
| `stick8.png` | `img1` o primer párrafo |
| `stick9.png` | `img2` o segundo párrafo |
| `stick10.png` | `img3` o tercer párrafo |

Admite nombres distintos del prefijo `img`. Si todos los nombres terminan en un número único, se ordenan por ese número. Para otros nombres se usa orden natural alfanumérico. No se renumeran los archivos originales.

El modo opcional **Por número del archivo** usa el número final: `stick8.png`, `foto_8.jpg` e `imagen8.webp` corresponden a `img8:`. Solo es útil con TXT etiquetados; un TXT por párrafos siempre se empareja por orden. Se detectan números duplicados y escenas sin imagen. Con etiquetas se permite omitir imágenes y se muestra una advertencia.

## Edición y sincronización

- Movimientos lentos: acercamiento, alejamiento, recorridos horizontales/verticales, diagonales con zoom e imagen fija.
- Entradas: corte directo, fundido cruzado, fundido por negro, cortinillas, deslizamientos y apertura circular.
- Filtros: original, cálido, frío, blanco y negro, sepia y cine suave.
- Cada escena permite editar narración, movimiento, transición, filtro, encuadre, punto de interés y pausa adicional.
- El modo automático usa una secuencia repetible con predominio de cortes y fundidos. No analiza semánticamente la imagen ni decide dónde está un rostro; revisa el punto de interés en el editor.
- El texto completo se sintetiza por escena y se mide el **WAV real**. La duración visual es `audio + margen global + pausa adicional`, redondeada hacia arriba a un fotograma.
- El margen predeterminado es **0,1 s**: un audio de 3 s produce una escena de 3,1 s a 30 fps. El redondeo agrega como máximo menos de un fotograma.
- Las transiciones se aplican al inicio de la escena nueva usando el último fotograma de la anterior. No restan tiempo a las escenas, no mezclan las voces y no cambian la velocidad de la narración automáticamente.
- **Preparar y medir narración** permite conocer los tiempos antes de renderizar. La caché reutiliza el audio al cambiar formato, movimientos o filtros; se invalida al cambiar texto, voz o velocidad.
- **Previsualizar escena** incluye la escena anterior cuando existe, para revisar la transición de entrada. Las vistas previas se generan a 720 y se guardan por separado.

El modelo de voz puede cometer errores de pronunciación o generación; escucha las vistas previas antes de publicar. El editor conserva el texto suministrado y nunca lo resume ni lo reescribe automáticamente.

## Horizontal y vertical

La app genera archivos reales en **16:9** y **9:16**, con 24 o 30 fps. El reproductor respeta la proporción del video generado. Cambiar el formato de un proyecto requiere generar su nueva versión.

Para convertir imágenes horizontales a vertical:

- **Recorte:** llena el encuadre y permite elegir el punto de interés con un clic en la imagen original. Es útil para conservar un rostro u objeto.
- **Imagen completa + fondo suave:** ajusta la imagen al encuadre sobre una copia desenfocada y oscurecida. En 9:16, las imágenes horizontales ocupan todo el ancho y el fondo completa el espacio superior e inferior.

Puedes elegir un ajuste global y sobrescribirlo por escena. Exportación H.264 + AAC, compatible con reproductores habituales:

| Opción | 16:9 | 9:16 |
| --- | --- | --- |
| 720 | 1280 × 720 | 720 × 1280 |
| 1080 | 1920 × 1080 | 1080 × 1920 |
| 1260 | 2240 × 1260 | 1260 × 2240 |
| 2K / QHD | 2560 × 1440 | 1440 × 2560 |

Se conserva la opción **1260** solicitada y se añade 1080. En esta app, “2K” se usa como la denominación habitual de QHD 1440; no corresponde al formato cinematográfico DCI 2048 × 1080. No se ofrece 4K.

## Voces locales

### Motor alternativo: OpenVoice V2 + MeloTTS español

La Biblioteca de voces permite instalar OpenVoice V2 y seleccionar el motor al guardar una voz. **Probar en OpenVoice**, en una voz existente de Chatterbox, crea un perfil alternativo con la misma muestra y un ejemplo audible. Usa ese perfil desde **Usar en este proyecto** después de escucharlo.

Este motor usa MeloTTS español para la narración y OpenVoice V2 para convertir el timbre a la muestra. No necesita transcribir el audio ni cargar modelos para otros idiomas. En la prueba local, ambos modelos sumaron **84.666.323 parámetros**. La segunda frase consecutiva produjo 2,98 s de audio en **8,78 s** de síntesis y conversión; la primera llamada y la carga inicial son más lentas. Son mediciones de una frase corta, no un tiempo garantizado para videos completos. El parecido de voz y la expresividad pueden diferir de Chatterbox.

Los proyectos y voces anteriores conservan su motor. Al cambiar de motor, se generan audios nuevos para mantener toda la narración con el mismo tipo de voz. El motor reutiliza sus modelos durante todas las escenas de una tarea y guarda la identidad de la muestra en caché. La instalación necesita Git, Internet y los modelos oficiales de [OpenVoice V2](https://github.com/myshell-ai/OpenVoice) y [MeloTTS](https://github.com/myshell-ai/MeloTTS).

La comparación local de una misma escena de narración produjo un MP4 de 12,7 s con OpenVoice en **85 s** (incluida la carga inicial), frente a unos **255 s** en la prueba anterior con Chatterbox. Es una comparación de esa escena, no una proyección para las 77 escenas del proyecto ni una garantía de calidad equivalente.

**Optimizar CPU**, en la tarjeta de Chatterbox de Biblioteca de voces, compara 1, 2, 4 y 6 hilos con el transformer original y con sus capas lineales en INT8. Guarda un perfil local y aplica el más rápido a los próximos procesos de Chatterbox. Es una medición del decoder, no una promesa de aceleración idéntica para la generación completa. El modelo acústico conserva su precisión original. Cambiar la velocidad de narración reutiliza el audio a velocidad original y aplica `atempo`, sin repetir la clonación. Cada tarea de voz guarda tiempos de tokens y de generación de onda en `speech-metrics.json` para localizar el coste real.

El montaje genera primero los audios pendientes y mide cada WAV, sin transcribirlos de nuevo. La caché conserva los bloques completos y las frases de voz clonada terminadas para reutilizarlas tras una cancelación. Las frases cortas de un bloque se agrupan hasta 220 caracteres para reducir llamadas al modelo. La estimación del tiempo restante se calcula a partir de los bloques completados y puede variar según su longitud. En el render se extrae el último fotograma desde el último segundo del clip, evitando decodificarlo entero.

### Clonación: Chatterbox Multilingual

Desde **Biblioteca de voces**, pulsa **Instalar motor local**. También puedes ejecutar `Instalar-voces.bat`. La descarga inicial requiere Internet y varios GB de almacenamiento. Después se ejecuta localmente, sin enviar la narración ni tus muestras a un servicio TTS.

La integración usa **`chatterbox-tts==0.1.7`**, su checkpoint multilingüe V2 y PyTorch 2.6 para **CPU**, en un entorno separado `.venv-voice`. El repositorio oficial también documenta versiones posteriores; la app usa la API y los pesos de la versión publicada que se instala y comprueba aquí.

1. Guarda una voz con un nombre y una muestra WAV, MP3, M4A, FLAC u OGG.
2. Recomiendo **8–15 segundos** de una sola persona, sin música ni ruido. La app acepta al menos 3 s y conserva como máximo 30 s. El modelo usa principalmente los primeros 6–10 s de referencia.
3. Pulsa **Generar ejemplo** para escuchar esa voz leyendo el texto de prueba.
4. Selecciónala en cualquier proyecto. Puedes guardar 10 perfiles o más.

Cada perfil almacena su muestra y un ejemplo sintetizado. No se entrena un modelo completo por voz: todos los perfiles comparten el motor de clonación. Retirar una voz de la biblioteca conserva sus archivos para los proyectos y versiones ya guardados.

Hardware objetivo de esta implementación: **Intel i5-12500H, aproximadamente 16 GB de RAM, Intel Iris Xe, sin CUDA**. Chatterbox puede ejecutar inferencia en CPU, pero no se promete generación en tiempo real. Los proyectos largos pueden tardar bastante; la cola procesa una tarea a la vez para limitar el consumo de memoria. La calidad depende de la muestra. Usa tu voz o una voz que tengas permiso para usar.

Comprobación realizada en ese equipo: la primera síntesis de una frase produjo un WAV de **4,34 s** y tardó aproximadamente **230 s** incluyendo carga del modelo y preparación de referencia. Se usó una voz sintética de Windows como referencia técnica, no una muestra humana para evaluar similitud. La generación de tokens ocupó unos 42 s de esa ejecución. En un video se carga el modelo una vez para todas sus escenas; cambiar únicamente la edición reutiliza el audio. Las voces de Windows son considerablemente más rápidas.

### Voces de Windows

También se muestran las voces instaladas de Windows, mediante `System.Speech`. Permiten probar la edición inmediatamente sin descargar un modelo. Son voces estándar, no voces clonadas. En el equipo de desarrollo hay Microsoft Sabina Desktop, en español de México.

La instalación del clonador no solicita una clave OpenAI ni una suscripción. Chatterbox y sus muestras oficiales se investigaron en:

- [Repositorio oficial de Resemble AI](https://github.com/resemble-ai/chatterbox)
- [Paquete publicado de Chatterbox](https://pypi.org/project/chatterbox-tts/0.1.7/)
- [Modelo multilingüe oficial](https://huggingface.co/ResembleAI/chatterbox)

Se consideró [XTTS-v2](https://github.com/coqui-ai/TTS/blob/dev/docs/source/models/xtts.md), que también admite español, pero tiene una licencia de modelo diferente. La opción integrada es Chatterbox, cuyo repositorio usa licencia MIT. Los avisos y las licencias de las dependencias siguen aplicándose.

## Historial, datos y recuperación

Los archivos locales están en `data/`, excluidos de Git:

```text
data/
  projects/<id>/       imágenes copiadas, TXT original, configuración editable
  voices/<id>/         muestra, perfil, ejemplo de voz
  cache/audio/         narraciones reutilizables
  models/             pesos del clonador
  jobs/               estado, solicitudes y registros de procesos
  exports/<id>/       video.mp4, timeline.json, subtitles.srt,
                      project-snapshot.json, manifest.json
```

Cada exportación y vista previa tiene un ID propio. No sobrescribe las versiones anteriores. El historial permite reproducir, descargar, abrir la carpeta y volver al proyecto. `project-snapshot.json` conserva la configuración usada por esa versión; abrir el proyecto desde el historial muestra su estado editable más reciente.

La opción SRT ofrece **subtítulos por escena**, con el texto completo y los tiempos del audio; no promete alineación por palabra ni los quema dentro del video. `timeline.json` contiene los tiempos reales por escena. Se crea el SRT en cada exportación y la opción de la interfaz habilita su descarga desde el historial.

Puedes cancelar tareas en curso o en cola. El audio completamente generado se conserva en caché. Si la app se cierra durante una tarea, marca esa tarea como interrumpida al reiniciar; vuelve a generarla para reutilizar el audio. No hay reanudación de los segmentos de video parcialmente renderizados. La app guarda cambios automáticamente y detecta conflictos entre ventanas.

El servidor escucha solamente en `127.0.0.1`; no se expone a la red. Bloquea mutaciones desde orígenes ajenos a la app. No requiere servicios externos para editar ni exportar. La instalación de los modelos sí descarga archivos de sus fuentes oficiales.

## Desarrollo y comprobación

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe run.py --no-browser
```

Las pruebas verifican parsing con y sin etiquetas, orden natural y emparejamiento desde `stick8`, errores de cantidad/numeración, caché estable, controles de API, punto de interés y render real de las ocho transiciones en ambos formatos. Decodifican los MP4 para contar los fotogramas y comprobar que la pausa de audio siga siendo silencio. Utilizan audio de prueba conocido, sin descargar el modelo de voz.

El motor se renderiza secuencialmente con FFmpeg y prepara una imagen por escena con Pillow; no carga cien imágenes a resolución completa a la vez. Chatterbox vive en otro proceso para aislar sus dependencias y permitir cancelar su ejecución.
