# Novedades

## 1.7.0

- **Barra lateral reordenada por uso.** Primero *Copia* (origen → destino en una sola tarjeta), luego
  *Qué se copia*, *Cómo se copia* (modo y versiones, integridad), *Automático* (disco de copia,
  recordatorios) y *Sistema* (energía, actualizaciones). Es más ancha y un 30 % más corta.
- **Tarjetas plegables con resumen.** Plegada, cada tarjeta enseña en una línea lo elegido («Espejo con
  versiones · versiones 90 días · 16 hilos»). Lo que se usa menos empieza plegado y la app recuerda cómo
  las dejas.
- **Interruptores** en lugar de casillas de dos líneas: título, explicación corta y el interruptor a la
  derecha; un clic en el texto también lo cambia, y se maneja con el teclado. Las explicaciones largas
  pasan a la ayuda emergente.
- La ventana estrecha ya no corta la página *Progreso* ni las demás: las casillas de datos pasan a dos
  columnas cuando no caben y los textos se reparten en varias líneas.
- *Incidencias* sin problemas se ve en verde, con un mensaje claro, y *Exportar* solo se activa si hay algo.
- Las copias recuperadas de los registros cuyo disco no estaba conectado al actualizar se apuntaban
  solo por su ruta, y la siguiente copia al mismo sitio se apuntaba aparte (la antigua podía avisar sin
  motivo). Ahora se unen: al arrancar toman la marca del disco registrado, al conectar un disco con su
  carpeta toman la suya y al sincronizar se juntan con la copia nueva. También toman el nombre del disco.
- Capturas del README y de la guía rápida con el diseño nuevo.

## 1.6.0

- **Recordatorios de copia.** Si llevas días sin copiar y has cambiado algo desde la última copia, la
  app te lo recuerda: «Tienes cambios sin copiar en DEV · 37 archivos nuevos o modificados desde tu
  última copia en BACKUP_4, hace 4 días». Si no has tocado nada, no dice nada.
- **Copias habituales.** Cada sincronización completa queda apuntada con su origen y su disco (aunque
  cambie de letra), así que se vigilan a la vez todas las que haces: carpetas distintas, discos
  distintos. Las copias hechas con versiones anteriores se recuperan de los registros.
- Eliges cada cuánto (1 día, 2, 3, 1 semana o 2 semanas) o lo desactivas, en general o copia a copia.
- **Sin agobiar.** Comprueba en segundo plano, sin abrir archivos y solo cuando toca; avisa una vez por
  periodo; con la ventana delante basta un aviso dentro de la app, y si no, una notificación del
  sistema. *Recordar mañana* lo aplaza un día y cerrarlo lo deja hasta el siguiente periodo.
- Si el disco está conectado, *Sincronizar ahora* desde el propio aviso; si no, te dice cuál conectar.
  Desde la lista se puede cambiar a otra copia habitual, comprobarla al momento o quitarla.

## 1.5.0

- **Guía rápida.** La primera vez que abres la app aparece un visor de cinco pasos con capturas reales:
  elegir origen y destino, decidir qué se copia, analizar y sincronizar, y dejar que vigile el disco.
  En cada captura se destaca dónde tocar.
- Se vuelve a abrir cuando quieras con el botón *Guía* de la cabecera, con F1 (⌘? en macOS) o desde el
  icono de la bandeja.
- Se maneja con el teclado (← →, Intro, Esc) o con los puntos de progreso, y respeta el ajuste del sistema
  que reduce las animaciones.

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
