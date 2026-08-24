V14 REAL - Integración Contasimple sobre V13 completo.

Se añade al programa actual:
- /admin/contasimple
- pantalla de credenciales editable por administrador
- tarifas base por empresa/servicio
- OAuth2 + refresh token
- registro/reintento de albaranes
- intento automático al finalizar un coche

La app conserva PostgreSQL existente y sus tablas. No sustituye la base de datos.
IMPORTANTE: el endpoint de creación de albaranes debe ser el que Contasimple habilite/proporcione para la integración.

V14 URL: solo la URL del portal es obligatoria en la pantalla; API, Client ID, Client Secret, Redirect URI y endpoint pueden dejarse vacíos y completarse después.
