# Novedades

## 1.4.0

- **Resumen al terminar.** Una tarjeta con lo que ha hecho la copia: archivos nuevos, modificados,
  movidos y pasados a versiones, carpetas creadas y quitadas, sin cambios y verificados, más la
  duración y la velocidad media. La notificación de fin trae las mismas cifras.
- **La revisión de integridad ya no se alarga.** El tiempo elegido (5 minutos por defecto) se respeta
  aunque haya un archivo de varios GB a medias; antes se terminaba de leer entero y podía pasarse
  muchos minutos con la pantalla en «Comprobando 100 %».
- **No repite lo recién comprobado.** La revisión se salta lo verificado hace menos de 30 días (al copiar
  ya se verifica cada archivo), así que justo después de una copia grande no vuelve a releer el disco.
  *Comprobar todo el disco ahora* sigue revisándolo todo.
- **Se ve que está comprobando.** Mientras revisa, la pantalla lo dice, enseña el tiempo que le queda y
  la hora a la que acaba, y el botón *Omitir revisión* la termina al momento sin deshacer nada.
- **Unidad entera como origen.** Con la raíz de un disco como origen (`F:\`), ya no se copian la papelera
  (`$RECYCLE.BIN`) ni `System Volume Information`.

## 1.3.0

- **Equipo y disco despiertos mientras copia.** Durante el análisis y la sincronización el equipo no
  se suspende por inactividad (la pantalla sí puede apagarse). En Windows es una solicitud de energía
  con motivo, visible en `powercfg /requests`; en macOS, aserciones de energía (como `caffeinate -i -m
  -s`), visibles en `pmset -g assertions`.
- **El disco USB no se duerme.** Si el disco de copia lleva 30 s sin recibir escrituras (por ejemplo
  mientras se analiza el origen), la app escribe unos bytes en `.devbackup/latido` y los fuerza hasta
  el disco, para que ni el sistema ni el propio disco lo apaguen.
- **Apagado retenido.** En Windows, apagar, reiniciar o cerrar sesión con una copia en marcha muestra
  «Copia de seguridad en curso» y deja elegir; si se apaga igualmente, la copia se detiene de forma
  ordenada. En macOS, salir de la app, cerrar sesión o apagar pide confirmación.
- **Si el equipo se suspende igualmente** (tapa, botón de encendido), la copia se pausa antes y sigue
  sola al volver, en cuanto el disco está disponible.
- **Si el disco desaparece a mitad** (cable, reinicio del USB, suspensión), la copia espera a que
  vuelva y repite el archivo que estaba a medias, en vez de dar por fallidos todos los que quedan.
- Indicador «Sin suspensión» en la cabecera y nueva tarjeta *Energía* para activarlo o desactivarlo.
  Tras 20 minutos en pausa el equipo vuelve a poder suspenderse.

## 1.2.0

- **Actualizaciones automáticas.** La app comprueba en GitHub si hay una versión nueva (al arrancar y
  cada pocas horas, como mucho una vez al día) y pregunta antes de instalar nada. Se puede desactivar
  en *Actualizaciones* y buscar a mano con *Buscar ahora*.
- La descarga se comprueba con el SHA-256 que publica GitHub antes de instalarla. En Windows la
  instalación es silenciosa y la app se vuelve a abrir sola; en macOS se sustituye la app y se reabre.
- Si hay una copia en marcha, la actualización espera a que termine.
- Opción *Omitir esta versión* para no volver a avisar de una versión concreta.
- La versión instalada se ve junto al nombre de la app.

## 1.1.0

- Windows se distribuye como instalador: instalación por usuario sin administrador, acceso en el menú
  Inicio, desinstalador en *Configuración → Aplicaciones* y aviso si la app está abierta al actualizar.
- macOS: la versión aparece en la app y en el nombre del `.dmg`.

## 1.0.0

- Primera versión pública: sincronización en espejo con versiones, detección de movidos y
  renombrados, índice para análisis rápidos, verificación SHA-256 de cada copia, revisión periódica de
  integridad, detección del disco al conectarlo, Windows y macOS.
