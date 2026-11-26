# 0001 · API sin estado

Fecha: 2026-11-25 · Estado: aceptada · Sesión 1

## Contexto

El HR Assistant debe poder crecer corriendo más copias detrás de un balanceador. Si una copia guardara la
conversación en memoria, la siguiente pregunta podría llegar a otra copia que no la conoce.

## Decisión

- La API no guarda nada entre peticiones: ni conversaciones, ni sesiones, ni caché propia.
- El historial lo guarda el navegador y lo manda en cada `POST /ask` (`history`, opcional).
- `GET /health` responde `{"status": "ok", "pid": <id del proceso>}`; el `pid` permite ver qué copia contestó.
- El directorio de empleados es de solo lectura y se carga una vez al arrancar.

## Consecuencias

- Escalar es correr más copias; cualquier copia puede contestar cualquier pregunta.
- El cliente controla el historial: puede mandarlo largo o alterado. Hace falta validarlo (rol permitido) y, en
  sesiones siguientes, limitar su tamaño y vigilar la inyección de instrucciones.
- Cada petición vuelve a enviar el historial al modelo: más tokens con conversaciones largas (se atiende con la
  ventana de historial cuando llegue la optimización de costos).
