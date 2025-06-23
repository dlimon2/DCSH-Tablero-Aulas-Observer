# DCSH UAMX: Tablero digital de asignación de aulas
El presente proyecto es una solución para mostrar, en un sitio web, la asignación de aulas en tiempo real, usando como base una hoja de cálculo maestra.

Autor: Daniel Limón <dani@dlimon.net>

## Desarrollo actual:
* Script observador de Google Sheets: Script de Python que mapea y observa cambios en la hoja de cálculo maestra. El mapeo y cambios son serializados y enviados a una API HTTP cuando se requiere.

* API HTTP con WebSockets: Proyecto de FastAPI (Python) que sirve para recibir los datos del script cuando se carga por primera vez o cuando hay cambios, y enviarlos a un servicio frontend a través de WebSockets para poder mantener un flujo de tiempo real.

* Sitio web con Tablero digital: Proyecto de ReactJS (JavaScript) que muestra la asginación de aulas recibida por WebSockets a través de la API HTTP. Se utilizan WebSockets para obtener y mostrar cambios en tiempo real.

~💎D
