# Novedades

## 2.0.2

- **Transcurrido y Restante siempre en `hh:mm:ss`.** Al terminar el análisis, Transcurrido ya no
  muestra milésimas en los segundos.

## 2.0.1

- **La rueda del ratón ya no cambia los desplegables ni los contadores.** Al pasar por encima
  sigue desplazándose la página; para cambiar un valor hay que hacer clic.

## 2.0.0

- **Nueva pestaña «Recuperar»: una máquina del tiempo de tu copia.** Todo lo que la sincronización
  apartó a versiones (lo que borraste del origen y la versión anterior de lo que cambiaste) aparece
  por fecha en una línea de tiempo: hoy, ayer, esta semana y por meses. «Todas las fechas» enseña la
  versión más reciente de cada archivo, con buscador y filtros *Borrados* y *Versiones anteriores*.
  - **Recuperar** devuelve lo seleccionado (archivos o carpetas enteras) a su sitio en el origen;
    **Recuperar en…** lo deja en otra carpeta. La versión se copia, no se mueve: sigue disponible.
  - **Nunca sobrescribe.** Si ya hay un archivo distinto con ese nombre, la versión se guarda al lado
    como `nombre (recuperado AAAA-MM-DD HHMM).ext`; si es idéntico, no se duplica.
  - **Ver** abre una copia temporal de solo lectura; **Mostrar** la enseña en su carpeta.
  - Clic derecho en una fecha: abrir su carpeta o eliminar esas versiones para liberar espacio
    (pide confirmación).
- **Protección contra borrados masivos.** Si una sincronización en espejo va a apartar a versiones
  una parte grande de la copia (50 archivos o más y al menos el 25 %, o 1 GB y el 25 % de los datos),
  o si el origen aparece vacío, se detiene antes de tocar nada y explica qué carpetas desaparecen. Se
  elige: *No sincronizar* (lo que pasa con Intro), *Ver la lista* o *Sincronizar igualmente*. También
  cuando sincroniza sola al conectar el disco. El análisis ya lo avisa en amarillo.
- **Versiones con plazo corto y limpieza automática.** Se guardan 7, 30 (por defecto) o 60 días y
  luego se quitan solas. Los plazos antiguos (90 días, 1 año, siempre) pasan a 60 días. Si lo que hay
  que copiar no cabe en el disco, antes de copiar se quitan las versiones más antiguas hasta que
  quepa (como Time Machine), y el resumen dice cuánto se liberó.
- El autodiagnóstico comprueba la protección, recuperar sin sobrescribir ni duplicar y la página
  «Recuperar».

## 1.9.7

- **Mover carpetas a otra subcarpeta sin recopiar nada, en todos los casos.** Además de deducir el
  movimiento por los archivos únicos, ahora se reconoce la carpeta entera: si lo que desaparece de
  una carpeta coincide archivo a archivo (ruta interna, tamaño y fecha) con lo nuevo de otra, y la
  coincidencia es única, es la misma carpeta en otro sitio. Así se mueven también:
  - varias subcarpetas de una misma carpeta llevadas a sitios distintos (`A\X` → `B\X` y
    `A\Y` → `C\Y`), con sus archivos repetidos;
  - una carpeta que solo tiene archivos repetidos (por ejemplo, paquetes con `__init__.py` vacíos).
- Una carpeta distinta con el mismo nombre no se confunde con una movida: se copia.
- El autodiagnóstico comprueba también mover subcarpetas hermanas a sitios distintos.

## 1.9.6

- **Corregido (importante): las carpetas excluidas a mano se olvidaban.** Desde la 1.0.0, recargar el
  árbol de carpetas (al abrir la app, al cambiar el origen o con el botón de volver a leer) borraba
  las exclusiones hechas a mano, y la siguiente copia incluía lo que habías desmarcado. Ahora se
  conservan; las reglas por nombre (`node_modules`, `.venv`…) no estaban afectadas. Revisa *Qué se
  copia* por si alguna carpeta que querías fuera vuelve a estar marcada.
- **Renombrar una carpeta no recopia nada.** Además de los archivos únicos, ahora se mueven también
  los repetidos (varios `__init__.py` vacíos, `.gitkeep`…): se deduce qué carpeta pasó a llamarse
  cómo y cada archivo se empareja con el de la misma ruta bajo el nombre antiguo.
- **Las exclusiones siguen a su carpeta.** Si excluiste `Proyecto\build` y renombras `Proyecto` a
  `Proyecto-v2`, la exclusión pasa sola a `Proyecto-v2\build` (lo dice el registro) y no se copia.
- **Cambios solo de mayúsculas.** `proyecto` → `Proyecto` (o un archivo `readme.md` → `README.md`)
  se renombra también en la copia, sin volver a copiar el contenido.
- **macOS: mayúsculas.** En macOS, «lib» y «Lib» se trataban como carpetas distintas aunque el disco
  no lo distinga, y al cambiar solo las mayúsculas la copia de su contenido pasaba a versiones hasta
  la sincronización siguiente. Ahora los nombres se comparan igual que en Windows.
- El autodiagnóstico comprueba los tres casos y que recargar el árbol conserva las exclusiones.

## 1.9.5

- **El tiempo restante cuenta también los atascos.** Si el servidor o el disco se paran un rato
  (otra copia compite, la nube vacía su caché), ese tiempo ya entra en la estimación en lugar de
  ignorarse; la 1.9.4 podía quedarse muy corta en esos casos. Mide en tramos de 10 s, que promedian
  los atascos cortos.
