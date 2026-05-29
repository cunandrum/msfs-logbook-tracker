# Libro de Vuelo - Tracker Automatico

App de escritorio para Windows que registra tus vuelos en MSFS 2020 automaticamente.

## Como funciona

1. Descarga el `.exe` de la seccion **Releases**
2. Ejecutalo e inicia sesion con tu cuenta de Libro de Vuelo
3. Abre MSFS 2020
4. Clic en **Conectar a MSFS**
5. Vola — al aterrizar el vuelo se guarda automaticamente con:
   - Aeropuerto de salida
   - Aeropuerto de llegada
   - Duracion del vuelo
   - Touchdown rate (fpm)

## Requisitos

- Windows 10/11
- Microsoft Flight Simulator 2020
- Cuenta en Libro de Vuelo (libro-de-vuelo-two.vercel.app)

## Desarrollo

```bash
pip install -r requirements.txt
python src/tracker.py
```
