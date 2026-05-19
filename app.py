import re
import csv
import io
import html
import requests
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, Response, request

app = Flask(__name__)

URL = "https://aviationweather.gov/api/data/isigmet"

CACHE = {}
CACHE_MINUTOS = 15


def generar_fechas(inicio, fin, paso_minutos):
    fechas = []
    fecha = inicio
    while fecha <= fin:
        fechas.append(fecha.strftime("%Y%m%d_%H%M"))
        fecha += timedelta(minutes=paso_minutos)
    return fechas


def consultar_fecha(fecha_api):
    params = {
        "format": "json",
        "date": fecha_api
    }

    try:
        r = requests.get(URL, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()

        salida = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item:
                    item["consulta_utc"] = fecha_api
                    salida.append(item)

        return salida

    except Exception as e:
        print(f"Error consultando {fecha_api}: {e}")
        return []


def parse_receipt_time(valor):
    try:
        return datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except Exception:
        return None


def extraer_validez(raw):
    texto = str(raw).replace("\n", " ")
    m = re.search(r"\bVALID\s+(\d{6})/(\d{6})\b", texto)

    if not m:
        return ""

    return f"{m.group(1)}/{m.group(2)}"


def armar_fenomeno(item):
    qualifier = str(item.get("qualifier", "")).strip()
    hazard = str(item.get("hazard", "")).strip()

    if qualifier.lower() in ["nan", "none", ""]:
        return hazard

    return f"{qualifier} {hazard}"


def normalizar_fir(item):
    fir_id = str(item.get("firId", "")).strip()
    fir_name = str(item.get("firName", "")).strip()

    if fir_id == "SACF" or fir_name.startswith("SACF"):
        return "SACO"

    if fir_id == "SAMF" or fir_name.startswith("SAMF"):
        return "SAME"

    return fir_name


def extraer_dia_generacion(raw):
    texto = str(raw).strip()
    primera_linea = texto.splitlines()[0] if texto else ""

    m = re.search(r"\b[A-Z]{4}\d{2}\s+[A-Z]{4}\s+(\d{2})\d{4}\b", primera_linea)

    if m:
        return m.group(1)

    return ""


def generar_tabla(horas_atras=24, paso_minutos=120, texto_a_buscar="SACO"):
    ahora = datetime.now(timezone.utc)
    inicio = ahora - timedelta(hours=horas_atras)

    fechas = generar_fechas(inicio, ahora, paso_minutos)

    todos = []

    # Consultas en paralelo para que no quede cargando tanto.
    max_workers = 6

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futuros = [executor.submit(consultar_fecha, fecha_api) for fecha_api in fechas]

        for futuro in as_completed(futuros):
            todos.extend(futuro.result())

    # Eliminar duplicados por rawSigmet.
    vistos = set()
    unicos = []

    for item in todos:
        raw = str(item.get("rawSigmet", "")).strip()

        if not raw:
            continue

        if raw in vistos:
            continue

        vistos.add(raw)
        unicos.append(item)

    filas = []

    for item in unicos:
        raw = str(item.get("rawSigmet", ""))

        # Filtrar por texto, por ejemplo SACO.
        if texto_a_buscar and texto_a_buscar.lower() not in raw.lower():
            continue

        # Filtrar por recepción dentro del período.
        receipt_dt = parse_receipt_time(item.get("receiptTime", ""))

        if receipt_dt is not None and receipt_dt < inicio:
            continue

        filas.append({
            "FIR Name": normalizar_fir(item),
            "Día generación": extraer_dia_generacion(raw),
            "Número / Tipo": item.get("seriesId", ""),
            "Fenómeno": armar_fenomeno(item),
            "Validez original": extraer_validez(raw),
        })

    filas = sorted(filas, key=lambda x: x.get("Validez original", ""))

    return filas


def filas_a_csv(filas):
    salida = io.StringIO()
    campos = ["FIR Name", "Día generación", "Número / Tipo", "Fenómeno", "Validez original"]
    writer = csv.DictWriter(salida, fieldnames=campos)
    writer.writeheader()

    for fila in filas:
        writer.writerow(fila)

    return salida.getvalue()


def filas_a_tabla_html(filas):
    if not filas:
        return '<div class="sin-datos">No se encontraron SIGMET para el filtro solicitado.</div>'

    encabezados = ["FIR Name", "Día generación", "Número / Tipo", "Fenómeno", "Validez original"]

    thead = "".join(f"<th>{html.escape(col)}</th>" for col in encabezados)

    filas_html = ""

    for fila in filas:
        celdas = "".join(
            f"<td>{html.escape(str(fila.get(col, '')))}</td>"
            for col in encabezados
        )
        filas_html += f"<tr>{celdas}</tr>"

    return f"""
    <table>
        <thead>
            <tr>{thead}</tr>
        </thead>
        <tbody>
            {filas_html}
        </tbody>
    </table>
    """


def pagina_html(filas, horas_atras, paso_minutos, texto_a_buscar, desde_cache=False):
    actualizacion = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    tabla_html = filas_a_tabla_html(filas)

    cache_txt = "sí" if desde_cache else "no"

    return f"""
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <title>SIGMET {html.escape(texto_a_buscar)}</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                background: #f4f7fb;
                margin: 0;
                padding: 30px;
                color: #222;
            }}

            .contenedor {{
                max-width: 1050px;
                margin: auto;
                background: white;
                padding: 25px;
                border-radius: 14px;
                box-shadow: 0 4px 18px rgba(0,0,0,0.12);
            }}

            h1 {{
                margin-top: 0;
                color: #003b73;
            }}

            .info {{
                margin-bottom: 20px;
                color: #555;
                font-size: 14px;
                line-height: 1.5;
            }}

            .boton {{
                display: inline-block;
                background: #003b73;
                color: white;
                padding: 10px 16px;
                border-radius: 8px;
                text-decoration: none;
                margin-bottom: 20px;
            }}

            table {{
                width: 100%;
                border-collapse: collapse;
                font-size: 15px;
            }}

            th {{
                background: #003b73;
                color: white;
                padding: 10px;
                text-align: left;
            }}

            td {{
                padding: 9px;
                border-bottom: 1px solid #ddd;
            }}

            tr:nth-child(even) {{
                background: #f2f6fb;
            }}

            .sin-datos {{
                padding: 20px;
                background: #fff3cd;
                border-radius: 8px;
                color: #856404;
            }}
        </style>
    </head>
    <body>
        <div class="contenedor">
            <h1>SIGMET {html.escape(texto_a_buscar)}</h1>

            <div class="info">
                Última actualización: {actualizacion}<br>
                Período consultado: últimas {horas_atras} horas<br>
                Paso de consulta: cada {paso_minutos} minutos<br>
                Orden: más viejos primero<br>
                Resultado desde caché: {cache_txt}
            </div>

            <a class="boton" href="/csv?horas={horas_atras}&paso={paso_minutos}&buscar={html.escape(texto_a_buscar)}">Descargar CSV</a>

            {tabla_html}
        </div>
    </body>
    </html>
    """


def obtener_datos(horas_atras, paso_minutos, texto_a_buscar):
    clave = (horas_atras, paso_minutos, texto_a_buscar.upper())
    ahora = datetime.now(timezone.utc)

    if clave in CACHE:
        edad = ahora - CACHE[clave]["timestamp"]
        if edad.total_seconds() < CACHE_MINUTOS * 60:
            return CACHE[clave]["filas"], True

    filas = generar_tabla(
        horas_atras=horas_atras,
        paso_minutos=paso_minutos,
        texto_a_buscar=texto_a_buscar
    )

    CACHE[clave] = {
        "timestamp": ahora,
        "filas": filas
    }

    return filas, False


@app.route("/")
def home():
    horas_atras = int(request.args.get("horas", 24))
    paso_minutos = int(request.args.get("paso", 120))
    texto_a_buscar = request.args.get("buscar", "SACO").strip()

    filas, desde_cache = obtener_datos(horas_atras, paso_minutos, texto_a_buscar)

    return pagina_html(
        filas=filas,
        horas_atras=horas_atras,
        paso_minutos=paso_minutos,
        texto_a_buscar=texto_a_buscar,
        desde_cache=desde_cache
    )


@app.route("/csv")
def descargar_csv():
    horas_atras = int(request.args.get("horas", 24))
    paso_minutos = int(request.args.get("paso", 120))
    texto_a_buscar = request.args.get("buscar", "SACO").strip()

    filas, _ = obtener_datos(horas_atras, paso_minutos, texto_a_buscar)
    csv_texto = filas_a_csv(filas)

    return Response(
        csv_texto,
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=tabla_sigmet.csv"
        }
    )


@app.route("/health")
def health():
    return "OK"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
