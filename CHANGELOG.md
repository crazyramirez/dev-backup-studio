# Novedades

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
