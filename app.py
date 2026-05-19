import requests
import pandas as pd
import re
from datetime import datetime, timedelta, timezone
from flask import Flask, Response, request

app = Flask(__name__)

URL = "https://aviationweather.gov/api/data/isigmet"

CACHE = {
    "timestamp": None,
    "html": None,
    "csv": None
}

CACHE_MINUTOS = 10


def extraer_validez_sigmet(raw):
    texto = str(raw).replace("\n", " ")
    m = re.search(r"\bVALID\s+(\d{6})/(\d{6})\b", texto)

    if not m:
        return pd.Series({"Validez original": ""})

    inicio_val = m.group(1)
    fin_val = m.group(2)

    return pd.Series({"Validez original": f"{inicio_val}/{fin_val}"})


def armar_fenomeno(row):
    qualifier = str(row.get("qualifier", "")).strip()
    hazard = str(row.get("hazard", "")).strip()

    if qualifier.lower() in ["nan", "none", ""]:
        return hazard

    return f"{qualifier} {hazard}"


def normalizar_fir(row):
    fir_id = str(row.get("firId", "")).strip()
    fir_name = str(row.get("firName", "")).strip()

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


def generar_tabla(horas_atras=96, paso_minutos=90, texto_a_buscar="SACO"):
    ahora = datetime.now(timezone.utc)
    inicio = ahora - timedelta(hours=horas_atras)

    todos = []
    fecha = inicio

    while fecha <= ahora:
        fecha_api = fecha.strftime("%Y%m%d_%H%M")

        params = {
            "format": "json",
            "date": fecha_api
        }

        try:
            r = requests.get(URL, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()

            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item:
                        item["consulta_utc"] = fecha_api
                        todos.append(item)

        except Exception as e:
            print("Error en", fecha_api, ":", e)

        fecha += timedelta(minutes=paso_minutos)

    df = pd.DataFrame(todos)

    columnas = [
        "FIR Name",
        "Día generación",
        "Número / Tipo",
        "Fenómeno",
        "Validez original"
    ]

    if df.empty:
        return pd.DataFrame(columns=columnas)

    if "rawSigmet" in df.columns:
        df = df.drop_duplicates(subset=["rawSigmet"])
    else:
        df = df.drop_duplicates()

    if "receiptTime" in df.columns:
        df["receiptTime"] = pd.to_datetime(df["receiptTime"], errors="coerce", utc=True)
        df = df[df["receiptTime"] >= inicio].copy()

    if texto_a_buscar:
        df = df[
            df["rawSigmet"].astype(str).str.contains(
                texto_a_buscar,
                case=False,
                na=False
            )
        ].copy()

    if df.empty:
        return pd.DataFrame(columns=columnas)

    df[["Validez original"]] = df["rawSigmet"].apply(extraer_validez_sigmet)
    df["Fenómeno"] = df.apply(armar_fenomeno, axis=1)
    df["FIR mostrado"] = df.apply(normalizar_fir, axis=1)
    df["Día generación"] = df["rawSigmet"].apply(extraer_dia_generacion)

    tabla_sigmet = pd.DataFrame({
        "FIR Name": df["FIR mostrado"],
        "Día generación": df["Día generación"],
        "Número / Tipo": df["seriesId"],
        "Fenómeno": df["Fenómeno"],
        "Validez original": df["Validez original"]
    })

    tabla_sigmet = tabla_sigmet.sort_values(
        "Validez original",
        ascending=True
    )

    return tabla_sigmet


def tabla_a_html(tabla, horas_atras, texto_a_buscar):
    actualizacion = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")

    tabla_html = tabla.to_html(
        index=False,
        classes="tabla",
        border=0
    )

    html = f"""
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <title>SIGMET {texto_a_buscar}</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                background: #f4f7fb;
                margin: 0;
                padding: 30px;
                color: #222;
            }}

            .contenedor {{
                max-width: 1100px;
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
            <h1>SIGMET {texto_a_buscar}</h1>

            <div class="info">
                Última actualización: {actualizacion}<br>
                Período consultado: últimas {horas_atras} horas<br>
                Orden: más viejos primero
            </div>

            <a class="boton" href="/csv">Descargar CSV</a>

            {tabla_html if not tabla.empty else '<div class="sin-datos">No se encontraron SIGMET para el filtro solicitado.</div>'}
        </div>
    </body>
    </html>
    """

    return html


@app.route("/")
def home():
    horas_atras = int(request.args.get("horas", 96))
    paso_minutos = int(request.args.get("paso", 90))
    texto_a_buscar = request.args.get("buscar", "SACO")

    ahora = datetime.now(timezone.utc)

    usar_cache = False

    if CACHE["timestamp"] is not None:
        diferencia = ahora - CACHE["timestamp"]
        if diferencia.total_seconds() < CACHE_MINUTOS * 60:
            usar_cache = True

    if usar_cache and CACHE["html"] is not None:
        return CACHE["html"]

    tabla = generar_tabla(
        horas_atras=horas_atras,
        paso_minutos=paso_minutos,
        texto_a_buscar=texto_a_buscar
    )

    html = tabla_a_html(tabla, horas_atras, texto_a_buscar)
    csv = tabla.to_csv(index=False, encoding="utf-8-sig")

    CACHE["timestamp"] = ahora
    CACHE["html"] = html
    CACHE["csv"] = csv

    return html


@app.route("/csv")
def descargar_csv():
    if CACHE["csv"] is None:
        tabla = generar_tabla()
        csv = tabla.to_csv(index=False, encoding="utf-8-sig")
    else:
        csv = CACHE["csv"]

    return Response(
        csv,
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=tabla_sigmet_SACO.csv"
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