- La primera copia a una carpeta de red parte de valores típicos de red (unos 60 ms por archivo)
  en lugar de los de un disco USB, así que acierta mucho antes. Desde la segunda, usa lo aprendido.
- La gráfica de velocidad usa la misma medida estable que la tarjeta.
- La hora de fin ya no suma la revisión de integridad cuando no hay nada que revisar (todo lo
  copiado es nuevo y ya se verificó al copiarlo).

## 1.9.4

- **Tiempo restante y hora de fin mucho más precisos.** El cálculo aprende de la propia copia
  cuánto cuesta cada byte y cuánto cada archivo (abrir, crear, verificar, renombrar), y lo ajusta
  sobre la marcha dando más peso a lo reciente. Antes cada archivo contaba como 1 MB fijo, y en
  una carpeta de red con miles de archivos pequeños el error podía ser de horas. Parte de lo
  aprendido en la copia anterior al mismo destino, así que acierta desde el principio; mientras
  tiene pocos datos lo indica con «afinando».
- La hora de fin incluye la revisión de integridad (como mucho el tiempo elegido) y lo dice.
- **Velocidad estable:** media de los últimos segundos de copia, sin contar pausas ni esperas al
  disco, con los archivos/s y los hilos que están copiando. Ya no cae a «0 B/s» mientras se
  verifican archivos.
- El tiempo previsto al conectar el disco usa el mismo modelo.

## 1.9.3

- **Se ve si estás conectado a tu nube.** Debajo del destino aparece «✓ Conectado a tu nube «pcspi»»
  (o que no responde ahora), y el botón pasa a *Volver a conectar…*. La app recuerda tu nube: al
  volver a conectar ya sale puesta.
- **Encuentra la nube aunque el cortafuegos de Windows bloquee la app.** La búsqueda usa también el
  servicio de descubrimiento del propio Windows, y prueba directamente la nube ya conocida.
- La carpeta de red de Personal Cloud Storage se llama ahora `DEV_Backup_Studio`. Un destino con el
  nombre antiguo (`\\pcspi.local\Copias\…`) se cambia solo al nuevo, si es la misma carpeta.

## 1.9.2

- **Marcar todo y Desmarcar todo** en *Qué se copia*, en lugar de *Restablecer*. Sin selección
  actúan sobre todo el árbol; con carpetas seleccionadas (Ctrl o Mayús + clic) solo sobre ellas y lo
  que tienen dentro, y el botón lo dice («Marcar 2 seleccionadas»). Se desactivan cuando no hay nada
  que hacer. Las carpetas excluidas por regla no cambian.

## 1.9.1

- Corregido: el diálogo *Conectar con mi nube* no se podía cerrar ni terminar la conexión («native
  Qt signal is not callable»). Una comprobación nueva del autodiagnóstico evita que vuelva a pasar.
- Si el código es correcto pero falla abrir la carpeta de red, *Conectar* reintenta sin pedir otro
  código (el código se gasta al usarlo).

## 1.9.0

- **Conectar con tu nube con un código.** Debajo del destino, *Conectar con mi nube…* busca
  Personal Cloud Storage en la red de casa y pide el código de 6 cifras que da su web (Ajustes →
  *Copias de tus ordenadores* → *Conectar un ordenador*). La app recibe la carpeta de red, guarda la
  credencial en el sistema (Administrador de credenciales de Windows, llavero en macOS), crea la
  carpeta de este ordenador y la pone como destino. Sin contraseñas.
- **Copias en una carpeta de red.** El destino puede ser una carpeta compartida por SMB, como la
  carpeta `Copias` de Personal Cloud Storage: una unidad de red (`Z:`) o una ruta `\\servidor\recurso`
  en Windows, o un recurso montado en `/Volumes` en macOS. Versiones, verificación SHA-256, revisión
  de integridad y detección de movidos funcionan igual que en un disco USB.
- Antes de mirar una carpeta de red se comprueba en un segundo que el servidor responde. Con el
  servidor apagado, la app ya no se queda esperando: dice «Servidor de red no disponible».
- Cuando el servidor vuelve a la red, la app lo trata como un disco recién conectado y ofrece
  sincronizar. Si la red se corta a mitad, la copia espera y sigue sola.
- La vigilancia de discos va en un hilo aparte: la ventana nunca espera a la red.
- En destinos de red se admite una diferencia de 2 s en la fecha de modificación (cada servidor la
  guarda con su precisión) y no se envía el latido que mantiene despierto un disco USB.
- Corregido: la verificación sin caché fallaba en las carpetas de red con «El parámetro no es
  correcto» (SMB no admite leer sin caché más allá del final del archivo).

## 1.8.0

- **Seis idiomas.** Español, inglés, francés, italiano, chino (simplificado) y japonés. Por defecto la
  app usa el idioma del sistema; en *Sistema → Idioma* se puede elegir otro y la app ofrece reiniciarse
  para aplicarlo (el aviso aparece ya en el idioma elegido). Los ajustes y las copias no cambian.
- Todo traducido: ventana, avisos, notificaciones, diálogos, el informe de cada copia y la guía rápida,
  cuyas capturas están hechas con la app en cada idioma.
- Números, porcentajes y fechas con el formato de cada idioma, y letra propia para chino y japonés.
- Los recordatorios reconocen los registros de copias escritos en cualquiera de los idiomas.

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
