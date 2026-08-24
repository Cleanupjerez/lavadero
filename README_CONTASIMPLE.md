V14 Contasimple.
Incluye OAuth2 Authorization Code + offline_access, tarifas por empresa/servicio,
registro de sincronización y reintentos, y llamada automática al finalizar un coche.

Variables Railway:
CONTASIMPLE_SITE_URL
CONTASIMPLE_API_URL
CONTASIMPLE_CLIENT_ID
CONTASIMPLE_CLIENT_SECRET
CONTASIMPLE_REDIRECT_URI
CONTASIMPLE_ALBARAN_ENDPOINT

Importante: la documentación oficial de Contasimple confirma OAuth2 y Client ID/Secret,
pero el endpoint concreto de creación de albaranes debe ser proporcionado/habilitado
para la integración. Por seguridad, no se inventa ese endpoint.
