# Lavadero V4 — Railway

Aplicación Flask para lavadero de coches con PostgreSQL. Esta versión añade marca, modelo y empresa/cliente al coche, y migra automáticamente columnas nuevas en bases existentes.

## Railway
1. Sube este proyecto a un repositorio de GitHub.
2. En Railway, dentro del proyecto que ya tiene Postgres, selecciona **GitHub Repository** y el repositorio.
3. Railway debe proporcionar `DATABASE_URL` al servicio si se referencia la base PostgreSQL.
4. Añade una variable `SECRET_KEY` con un valor aleatorio largo.
5. Despliega. El comando es `gunicorn app:app --bind 0.0.0.0:$PORT`.

La aplicación crea automáticamente las tablas y 10 usuarios de prueba al arrancar.

Credenciales iniciales de prueba:
admin / 1234
juan / 1234
pedro / 1234
antonio / 1234
manuel / 1234
carlos / 1234
david / 1234
miguel / 1234
jose / 1234
luis / 1234

IMPORTANTE: cambia las contraseñas antes de uso real.

## Empresas
La V6 añade una tabla de empresas/clientes con listado desplegable. Se crean inicialmente Particular, Empresa 1, Empresa 2 y Empresa 3; el administrador puede añadir, activar u ocultar empresas desde /companies.
