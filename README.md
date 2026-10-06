# Prueba controlada de carga HTTP y LDAPS

Este repositorio contiene un ejecutor de carga independiente de las imágenes de LDAP, backend y frontend. Permite probar endpoints HTTP y binds TLS/LDAP en un laboratorio propio. Rechaza destinos que resuelvan a direcciones públicas y limita duración, concurrencia y operaciones por segundo.

## Requisitos

- Python 3.10 o posterior, o Docker.
- Los servicios probados deben ser propios y estar aislados en una red local.
- No apuntar la prueba a sistemas ajenos ni a servicios públicos.

## Preparación

Con Python:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

También se puede construir la imagen de prueba independiente:

```sh
docker build -t trabajo7-ddos .
```

## Uso

Prueba HTTP contra un puerto publicado localmente:

```sh
python load_test.py --protocol http --host 127.0.0.1 --port 8080 --path / --duration 20 --concurrency 5 --rps 10
```

Ejemplo para el chequeo de salud del backend:

```sh
python load_test.py --protocol http --host 127.0.0.1 --port 5000 --path /api/health --duration 20 --concurrency 5 --rps 10
```

Prueba LDAPS controlada. Usa una identidad ficticia y una contraseña deliberadamente inválida para generar binds fallidos y ejercitar el jail del puerto TLS:

```sh
python load_test.py --protocol ldaps --host 127.0.0.1 --port 636 --bind-dn "uid=fail2ban-test,ou=users,dc=example,dc=com" --password "invalid-fail2ban-test-password" --duration 20 --concurrency 2 --rps 5
```

El modo LDAPS acepta certificados autofirmados para este laboratorio. No lo uses con credenciales reales ni como verificador de certificados de producción.

Límites incorporados: máximo 120 segundos, 100 workers y 200 operaciones/s. Los valores predeterminados son 15 segundos, 5 workers y 10 operaciones/s. `--host` y `--port` son obligatorios; `--path` solo aplica a HTTP.

## Procedimiento para hallar la carga estable

1. Levantar el stack protegido y confirmar que cada servicio responde a una petición individual.
2. Comprobar que Fail2Ban está activo y guardar su estado inicial. Para LDAPS, confirmar además que el puerto publicado es TLS/636.
3. Ejecutar una línea base corta con pocos workers y baja tasa. Revisar en otra terminal el health check, logs y reinicios del contenedor.
4. Aumentar solo `--rps` entre corridas; mantener constantes host, puerto, ruta, duración y concurrencia. Después, repetir variando solo `--concurrency`.
5. En cada corrida registrar el comando, el total, operaciones/s reales, códigos/errores y el estado de Fail2Ban. Una IP baneada o un rechazo intencional demuestra que el jail actuó; por sí solo no demuestra que el servicio se haya caído.
6. Considerar estable una corrida si termina sin reinicio del servicio, el chequeo de salud desde una fuente no baneada sigue respondiendo y se cumple el criterio de disponibilidad acordado para la práctica. Registrar por separado las corridas donde Fail2Ban bloquea al generador.
7. Repetir el último nivel estable y el primer nivel no estable para reducir falsos resultados. Restaurar el jail/firewall y verificar el servicio al finalizar.

No elevar simultáneamente duración, concurrencia y tasa: así no se puede atribuir el resultado a un parámetro concreto. La tasa configurada es un máximo agregado; la tasa real aparece en el resumen `requests_per_second` y puede ser menor si el servidor tarda en responder.

## Resultados de esta ejecución

Completar esta tabla con salidas observadas en los contenedores de la práctica. No se deben copiar valores de ejemplo ni presentar resultados sin ejecutar las corridas.

| Servicio y protocolo | Host:puerto | Ruta o DN de prueba | Duración | Workers | Límite ops/s | Ops/s reales | Resultado / estado Fail2Ban | ¿Sigue sano? |
|---|---|---|---:|---:|---:|---:|---|---|
| Frontend (HTTP) | Pendiente | `/` | Pendiente | Pendiente | Pendiente | Pendiente | Pendiente | Pendiente |
| Backend (HTTP) | Pendiente | `/api/health` | Pendiente | Pendiente | Pendiente | Pendiente | Pendiente | Pendiente |
| Directorio LDAP (LDAPS) | Pendiente | DN ficticio | Pendiente | Pendiente | Pendiente | Pendiente | Pendiente | Pendiente |

## Evidencia sugerida

Guardar la salida completa de cada corrida, el estado de `fail2ban-client status` antes/después, logs relevantes, `docker compose ps` antes/después y el resultado de un health check después de retirar un ban. Ocultar contraseñas, secretos y datos personales.