import streamlit as st
import pandas as pd
import math
import json
from datetime import datetime

# ══════════════════════════════════════════════════════════════════════════════
# AUTENTICACIÓN — Usuarios desde Streamlit Secrets
# ══════════════════════════════════════════════════════════════════════════════

def cargar_usuarios():
    """Lee usuarios desde st.secrets['USUARIOS'].
    Formato: "email:password:nombre:rol|email2:password2:nombre2:rol2"
    """
    try:
        raw = st.secrets.get("USUARIOS", "")
    except Exception:
        raw = ""
    if not raw:
        return {"admin@jaan.com": {"password": "jaan2024", "nombre": "Administrador", "rol": "admin"}}
    usuarios = {}
    for entry in raw.split("|"):
        parts = entry.strip().split(":")
        if len(parts) >= 4:
            usuarios[parts[0].strip()] = {
                "password": parts[1].strip(),
                "nombre":   parts[2].strip(),
                "rol":      parts[3].strip(),
            }
    return usuarios


def login_screen():
    st.markdown("""
    <div style='max-width:400px;margin:80px auto;padding:40px;
                background:white;border-radius:16px;
                border:0.5px solid #dde1ea;
                box-shadow:0 4px 24px rgba(0,0,0,0.08)'>
        <div style='text-align:center;margin-bottom:32px'>
            <h2 style='color:#0f1b3d;margin:0;font-weight:500'>JAAN Manufacturing</h2>
            <p style='color:#6b7280;font-size:13px;margin:6px 0 0'>Cotizador Profesional</p>
        </div>
    </div>
    """, unsafe_allow_html=True)
    with st.form("login_form"):
        st.markdown("### Iniciar sesión")
        email    = st.text_input("Correo electrónico", placeholder="usuario@jaan.com")
        password = st.text_input("Contraseña", type="password")
        submit   = st.form_submit_button("Entrar", use_container_width=True)
        if submit:
            if not email or not password:
                st.error("Ingresa tu correo y contraseña")
                return
            usuarios = cargar_usuarios()
            email_key = email.strip().lower()
            if email_key in usuarios and usuarios[email_key]["password"] == password:
                st.session_state.usuario     = {"email": email_key, "nombre": usuarios[email_key]["nombre"], "rol": usuarios[email_key]["rol"]}
                st.session_state.autenticado = True
                st.rerun()
            else:
                st.error("❌ Credenciales incorrectas")


if not st.session_state.get("autenticado", False):
    login_screen()
    st.stop()

st.set_page_config(page_title="Cotizador JAAN Manufacturing", page_icon="⚙️",
                   layout="wide", initial_sidebar_state="expanded")

# ══════════════════════════════════════════════════════════════════════════════
# GOOGLE SHEETS — Guardar y cargar cotizaciones
# ══════════════════════════════════════════════════════════════════════════════

def get_gsheet_token():
    """Obtiene token de acceso OAuth2 para Google Sheets API"""
    import json, time
    import requests
    try:
        raw = st.secrets.get("GSHEET_CREDENTIALS", "")
        if not raw:
            try:
                creds_dict = dict(st.secrets["gcp_service_account"])
                raw = json.dumps(creds_dict)
            except Exception:
                return None, "No se encontraron credenciales"
        creds_dict = json.loads(raw)
        
        # Crear JWT para obtener access token
        import base64, hashlib
        from cryptography.hazmat.primitives import serialization, hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.backends import default_backend
        
        now = int(time.time())
        header = base64.urlsafe_b64encode(json.dumps({"alg":"RS256","typ":"JWT"}).encode()).rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(json.dumps({
            "iss": creds_dict["client_email"],
            "scope": "https://www.googleapis.com/auth/spreadsheets https://www.googleapis.com/auth/drive",
            "aud": "https://oauth2.googleapis.com/token",
            "exp": now + 3600,
            "iat": now
        }).encode()).rstrip(b"=").decode()
        
        msg = f"{header}.{payload}".encode()
        private_key = serialization.load_pem_private_key(
            creds_dict["private_key"].encode(), password=None, backend=default_backend())
        sig = base64.urlsafe_b64encode(
            private_key.sign(msg, padding.PKCS1v15(), hashes.SHA256())).rstrip(b"=").decode()
        
        jwt = f"{header}.{payload}.{sig}"
        resp = requests.post("https://oauth2.googleapis.com/token", data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": jwt
        })
        token_data = resp.json()
        if "access_token" not in token_data:
            return None, f"Error token: {token_data}"
        return token_data["access_token"], None
    except Exception as e:
        return None, str(e)


def get_or_create_sheet_id(token):
    """Obtiene el sheet ID desde Secrets o crea uno nuevo"""
    import requests, json
    sheet_id = st.secrets.get("GSHEET_ID", "").strip()
    if sheet_id:
        # Verificar acceso
        r = requests.get(
            f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}",
            headers={"Authorization": f"Bearer {token}"})
        if r.status_code == 200:
            return sheet_id, None
    return None, f"Sheet no accesible con ID: {sheet_id}"


GSHEET_HEADERS = ["numero","fecha","usuario_email","cliente","atencion",
                  "direccion","cp","ciudad","pais","moneda","tipo_cambio","margen_global",
                  "subtotal","iva","total_neto","vigencia",
                  "tiempo_entrega","cond_pago","datos_json"]

def _get_token_and_sheet():
    """Helper: retorna (token, sheet_id, error)"""
    token, err = get_gsheet_token()
    if not token:
        return None, None, err
    sheet_id, err2 = get_or_create_sheet_id(token)
    if not sheet_id:
        return None, None, err2
    return token, sheet_id, None

def _ensure_headers(token, sheet_id):
    """Crea la fila de encabezados si el sheet está vacío"""
    import requests
    check = requests.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/A1",
        headers={"Authorization": f"Bearer {token}"})
    if check.status_code == 200 and not check.json().get("values"):
        requests.post(
            f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/A1:append?valueInputOption=RAW",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"values": [GSHEET_HEADERS]})

def _find_row_number(token, sheet_id, numero_cot):
    """Busca en columna A el número de cotización. Retorna número de fila (1-based) o None."""
    import requests
    resp = requests.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/A2:A2000",
        headers={"Authorization": f"Bearer {token}"})
    if resp.status_code != 200:
        return None
    rows = resp.json().get("values", [])
    for i, row in enumerate(rows, 2):   # fila 2 en adelante (1 es header)
        if row and row[0].strip() == numero_cot.strip():
            return i
    return None

def subir_plano_drive(file_bytes, filename, mime_type="application/pdf"):
    """Sube un archivo a Google Drive del Service Account y retorna el file_id."""
    import requests, json as _json
    token, err = get_gsheet_token()
    if not token:
        return None, f"Token error: {err}"

    # Obtener folder ID desde Secrets (carpeta compartida con el SA en Drive del usuario)
    folder_id = st.session_state.get("_drive_folder_id")
    if not folder_id:
        # Primero intentar desde Secrets (recomendado)
        folder_id = st.secrets.get("DRIVE_FOLDER_ID", "").strip()
        if not folder_id:
            # Fallback: buscar carpeta accesible por el SA
            r = requests.get(
                "https://www.googleapis.com/drive/v3/files",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "q": "name='JAAN-Planos' and mimeType='application/vnd.google-apps.folder' and trashed=false",
                    "fields": "files(id,name)", "spaces": "drive"
                }
            )
            if r.status_code == 200:
                files = r.json().get("files", [])
                if files:
                    folder_id = files[0]["id"]
        if folder_id:
            st.session_state["_drive_folder_id"] = folder_id

    # Subir archivo multipart
    metadata = _json.dumps({"name": filename, "parents": [folder_id] if folder_id else []})
    boundary = "jaan_boundary_xyz"
    CRLF = "\r\n"
    body = (
        f"--{boundary}{CRLF}Content-Type: application/json; charset=UTF-8{CRLF}{CRLF}"
        f"{metadata}{CRLF}"
        f"--{boundary}{CRLF}Content-Type: {mime_type}{CRLF}{CRLF}"
    ).encode() + file_bytes + f"{CRLF}--{boundary}--".encode()

    resp = requests.post(
        "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id,name",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": f"multipart/related; boundary={boundary}"},
        data=body
    )
    if resp.status_code in (200, 201):
        return resp.json().get("id"), None
    return None, f"Error Drive {resp.status_code}: {resp.text[:300]}"
def descargar_plano_drive(file_id):
    """Descarga un archivo de Google Drive por su file_id"""
    import requests
    token, err = get_gsheet_token()
    if not token:
        return None, err
    resp = requests.get(
        f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media",
        headers={"Authorization": f"Bearer {token}"}
    )
    if resp.status_code == 200:
        return resp.content, None
    return None, f"Error descargando: {resp.status_code}"


def append_to_gsheet(values):
    """Agrega una fila nueva al Google Sheet"""
    import requests
    token, sheet_id, err = _get_token_and_sheet()
    if not token:
        return False, err
    _ensure_headers(token, sheet_id)
    url = (f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}"
           f"/values/A1:append?valueInputOption=RAW&insertDataOption=INSERT_ROWS")
    resp = requests.post(url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"values": [values]})
    if resp.status_code == 200:
        return True, None
    return False, f"Error {resp.status_code}: {resp.text[:200]}"

def update_gsheet_row(numero_cot, values):
    """Actualiza la fila existente de una cotización. Si no existe, la agrega."""
    import requests
    token, sheet_id, err = _get_token_and_sheet()
    if not token:
        return False, err
    _ensure_headers(token, sheet_id)
    row_num = _find_row_number(token, sheet_id, numero_cot)
    if row_num is None:
        # No existe → insertar como nueva
        return append_to_gsheet(values)
    # Existe → sobreescribir esa fila completa
    n_cols = len(values)
    end_col = chr(ord("A") + n_cols - 1) if n_cols <= 26 else "Z"
    range_str = f"A{row_num}:{end_col}{row_num}"
    resp = requests.put(
        f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{range_str}?valueInputOption=RAW",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"values": [values]})
    if resp.status_code == 200:
        return True, None
    return False, f"Error actualizando fila {row_num}: {resp.status_code} {resp.text[:200]}"


def get_gsheet():
    """Compatibilidad — retorna None para usar append_to_gsheet directamente"""
    return None, "usar append_to_gsheet"


# ── Verificar autenticación ───────────────────────────────────────────────────
if "autenticado" not in st.session_state:
    st.session_state.autenticado = False

if not st.session_state.autenticado:
    login_screen()
    st.stop()


st.markdown("""
<style>
    /* ── Header (navy fijo, funciona light y dark) ── */
    .main-header {
        background: #0f1b3d !important;
        padding: 18px 28px 16px; border-radius: 10px;
        margin-bottom: 20px; border-bottom: 3px solid #185FA5;
    }
    .main-header h1 { color:#fff !important; margin:0; font-size:20px; font-weight:500; }
    .main-header p  { color:rgba(255,255,255,0.5) !important; margin:4px 0 0; font-size:11px; text-transform:uppercase; letter-spacing:0.04em; }

    /* ── Cajas de resultado (navy fijo) ── */
    .result-box { background:#0f1b3d; border-radius:10px; padding:18px 20px; color:white; text-align:center; }
    .result-box .price { font-size:32px; font-weight:500; }
    .result-box .label { font-size:10px; opacity:0.6; text-transform:uppercase; letter-spacing:0.06em; }
    .semaforo-box { border-radius:8px; padding:10px 14px; margin-top:8px; font-size:13px; }
    .peso-result  { background:#EAF3DE; border:0.5px solid #639922; border-radius:8px; padding:10px 14px; font-weight:500; color:#3B6D11; font-size:13px; }
    .op-header    { font-size:10px; font-weight:500; letter-spacing:0.06em; text-transform:uppercase; }

    /* ── Pieza card (borde acento azul, fondo transparente) ── */
    .pieza-card {
        border: 0.5px solid rgba(24,95,165,0.3);
        border-left: 3px solid #185FA5;
        border-radius: 10px; padding: 16px 20px; margin-bottom: 14px;
    }

    /* ── Total box ── */
    .total-box {
        border: 0.5px solid rgba(24,95,165,0.3);
        border-left: 3px solid #185FA5;
        border-radius: 10px; padding: 18px 20px; text-align: center;
    }

    /* ── Botones ── */
    .stButton > button {
        background:#0f1b3d !important; color:white !important;
        border:none !important; border-radius:8px !important;
        padding:8px 20px !important; font-weight:500 !important;
        transition:background 0.15s !important;
    }
    .stButton > button:hover { background:#185FA5 !important; }

    /* ── Inputs con borde visible ── */
    input[class*="st-"], textarea[class*="st-"] {
        border: 2px solid #8fafd4 !important;
        border-radius: 6px !important;
    }
    input[class*="st-"]:focus, textarea[class*="st-"]:focus {
        border: 2px solid #185FA5 !important;
        box-shadow: 0 0 0 3px rgba(24,95,165,0.2) !important;
    }
    div[data-baseweb="select"] > div {
        border: 2px solid #8fafd4 !important;
        border-radius: 6px !important;
    }
    div[data-baseweb="select"] > div:focus-within {
        border: 2px solid #185FA5 !important;
        box-shadow: 0 0 0 3px rgba(24,95,165,0.2) !important;
    }

    /* ── Expanders — solo borde y acento, sin forzar fondo ── */
    div[data-testid="stExpander"] {
        border: 0.5px solid rgba(24,95,165,0.4) !important;
        border-left: 3px solid #185FA5 !important;
        border-radius: 8px !important;
        margin-bottom: 6px !important;
    }
    div[data-testid="stExpanderHeader"] {
        font-weight: 500 !important;
        font-size: 13px !important;
        padding: 10px 14px !important;
        border-radius: 8px !important;
    }
    div[data-testid="stExpanderHeader"] svg { stroke: #185FA5 !important; }
</style>
""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTES
# ══════════════════════════════════════════════════════════════════════════════
TIPOS_MAQUINA = [
    "Lathe 2 Axis",
    "Mill-Turn C Axis",
    "Mill-Turn CY Axis",
    "Center Mill 3 Axis",
    "Center Mill 4th Axis",
]

ICONOS_TIPO = {
    "Lathe 2 Axis":         "🔩",
    "Mill-Turn C Axis":     "🔧",
    "Mill-Turn CY Axis":    "⚙️",
    "Center Mill 3 Axis":   "🛠️",
    "Center Mill 4th Axis": "💎",
}

FACTOR_TIPO = {
    "Lathe 2 Axis":         1.04,
    "Mill-Turn C Axis":     1.22,
    "Mill-Turn CY Axis":    1.31,
    "Center Mill 3 Axis":   1.12,
    "Center Mill 4th Axis": 1.22,
}

DEPR_TIPO = {
    "Lathe 2 Axis":         43.18,
    "Mill-Turn C Axis":     48.36,
    "Mill-Turn CY Axis":    82.22,
    "Center Mill 3 Axis":   53.91,
    "Center Mill 4th Axis": 81.25,
}

TOTAL_FIJO_MES = 1_185_722

TRATAMIENTOS_LISTA = [
    "Ninguno", "Anodizado", "Pasivado", "Cromo Duro",
    "Chemical Conversion", "Temple", "Nitrurado",
    "Pintura en polvo", "Zincado", "Niquelado", "Otro"
]

FIGURAS = {
    "Redondo (barra)":     {"dims": ["Diámetro (mm)", "Longitud (mm)"],                           "icono": "⚫"},
    "Hexagonal":           {"dims": ["Dist. entre caras (mm)", "Longitud (mm)"],                  "icono": "⬡"},
    "Cuadrado (barra)":    {"dims": ["Lado (mm)", "Longitud (mm)"],                               "icono": "⬛"},
    "Rectangular (barra)": {"dims": ["Ancho (mm)", "Alto (mm)", "Longitud (mm)"],                "icono": "▬"},
    "Tubo redondo":        {"dims": ["Diámetro ext. (mm)", "Espesor pared (mm)", "Longitud (mm)"],"icono": "○"},
    "Tubo cuadrado":       {"dims": ["Lado ext. (mm)", "Espesor pared (mm)", "Longitud (mm)"],   "icono": "□"},
    "PTR (tubo rect.)":    {"dims": ["Ancho (mm)", "Alto (mm)", "Espesor (mm)", "Longitud (mm)"],"icono": "▭"},
    "Solera":              {"dims": ["Ancho (mm)", "Espesor (mm)", "Longitud (mm)"],              "icono": "▬"},
    "Ángulo (L)":          {"dims": ["Lado (mm)", "Espesor (mm)", "Longitud (mm)"],              "icono": "∟"},
    "Placa / Lámina":      {"dims": ["Ancho (mm)", "Largo (mm)", "Espesor (mm)"],                "icono": "▦"},
    "Tocho / Bloque":      {"dims": ["Ancho (mm)", "Alto (mm)", "Largo (mm)"],                   "icono": "🧱"},
    "Canal (U / C)":       {"dims": ["Ancho (mm)", "Alto (mm)", "Espesor (mm)", "Longitud (mm)"],"icono": "∪"},
    "Otro / Manual":       {"dims": [],                                                           "icono": "✏️"},
}

MATERIALES_DENSIDAD = {
    "Aluminio 6061-T6": 2700, "Aluminio 7075": 2810, "Aluminio 2024": 2780,
    "Inox 303": 7900, "Inox 304": 7900, "Inox 316": 7980, "Inox 416 / 420": 7700,
    "Acero 1018": 7850, "Acero 1045": 7850, "Acero 4140": 7850, "Acero 4340": 7850,
    "Acero D2 (herram.)": 7700, "Latón ASTM B16-360": 8500, "Bronce": 8800,
    "Cobre": 8960, "Titanio Gr.5": 4430, "Nylon / Plástico": 1150,
    "Delrin (POM)": 1410, "Otro": 7850,
}

# ══════════════════════════════════════════════════════════════════════════════
# FUNCIONES
# ══════════════════════════════════════════════════════════════════════════════
def calcular_precios_por_tipo(maq_activas, turnos, hrs_turno, dias_mes, eficiencia):
    """Calcula precio/hr para cada tipo según parámetros operativos"""
    hrs_prod = hrs_turno * turnos * dias_mes * (eficiencia / 100)
    hrs_prod = max(hrs_prod, 1)
    fijo_hr  = TOTAL_FIJO_MES / max(maq_activas, 1) / hrs_prod
    precios  = {}
    for tipo in TIPOS_MAQUINA:
        precio = round((DEPR_TIPO[tipo] + fijo_hr) * FACTOR_TIPO[tipo] / 50) * 50
        precios[tipo] = max(int(precio), 200)
    return precios, fijo_hr, hrs_prod


def calcular_semaforo(demanda_mensual, ciclo_hrs, setup_hrs,
                      hrs_turno, dias_mes, eficiencia, num_maquinas):
    """Calcula turnos necesarios y semáforo de viabilidad"""
    if demanda_mensual <= 0:
        return None
    hrs_req = (setup_hrs + demanda_mensual * ciclo_hrs) / max(num_maquinas, 1)
    hrs_1t  = hrs_turno * dias_mes * (eficiencia / 100)
    if hrs_req <= hrs_1t:
        t, s, st = 1, "🟢", "1 turno suficiente"
    elif hrs_req <= hrs_1t * 2:
        t, s, st = 2, "🟡", "Requiere 2 turnos"
    elif hrs_req <= hrs_1t * 3:
        t, s, st = 3, "🟠", "Requiere 3 turnos"
    else:
        t, s, st = 3, "🔴", "Insuficiente — considera más máquinas"
    util = (hrs_req / (hrs_1t * t)) * 100
    return {"turnos": t, "semaforo": s, "status": st,
            "hrs_req": hrs_req, "hrs_disp": hrs_1t * t, "utilizacion": util}


def calcular_volumen(figura, dims):
    try:
        d = [float(x) for x in dims]
        if figura == "Redondo (barra)":
            return math.pi * (d[0]/2)**2 * d[1] / 1000
        elif figura == "Hexagonal":
            s = d[0] / math.sqrt(3)
            return (3*math.sqrt(3)/2) * s**2 * d[1] / 1000
        elif figura == "Cuadrado (barra)":
            return d[0]**2 * d[1] / 1000
        elif figura == "Rectangular (barra)":
            return d[0] * d[1] * d[2] / 1000
        elif figura == "Tubo redondo":
            return math.pi/4 * (d[0]**2 - (d[0]-2*d[1])**2) * d[2] / 1000
        elif figura == "Tubo cuadrado":
            return (d[0]**2 - (d[0]-2*d[1])**2) * d[2] / 1000
        elif figura == "PTR (tubo rect.)":
            return (d[0]*d[1] - (d[0]-2*d[2])*(d[1]-2*d[2])) * d[3] / 1000
        elif figura == "Solera":
            return d[0] * d[1] * d[2] / 1000
        elif figura == "Ángulo (L)":
            return (2*d[0]*d[1] - d[1]**2) * d[2] / 1000
        elif figura in ["Placa / Lámina", "Tocho / Bloque"]:
            return d[0] * d[1] * d[2] / 1000
        elif figura == "Canal (U / C)":
            return (d[0]*d[2] + 2*(d[1]-d[2])*d[2]) * d[3] / 1000
        return 0
    except:
        return 0


def calcular_peso_kg(vol_cm3, densidad):
    return vol_cm3 * densidad / 1_000_000


def fmt(v, moneda="MXN", tc=17.31):
    """Formatea valor en la moneda seleccionada"""
    if moneda == "USD":
        return f"USD ${v/tc:,.2f}"
    return f"${v:,.2f}"


def nueva_operacion(idx):
    return {"id": idx, "label": f"Op {idx*10}",
            "tipo_maq": "Lathe 2 Axis",
            "num_maquinas": 1, "setup_hrs": 0.5,
            "ciclo_hrs": 0.25, "paralelo": False}


def nueva_materia_prima():
    return {"figura": "Redondo (barra)", "material": "Inox 303",
            "dims": [0.0,0.0,0.0,0.0], "precio_kg": 0.0,
            "desperdicio": 10.0, "costo_manual": 0.0, "modo": "Por tramo",
            "proveedor": "",
            "cotizacion_mp_nombre": "",
            "cotizacion_mp_b64": "",
            # Campos para cálculo por tramo (todo en pulgadas)
            "largo_tramo":   31.5,    # pulgadas
            "largo_pieza":    1.575,  # pulgadas
            "agarre":         0.984,  # pulgadas
            "costo_tramo":    0.0,
            # Campos para corte previo de barra larga
            "corte_previo":  False,
            "largo_barra":  144.0,    # pulgadas
            "largo_corte":   26.0}


def nueva_pieza(idx, defaults):
    """Crea pieza nueva heredando defaults del sidebar"""
    return {
        "id":            idx,
        "num_dibujo":    "",
        "descripcion":   "",
        "materia_prima": nueva_materia_prima(),
        "tratamiento":   "Ninguno",
        "costo_trat":    0.0,
        "dias_trat":     0,
        "proveedor_trat": "",
        "cotizacion_trat_nombre": "",
        "cotizacion_trat_b64": "",
        "plano_nombre": "",
        "plano_b64": "",
        "plano_drive_id": "",
        "plano_tipo": "",
        "cantidad":      10,
        "demanda_mensual": 0,
        "tipo_pedido":   "Pedido único",
        "moq":           0,
        "eau":           0,
        "margen_mo":     35,
        "margen_mat":    35,
        "margen_trat":   35,
        "usar_margen_global": False,
        # ── Parámetros de operación PROPIOS de esta pieza ──
        "maq_activas":   defaults["maq_activas"],
        "turnos":        defaults["turnos"],
        "hrs_turno":     defaults["hrs_turno"],
        "dias_mes":      defaults["dias_mes"],
        "eficiencia":    defaults["eficiencia"],
        "operaciones":   [nueva_operacion(1)],
    }


def calcular_pieza(pieza, margen_pct):
    """Cálculo completo usando los parámetros operativos PROPIOS de la pieza"""
    ops      = pieza["operaciones"]
    cantidad = pieza["cantidad"]
    mp       = pieza["materia_prima"]

    # Parámetros operativos de ESTA pieza
    maq_act  = pieza.get("maq_activas", 7)
    turnos   = pieza.get("turnos",      1)
    hrs_t    = pieza.get("hrs_turno",   8)
    dias_m   = pieza.get("dias_mes",    21)
    efic     = pieza.get("eficiencia",  65)

    # Precios calculados con los parámetros de ESTA pieza
    precios, fijo_hr, hrs_prod = calcular_precios_por_tipo(
        maq_act, turnos, hrs_t, dias_m, efic
    )

    # Costo material
    if mp["modo"] == "Manual":
        costo_mat = mp["costo_manual"]
    elif mp["modo"] == "Por tramo":
        # Todo en pulgadas
        KERF_IN      = 4.0 / 25.4   # 4mm en pulgadas
        largo_pieza  = mp.get("largo_pieza",  1.575)
        agarre       = mp.get("agarre",       0.984)
        corte_previo = mp.get("corte_previo", False)

        if corte_previo:
            largo_barra  = mp.get("largo_barra", 144.0)
            largo_corte  = mp.get("largo_corte",  26.0)
            costo_barra  = mp.get("costo_tramo",   0.0)
            tramos       = int(largo_barra / (largo_corte + KERF_IN))
            costo_tramo  = costo_barra / tramos if tramos > 0 else 0
            largo_tramo  = largo_corte
        else:
            largo_tramo  = mp.get("largo_tramo", 31.5)
            costo_tramo  = mp.get("costo_tramo",  0.0)

        largo_util   = largo_tramo - agarre
        piezas_tramo = int(largo_util / (largo_pieza + KERF_IN))
        costo_mat    = costo_tramo / piezas_tramo if piezas_tramo > 0 else 0
    else:
        dims_label = FIGURAS[mp["figura"]]["dims"]
        vol        = calcular_volumen(mp["figura"], mp["dims"][:len(dims_label)])
        peso       = calcular_peso_kg(vol, MATERIALES_DENSIDAD.get(mp["material"], 7850))
        costo_mat  = peso * (1 + mp["desperdicio"]/100) * mp["precio_kg"]

    # Operaciones
    resultados = []
    for op in ops:
        tipo_maq  = op.get("tipo_maq", "Lathe 2 Axis")
        precio_hr = precios.get(tipo_maq, 1200) * op["num_maquinas"]
        setup_pza = op["setup_hrs"] / max(cantidad, 1)
        total_pza = setup_pza + op["ciclo_hrs"]
        costo_pza = total_pza * precio_hr
        resultados.append({**op, "tipo_maq": tipo_maq,
                           "precio_hr": precio_hr, "setup_pza": setup_pza,
                           "total_pza": total_pza, "costo_pza": costo_pza})

    # Camino crítico — serie o paralelo
    etapas = []
    for op in resultados:
        if not op["paralelo"] or not etapas:
            etapas.append([op])
        else:
            etapas[-1].append(op)
    tiempo_pza = sum(max(o["total_pza"] for o in e) for e in etapas)
    costo_maq  = sum(max(o["costo_pza"] for o in e) for e in etapas)

    # Semáforo de demanda
    demanda    = pieza.get("demanda_mensual", 0)
    ciclo_ref  = max((op["ciclo_hrs"]    for op in ops), default=0.25)
    setup_ref  = max((op["setup_hrs"]    for op in ops), default=0.5)
    num_ref    = max((op["num_maquinas"] for op in ops), default=1)
    semaforo   = calcular_semaforo(demanda, ciclo_ref, setup_ref,
                                   hrs_t, dias_m, efic, num_ref)

    # Márgenes por componente — independientes
    if pieza.get("usar_margen_global", False):
        util_mo   = costo_maq  * (margen_pct / 100)
        util_mat  = costo_mat  * (margen_pct / 100)
        util_trat = pieza["costo_trat"] * (margen_pct / 100)
    else:
        util_mo   = costo_maq  * (pieza.get("margen_mo",   35) / 100)
        util_mat  = costo_mat  * (pieza.get("margen_mat",  35) / 100)
        util_trat = pieza["costo_trat"] * (pieza.get("margen_trat", 35) / 100)

    subtotal   = costo_maq + costo_mat + pieza["costo_trat"]
    utilidad   = util_mo + util_mat + util_trat
    precio_pza = subtotal + utilidad
    total      = precio_pza * cantidad

    return {
        "ops_resultado": resultados,
        "tiempo_min":    tiempo_pza * 60,
        "costo_maq":     costo_maq,
        "costo_material": costo_mat,
        "costo_trat":    pieza["costo_trat"],
        "subtotal":      subtotal,
        "utilidad":      utilidad,
        "precio_pza":    precio_pza,
        "total":         total,
        "precios_tipo":  precios,
        "fijo_hr":       fijo_hr,
        "hrs_prod":      hrs_prod,
        "semaforo":      semaforo,
    }

# ══════════════════════════════════════════════════════════════════════════════
# ESTADO INICIAL
# ══════════════════════════════════════════════════════════════════════════════
# Defaults del sidebar — solo para heredar al crear piezas nuevas
DEFAULTS = {"maq_activas": 7, "turnos": 1, "hrs_turno": 10,
            "dias_mes": 22, "eficiencia": 75}

if "piezas"       not in st.session_state:
    st.session_state.piezas = [nueva_pieza(1, DEFAULTS)]
if "cotizaciones" not in st.session_state:
    st.session_state.cotizaciones = []

# ══════════════════════════════════════════════════════════════════════════════
# HEADER
# ══════════════════════════════════════════════════════════════════════════════
st.markdown("""
<div class="main-header">
    <h1>JAAN Manufacturing &nbsp;·&nbsp; Cotizador Profesional</h1>
    <p>Sistema profesional &nbsp;·&nbsp; Parámetros por pieza &nbsp;·&nbsp; Ruteo serie y paralelo</p>
</div>
""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    # ── Usuario actual ───────────────────────────────────────────────
    usuario = st.session_state.get("usuario", {})
    st.markdown(
        f"<div style='background:#E6F1FB;border-radius:8px;padding:10px 12px;"
        f"margin-bottom:12px;font-size:13px'>"
        f"👤 <b>{usuario.get('nombre', usuario.get('email',''))}</b><br>"
        f"<span style='color:#6b7280;font-size:11px'>{usuario.get('rol','vendedor').upper()}</span>"
        f"</div>",
        unsafe_allow_html=True
    )
    if st.button("🚪 Cerrar sesión", use_container_width=True):
        st.session_state.autenticado = False
        st.session_state.usuario     = {}
        st.rerun()
    st.markdown("---")

    st.markdown("### 📋 Datos de la cotización")
    # Transferir valores cargados desde historial a keys persistentes (una sola vez)
    if "_cliente"   in st.session_state: st.session_state["sb_cliente"]   = st.session_state.pop("_cliente")
    if "_atencion"  in st.session_state: st.session_state["sb_atencion"]  = st.session_state.pop("_atencion")
    if "_ciudad"    in st.session_state: st.session_state["sb_ciudad"]    = st.session_state.pop("_ciudad")
    if "_direccion" in st.session_state: st.session_state["sb_direccion"] = st.session_state.pop("_direccion")
    if "_cp"        in st.session_state: st.session_state["sb_cp"]        = st.session_state.pop("_cp")
    if "_pais"      in st.session_state: st.session_state["sb_pais"]      = st.session_state.pop("_pais")
    # Widgets con key persistente — sobreviven a cualquier rerun
    cliente   = st.text_input("Cliente",     key="sb_cliente",   placeholder="Nombre del cliente")
    atencion  = st.text_input("Atención a",  key="sb_atencion",  placeholder="Nombre del contacto")
    direccion = st.text_input("Dirección",   key="sb_direccion", placeholder="Calle, número, colonia")
    col_cp, col_ciudad = st.columns([1, 2])
    with col_cp:
        cp      = st.text_input("C.P.",      key="sb_cp",        placeholder="00000")
    with col_ciudad:
        ciudad  = st.text_input("Ciudad",    key="sb_ciudad",    placeholder="Ciudad, Estado")
    pais      = st.text_input("País",        key="sb_pais",      placeholder="México")

    # ── Consecutivo automático por sufijo ────────────────────────────
    sufijo_default = st.session_state.get("sufijo_cot", "COT")
    sufijo = st.text_input("Sufijo de cotización", value=sufijo_default,
                placeholder="Ej: AIE, GFT, COT",
                help="El número consecutivo se asigna automáticamente por sufijo").upper().strip()
    st.session_state["sufijo_cot"] = sufijo

    def get_siguiente_numero(sufijo):
        """Obtiene el siguiente número consecutivo para el sufijo dado"""
        import requests
        token, _ = get_gsheet_token()
        if not token:
            return f"{sufijo}-001"
        sheet_id = st.secrets.get("GSHEET_ID", "").strip()
        try:
            resp = requests.get(
                f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/A2:A1000",
                headers={"Authorization": f"Bearer {token}"})
            if resp.status_code != 200:
                return f"{sufijo}-001"
            numeros = [r[0] for r in resp.json().get("values", []) if r and r[0].startswith(sufijo + "-")]
            if not numeros:
                return f"{sufijo}-001"
            # Extraer el mayor consecutivo
            maxn = 0
            for n in numeros:
                try:
                    maxn = max(maxn, int(n.split("-")[-1]))
                except Exception:
                    pass
            return f"{sufijo}-{str(maxn + 1).zfill(3)}"
        except Exception:
            return f"{sufijo}-001"

    # Generar número si cambia el sufijo o es nuevo
    if ("num_cot_generado" not in st.session_state or
        st.session_state.get("sufijo_anterior") != sufijo):
        st.session_state["num_cot_generado"] = get_siguiente_numero(sufijo)
        st.session_state["sufijo_anterior"] = sufijo

    # Transferir valores cargados desde historial a keys persistentes (una sola vez)
    if "_tipo_cambio" in st.session_state: st.session_state["sb_tipo_cambio"] = st.session_state.pop("_tipo_cambio")
    if "_moneda"      in st.session_state: st.session_state["sb_moneda"]      = st.session_state.pop("_moneda")
    if "_margen"      in st.session_state: st.session_state["sb_margen"]      = st.session_state.pop("_margen")
    if "_vigencia"    in st.session_state: st.session_state["sb_vigencia"]    = st.session_state.pop("_vigencia")
    if "_t_entrega"   in st.session_state: st.session_state["sb_t_entrega"]   = st.session_state.pop("_t_entrega")
    if "_cond_pago"   in st.session_state: st.session_state["sb_cond_pago"]   = st.session_state.pop("_cond_pago")

    # Inicializar defaults si no existen aún
    if "sb_moneda"      not in st.session_state: st.session_state["sb_moneda"]      = "MXN"
    if "sb_margen"      not in st.session_state: st.session_state["sb_margen"]      = 35
    if "sb_vigencia"    not in st.session_state: st.session_state["sb_vigencia"]    = "15 Días"
    if "sb_t_entrega"   not in st.session_state: st.session_state["sb_t_entrega"]   = "22-30 días hábiles"
    if "sb_cond_pago"   not in st.session_state: st.session_state["sb_cond_pago"]   = "40% anticipo - 60% contra-entrega"

    # ── Tipo de cambio automático (se actualiza una vez por día) ─────
    @st.cache_data(ttl=86400)   # cache 24 horas
    def obtener_tipo_cambio():
        import requests as _req
        try:
            r = _req.get("https://api.frankfurter.app/latest?from=USD&to=MXN", timeout=5)
            if r.status_code == 200:
                tc = r.json()["rates"]["MXN"]
                return round(tc, 4)
        except Exception:
            pass
        try:
            r = _req.get("https://open.er-api.com/v6/latest/USD", timeout=5)
            if r.status_code == 200:
                tc = r.json()["rates"]["MXN"]
                return round(tc, 4)
        except Exception:
            pass
        return 17.31   # fallback

    # Cargar TC automático solo si no hay uno manual/cargado del historial
    if "sb_tipo_cambio" not in st.session_state:
        st.session_state["sb_tipo_cambio"] = obtener_tipo_cambio()
    # Aplicar reset TC antes de renderizar el widget (evita StreamlitAPIException)
    if "_reset_tc" in st.session_state:
        st.session_state["sb_tipo_cambio"] = st.session_state.pop("_reset_tc")

    num_cot = st.text_input("Núm. cotización",
                value=st.session_state.get("num_cot_generado", f"{sufijo}-001"),
                help="Editable — se genera automáticamente")

    # Mostrar fuente y fecha del TC
    tc_auto = obtener_tipo_cambio()
    st.caption(f"💱 TC de referencia hoy: **${tc_auto:,.4f}** · Fuente: Frankfurter (BCE) · Editable")
    tipo_cambio = st.number_input("Tipo de cambio USD/MXN", key="sb_tipo_cambio", step=0.01,
                    help="Se actualiza automáticamente cada 24h — puedes editarlo manualmente")
    if st.button("🔄 Usar TC del día", use_container_width=True):
        st.session_state["_reset_tc"] = tc_auto
        st.rerun()
    _moneda_val = st.session_state.get("sb_moneda","MXN")
    _moneda_idx = ["MXN","USD"].index(_moneda_val) if _moneda_val in ["MXN","USD"] else 0
    moneda_cot  = st.radio("Moneda de la cotización", ["MXN", "USD"],
                    index=_moneda_idx,
                    horizontal=True,
                    help="Toda la cotización se mostrará en la moneda seleccionada")
    st.session_state["sb_moneda"] = moneda_cot

    st.markdown("---")
    st.markdown("### ⚙️ Cotización")
    margen_global = st.slider("Margen de utilidad (%)", 0, 100, key="sb_margen")

    st.markdown("---")
    st.markdown("### 📦 Condiciones generales")
    vigencia  = st.text_input("Vigencia",            key="sb_vigencia")
    t_entrega = st.text_input("Tiempo de entrega",   key="sb_t_entrega")
    cond_pago = st.text_input("Condiciones de pago", key="sb_cond_pago")

# ══════════════════════════════════════════════════════════════════════════════
# TABS
# ══════════════════════════════════════════════════════════════════════════════
# Wrapper de formato con moneda activa
def fmtc(v):
    """Formato con moneda seleccionada en la cotización"""
    return fmt(v, moneda_cot, tipo_cambio)

simbolo = "USD $" if moneda_cot == "USD" else "$"


# ── Guardar cotización en Google Sheets ──────────────────────────────────────
def guardar_cotizacion():
    total = sum(calcular_pieza(p, margen_global).get("total", 0)
                for p in st.session_state.piezas)
    iva        = total * 0.16
    total_neto = total + iva

    def piezas_sin_b64(piezas):
        """Elimina campos base64 pesados conservando IDs de Drive"""
        limpias = []
        for p in piezas:
            pc = dict(p)
            # Limpiar b64 pero CONSERVAR drive_id y nombre
            pc["plano_b64"] = ""
            # plano_drive_id, plano_nombre, plano_tipo se conservan
            mp = dict(pc.get("materia_prima", {}))
            mp["cotizacion_mp_b64"] = ""
            pc["materia_prima"] = mp
            pc["cotizacion_trat_b64"] = ""
            limpias.append(pc)
        return limpias

    fila = [
        num_cot,
        datetime.now().strftime("%d/%m/%Y %H:%M"),
        st.session_state.get("usuario", {}).get("email", ""),
        cliente, atencion, direccion, cp, ciudad, pais,
        moneda_cot, str(tipo_cambio), str(margen_global),
        str(round(total, 2)), str(round(iva, 2)), str(round(total_neto, 2)),
        vigencia, t_entrega, cond_pago,
        json.dumps({
            "piezas": piezas_sin_b64(st.session_state.piezas),
            "cond_generales": {"vigencia": vigencia,
                               "tiempo_entrega": t_entrega,
                               "cond_pago": cond_pago}
        }, default=str, ensure_ascii=False)
    ]

    # Upsert: actualiza si ya existe, inserta si es nueva
    ok, err2 = update_gsheet_row(num_cot, fila)
    if ok:
        st.success(f"✅ Cotización {num_cot} guardada en Google Sheets — {len(st.session_state.piezas)} pieza(s)")
    else:
        # Guardar localmente como fallback
        if "cotizaciones" not in st.session_state:
            st.session_state.cotizaciones = []
        existing = [i for i, c in enumerate(st.session_state.cotizaciones)
                    if c.get("numero") == num_cot]
        cot_local = {"numero": num_cot, "fecha": fila[1],
                     "cliente": cliente, "total_neto": total_neto}
        if existing:
            st.session_state.cotizaciones[existing[0]] = cot_local
        else:
            st.session_state.cotizaciones.append(cot_local)
        st.warning(f"⚠️ Guardada localmente. Error: {err2}")


def cargar_cotizaciones():
    """Lee cotizaciones desde Google Sheets, deduplicando por número (más reciente gana)"""
    import requests
    token, err = get_gsheet_token()
    if not token:
        return st.session_state.get("cotizaciones", [])
    
    sheet_id = st.secrets.get("GSHEET_ID", "").strip()
    if not sheet_id:
        return st.session_state.get("cotizaciones", [])
    
    try:
        resp = requests.get(
            f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/A1:Z2000",
            headers={"Authorization": f"Bearer {token}"})
        if resp.status_code != 200:
            return st.session_state.get("cotizaciones", [])
        
        data = resp.json()
        values = data.get("values", [])
        if len(values) < 2:
            return []
        
        headers = values[0]
        import json as _json

        # Headers canónicos actuales — usados para mapear columnas nuevas
        CANONICAL = ["numero","fecha","usuario_email","cliente","atencion",
                     "direccion","cp","ciudad","pais","moneda","tipo_cambio",
                     "margen_global","subtotal","iva","total_neto","vigencia",
                     "tiempo_entrega","cond_pago","datos_json","status"]

        # Schemas conocidos — mapeados por número de columnas (sin contar status al final)
        # Schema viejo:  numero,fecha,email,cliente,atencion,ciudad,moneda,tc,margen,sub,iva,total,vig,tent,cpago,json
        # Schema nuevo:  numero,fecha,email,cliente,atencion,direccion,cp,ciudad,pais,moneda,tc,margen,sub,iva,total,vig,tent,cpago,json
        SCHEMA_VIEJO = ["numero","fecha","usuario_email","cliente","atencion",
                        "ciudad","moneda","tipo_cambio","margen_global",
                        "subtotal","iva","total_neto","vigencia",
                        "tiempo_entrega","cond_pago","datos_json"]
        SCHEMA_NUEVO = ["numero","fecha","usuario_email","cliente","atencion",
                        "direccion","cp","ciudad","pais","moneda","tipo_cambio",
                        "margen_global","subtotal","iva","total_neto","vigencia",
                        "tiempo_entrega","cond_pago","datos_json"]

        def parse_row(headers, row):
            """Mapea una fila detectando automáticamente si es schema viejo o nuevo."""

            # Siempre mapear primero por headers reales del Sheet
            d_raw = {}
            for i, h in enumerate(headers):
                d_raw[h] = row[i] if i < len(row) else ""

            # Detectar si esta FILA específica es de schema viejo:
            # En schema viejo, la col "direccion" (pos 5) contiene ciudad
            # y la col "moneda" (pos 9 nuevo) contiene un número (tipo_cambio)
            # La clave: en schema viejo, datos_json está en col 15 (pos 15)
            # En schema nuevo, datos_json está en col 18 (pos 18)
            # Detectamos buscando cuál posición tiene JSON válido con "piezas"
            datos_json_val = ""
            datos_json_idx = -1
            for i, cell in enumerate(row):
                cell = (cell or "").strip()
                if cell.startswith("{") and '"piezas"' in cell:
                    try:
                        _json.loads(cell)
                        datos_json_val = cell
                        datos_json_idx = i
                        break
                    except Exception:
                        pass

            # Si datos_json está en posición 15 → schema viejo (16 cols base)
            # Si datos_json está en posición 18 → schema nuevo (19 cols base)
            if datos_json_idx == 15 or (datos_json_idx == -1 and len(row) <= 17):
                schema = SCHEMA_VIEJO
            else:
                schema = SCHEMA_NUEVO

            d = {}
            for i, h in enumerate(schema):
                d[h] = row[i] if i < len(row) else ""

            # Forzar datos_json encontrado
            if datos_json_val:
                d["datos_json"] = datos_json_val

            # Garantizar campos nuevos vacíos si schema viejo
            for campo in ["direccion","cp","pais"]:
                if campo not in d:
                    d[campo] = ""

            # Status puede estar como columna extra al final
            if "status" in headers:
                si = headers.index("status")
                d["status"] = row[si] if si < len(row) else ""
            elif d_raw.get("status"):
                d["status"] = d_raw["status"]

            # Sanear moneda
            if d.get("moneda","") not in ("MXN","USD"):
                d["moneda"] = "MXN"

            return d

        seen = {}
        for row in reversed(values[1:]):
            if not row or not row[0]:
                continue
            numero = row[0].strip()
            d = parse_row(headers, row)

            # Extraer num_dibujos y descripciones desde datos_json
            try:
                datos_raw = d.get("datos_json", "{}")
                if isinstance(datos_raw, str) and datos_raw.strip():
                    datos = _json.loads(datos_raw)
                else:
                    datos = {}
                piezas = datos.get("piezas", []) if isinstance(datos, dict) else (datos if isinstance(datos, list) else [])
                # Construir lista de items: [{dwg, desc, cant, total}, ...]
                items = []
                total_cant = 0
                margen_g = 35
                try:
                    margen_g = int(float(d.get("margen_global", 35) or 35))
                except Exception:
                    margen_g = 35
                for p in piezas:
                    dwg  = str(p.get("num_dibujo","")).strip()
                    desc = str(p.get("descripcion","")).strip()
                    if p.get("tipo_pedido") == "Por proyecto":
                        cant = int(p.get("eau", 0) or 0)
                    else:
                        cant = int(p.get("cantidad", 0) or 0)
                    total_cant += cant
                    # Calcular total por pieza desde datos guardados
                    try:
                        res = calcular_pieza(p, margen_g)
                        total_pieza = res.get("total", 0)
                    except Exception:
                        total_pieza = 0
                    if dwg or desc:
                        items.append({"dwg": dwg, "desc": desc, "cant": cant, "total": total_pieza})
                d["items_lista"]   = items
                d["num_dibujos"]   = " | ".join(i["dwg"]  for i in items if i["dwg"])
                d["descripciones"] = " | ".join(i["desc"] for i in items if i["desc"])
                d["cantidad_total"] = total_cant
            except Exception:
                d["num_dibujos"]   = ""
                d["descripciones"] = ""

            seen[numero] = d

        return list(reversed(list(seen.values())))
    except Exception:
        return st.session_state.get("cotizaciones", [])


def actualizar_status_gsheet(numero, nuevo_status):
    """Actualiza el status de una cotización en Google Sheets"""
    import requests
    token, err = get_gsheet_token()
    if not token:
        return False, err
    sheet_id = st.secrets.get("GSHEET_ID", "").strip()
    # Leer todas las filas para encontrar la fila correcta
    resp = requests.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/A1:Z1000",
        headers={"Authorization": f"Bearer {token}"})
    if resp.status_code != 200:
        return False, "No se pudo leer el sheet"
    values = resp.json().get("values", [])
    if len(values) < 2:
        return False, "Sheet vacío"
    headers = values[0]
    # Encontrar o crear columna status
    if "status" in headers:
        status_col = headers.index("status")
    else:
        # Agregar encabezado status al final
        status_col = len(headers)
        col_header_letter = chr(ord("A") + status_col)
        requests.put(
            f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{col_header_letter}1?valueInputOption=RAW",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"values": [["status"]]})

    # Encontrar fila del número de cotización
    row_num = None
    for i, row in enumerate(values[1:], 2):
        if row and row[0] == numero:
            row_num = i
            break
    if not row_num:
        return False, f"Cotización {numero} no encontrada en el Sheet"

    # Actualizar el status
    col_letter = chr(ord("A") + status_col)
    update_resp = requests.put(
        f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{col_letter}{row_num}?valueInputOption=RAW",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"values": [[nuevo_status]]})
    if update_resp.status_code == 200:
        return True, None
    return False, f"Error {update_resp.status_code}: {update_resp.text[:200]}"


tab1, tab2, tab3 = st.tabs(["📐 Piezas y Ruteo", "📄 Cotización", "🗂️ Historial"])

# Inject expander colors via components.html (executes in real iframe with DOM access)
import streamlit.components.v1 as components
components.html("""
<script>
(function() {
    var rules = [
        { text: "Plano",       bg: "#EEEDFE", border: "#534AB7", color: "#3C3489" },
        { text: "Par\u00e1metros", bg: "#E6F1FB", border: "#185FA5", color: "#0C447C" },
        { text: "Materia",     bg: "#EAF3DE", border: "#3B6D11", color: "#27500A" },
        { text: "Tratamiento", bg: "#FAEEDA", border: "#854F0B", color: "#633806" },
        { text: "Operaciones", bg: "#F1EFE8", border: "#5F5E5A", color: "#2C2C2A" },
    ];
    function paint() {
        var doc = window.parent.document;
        doc.querySelectorAll('[data-testid="stExpanderHeader"]').forEach(function(h) {
            var txt = h.innerText || h.textContent || "";
            for (var i = 0; i < rules.length; i++) {
                if (txt.indexOf(rules[i].text) !== -1) {
                    h.style.setProperty("background", rules[i].bg, "important");
                    h.style.setProperty("border-left", "3px solid " + rules[i].border, "important");
                    h.style.setProperty("padding-left", "12px", "important");
                    h.querySelectorAll("p,span").forEach(function(el) {
                        el.style.setProperty("color", rules[i].color, "important");
                        el.style.setProperty("font-weight", "500", "important");
                    });
                    h.querySelectorAll("svg").forEach(function(sv) {
                        sv.style.setProperty("stroke", rules[i].border, "important");
                        sv.style.setProperty("fill", "none", "important");
                    });
                    break;
                }
            }
        });
    }
    paint();
    var obs = new MutationObserver(paint);
    obs.observe(window.parent.document.body, { childList: true, subtree: true });
    setTimeout(paint, 300);
    setTimeout(paint, 800);
    setTimeout(paint, 2000);
})();
</script>
""", height=0)



# ══ TAB 1 ═════════════════════════════════════════════════════════════════════
with tab1:
    col_t, col_b = st.columns([4, 1])
    with col_t:
        st.markdown(f"#### {len(st.session_state.piezas)} pieza(s) en esta cotización")
    with col_b:
        if st.button("➕ Nueva pieza"):
            nid = max(p["id"] for p in st.session_state.piezas) + 1
            st.session_state.piezas.append(nueva_pieza(nid, DEFAULTS))
            st.rerun()

    piezas_a_eliminar = []

    for pi, pieza in enumerate(st.session_state.piezas):
        st.markdown("<div class='pieza-card'>", unsafe_allow_html=True)

        # ── Identificación ───────────────────────────────────────────────────
        ci1, ci2, ci3, ci4, ci5 = st.columns([0.4, 1.2, 2.4, 1.4, 0.4])
        with ci1:
            st.markdown(f"<b style='font-size:20px;color:#1a5cff'>#{pi+1}</b>",
                        unsafe_allow_html=True)
        with ci2:
            ndwg = st.text_input("Núm. dibujo", value=pieza["num_dibujo"],
                key=f"ndwg_{pieza['id']}", placeholder="DWG-XXXX")
            st.session_state.piezas[pi]["num_dibujo"] = ndwg
        with ci3:
            desc = st.text_input("Descripción", value=pieza["descripcion"],
                key=f"desc_{pieza['id']}", placeholder="Nombre / descripción")
            st.session_state.piezas[pi]["descripcion"] = desc
        with ci4:
            tipo_pedido = st.radio("Tipo de pedido",
                ["Pedido único", "Por proyecto"],
                index=0 if pieza.get("tipo_pedido","Pedido único")=="Pedido único" else 1,
                key=f"tped_{pieza['id']}", horizontal=True)
            st.session_state.piezas[pi]["tipo_pedido"] = tipo_pedido
            if tipo_pedido == "Pedido único":
                cant = st.number_input("Cantidad (pzas)", min_value=1, max_value=999999,
                    value=pieza["cantidad"], key=f"cant_{pieza['id']}")
                st.session_state.piezas[pi]["cantidad"] = cant
            else:
                col_moq, col_eau = st.columns(2)
                with col_moq:
                    moq = st.number_input("MOQ/Month", min_value=0, max_value=999999,
                        value=int(pieza.get("moq", 0)), key=f"moq_{pieza['id']}")
                    st.session_state.piezas[pi]["moq"] = moq
                with col_eau:
                    eau = st.number_input("EAU/Year", min_value=0, max_value=9999999,
                        value=int(pieza.get("eau", 0)), key=f"eau_{pieza['id']}")
                    st.session_state.piezas[pi]["eau"] = eau
                # Use MOQ as cantidad for calculations
                cant = moq if moq > 0 else 1
                st.session_state.piezas[pi]["cantidad"] = cant
        with ci5:
            if len(st.session_state.piezas) > 1:
                st.write("")
                if st.button("🗑️", key=f"delp_{pieza['id']}"):
                    piezas_a_eliminar.append(pi)

        # ── Plano / Análisis IA ──────────────────────────────────────────────
        with st.expander("▸  Plano de la pieza — Análisis IA", expanded=False):
            st.caption("Sube el plano en PDF o imagen y la IA analizará las operaciones, tiempos y tipo de máquina sugeridos")
            plano_col1, plano_col2 = st.columns([1, 1])
            with plano_col1:
                plano_file = st.file_uploader(
                    "Subir plano (PDF o imagen)",
                    type=["pdf", "png", "jpg", "jpeg"],
                    key=f"plano_{pieza['id']}",
                    help="El plano se enviará a Claude para análisis"
                )
            with plano_col2:
                notas_plano = st.text_area(
                    "Notas adicionales para la IA (opcional)",
                    placeholder="Ej: Material Inox 303, tolerancias críticas en diámetro exterior, acabado Ra 1.6...",
                    key=f"notas_plano_{pieza['id']}",
                    height=80
                )

            # Vista previa del plano dentro del expander
            if plano_file is not None:
                import base64 as b64mod
                file_bytes_prev = plano_file.read()
                plano_file.seek(0)
                is_img_type = plano_file.name.lower().endswith((".png",".jpg",".jpeg"))
                # Subir a Google Drive si no tiene drive_id aún
                if not st.session_state.piezas[pi].get("plano_drive_id"):
                    with st.spinner("☁️ Subiendo plano a Google Drive..."):
                        mime = "image/png" if is_img_type else "application/pdf"
                        file_id, drive_err = subir_plano_drive(file_bytes_prev, plano_file.name, mime)
                    if file_id:
                        st.session_state.piezas[pi]["plano_nombre"]    = plano_file.name
                        st.session_state.piezas[pi]["plano_drive_id"]  = file_id
                        st.session_state.piezas[pi]["plano_b64"]       = ""
                        st.session_state.piezas[pi]["plano_tipo"]      = "img" if is_img_type else "pdf"
                        st.success(f"☁️ Plano subido a Drive correctamente: {plano_file.name}")
                    else:
                        # Drive falló — mostrar error real para diagnóstico
                        st.error(f"❌ Error subiendo a Drive: {drive_err}")
                        st.info("💡 Verifica que el Service Account tenga la API de Drive habilitada en Google Cloud Console.")
                        # Guardar solo nombre y tipo (sin b64 para no exceder Sheet)
                        st.session_state.piezas[pi]["plano_nombre"]   = plano_file.name
                        st.session_state.piezas[pi]["plano_drive_id"] = ""
                        st.session_state.piezas[pi]["plano_b64"]      = ""
                        st.session_state.piezas[pi]["plano_tipo"]     = "img" if is_img_type else "pdf"
                is_img = plano_file.name.lower().endswith((".png",".jpg",".jpeg"))
                st.markdown("**👁️ Vista previa del plano:**")
                if is_img:
                    prev_c1, prev_c2 = st.columns([1, 2])
                    with prev_c1:
                        st.image(file_bytes_prev, width=150, caption=plano_file.name)
                    with prev_c2:
                        if st.toggle("🔍 Ver tamaño completo", key=f"toggle_prev_{pieza['id']}"):
                            st.image(file_bytes_prev, use_container_width=True)
                else:
                    pdf_b64 = b64mod.b64encode(file_bytes_prev).decode()
                    prev_c1, prev_c2 = st.columns([1, 3])
                    with prev_c1:
                        st.markdown(f"📄 **{plano_file.name}**")
                        st.caption(f"{len(file_bytes_prev)//1024} KB")
                        st.download_button(
                            "⬇️ Descargar PDF",
                            data=file_bytes_prev,
                            file_name=plano_file.name,
                            mime="application/pdf",
                            key=f"dl_plano_{pieza['id']}",
                            use_container_width=True
                        )
                    with prev_c2:
                        if st.toggle("🔍 Ver PDF completo", key=f"toggle_prev_{pieza['id']}"):
                            try:
                                import fitz
                                pdf_doc = fitz.open(stream=file_bytes_prev, filetype="pdf")
                                for page_num in range(min(len(pdf_doc), 3)):
                                    page = pdf_doc[page_num]
                                    # Detectar si la página es vertical y rotar
                                    rect = page.rect
                                    if rect.height > rect.width:
                                        mat = fitz.Matrix(2, 2).prerotate(270)
                                    else:
                                        mat = fitz.Matrix(2, 2)
                                    pix = page.get_pixmap(matrix=mat)
                                    img_bytes = pix.tobytes("png")
                                    st.image(img_bytes, use_container_width=True,
                                             caption=f"Página {page_num+1}")
                            except ImportError:
                                st.markdown(
                                    f'<object data="data:application/pdf;base64,{pdf_b64}" '
                                    f'type="application/pdf" width="100%" height="500px">'
                                    f'<p>Tu navegador no puede mostrar el PDF. '
                                    f'<a href="data:application/pdf;base64,{pdf_b64}" download="plano.pdf">Descargar PDF</a></p>'
                                    f'</object>',
                                    unsafe_allow_html=True)
                st.markdown("---")

            # Mostrar plano guardado si existe y no hay nuevo archivo subido
            if plano_file is None and pieza.get("plano_nombre"):
                drive_id = pieza.get("plano_drive_id", "")
                b64_local = pieza.get("plano_b64", "")
                st.markdown(f"**👁️ Plano guardado: {pieza['plano_nombre']}**")

                # Obtener bytes: desde Drive o desde b64 local
                plano_bytes_saved = None
                if drive_id:
                    cache_key = f"_plano_cache_{drive_id}"
                    if cache_key not in st.session_state:
                        with st.spinner("☁️ Descargando plano de Drive..."):
                            plano_bytes_saved, _ = descargar_plano_drive(drive_id)
                            if plano_bytes_saved:
                                st.session_state[cache_key] = plano_bytes_saved
                    else:
                        plano_bytes_saved = st.session_state[cache_key]
                elif b64_local:
                    import base64 as b64mod
                    plano_bytes_saved = b64mod.b64decode(b64_local)

                if plano_bytes_saved:
                    if pieza.get("plano_tipo") == "img":
                        st.image(plano_bytes_saved, use_container_width=True, caption=pieza["plano_nombre"])
                    else:
                        sc1, sc2 = st.columns([1, 3])
                        with sc1:
                            st.markdown(f"📄 **{pieza['plano_nombre']}**")
                            st.download_button(
                                "⬇️ Descargar PDF",
                                data=plano_bytes_saved,
                                file_name=pieza["plano_nombre"],
                                mime="application/pdf",
                                key=f"dl_plano_saved_{pieza['id']}",
                                use_container_width=True
                            )
                        with sc2:
                            if st.toggle("🔍 Ver PDF guardado", key=f"toggle_saved_{pieza['id']}"):
                                try:
                                    import fitz
                                    pdf_doc = fitz.open(stream=plano_bytes_saved, filetype="pdf")
                                    for page_num in range(min(len(pdf_doc), 3)):
                                        page = pdf_doc[page_num]
                                        rect = page.rect
                                        mat = fitz.Matrix(2, 2).prerotate(270) if rect.height > rect.width else fitz.Matrix(2, 2)
                                        pix = page.get_pixmap(matrix=mat)
                                        st.image(pix.tobytes("png"), use_container_width=True, caption=f"Página {page_num+1}")
                                except ImportError:
                                    import base64 as b64mod
                                    pdf_b64_disp = b64mod.b64encode(plano_bytes_saved).decode()
                                    st.markdown(
                                        f'<object data="data:application/pdf;base64,{pdf_b64_disp}" '
                                        f'type="application/pdf" width="100%" height="500px"></object>',
                                        unsafe_allow_html=True)
                else:
                    if drive_id:
                        st.warning("⚠️ No se pudo descargar el plano de Drive. Verifica la conexión.")
                    else:
                        st.info("ℹ️ El plano no está vinculado a Drive. Vuelve a subirlo para guardarlo correctamente.")
                st.markdown("---")

            if plano_file is not None:
                ia_col1, ia_col2 = st.columns([1, 2])
                with ia_col1:
                    ia_engine = st.radio(
                        "Motor de IA",
                        ["Claude (Anthropic)", "GPT-4o (OpenAI)"],
                        key=f"ia_engine_{pieza['id']}",
                        horizontal=True,
                        help="Claude es más preciso con planos técnicos industriales"
                    )
                with ia_col2:
                    analizar_btn = st.button(
                        "🤖 Analizar plano con IA",
                        key=f"analyze_{pieza['id']}",
                        help="La IA analizará el plano y sugerirá operaciones, tiempos y máquinas"
                    )

                if analizar_btn:
                    import base64, json, requests

                    # Motor seleccionado
                    usar_claude = st.session_state.get(f"ia_engine_{pieza['id']}", "Claude (Anthropic)") == "Claude (Anthropic)"
                    spinner_msg = "🤖 Claude analizando plano..." if usar_claude else "🤖 GPT-4o analizando plano..."

                    with st.spinner(spinner_msg):
                        try:
                            import os, base64, json, requests
                            file_bytes = plano_file.read()
                            file_b64   = base64.b64encode(file_bytes).decode("utf-8")
                            is_pdf     = plano_file.name.lower().endswith(".pdf")
                            material_pieza = pieza["materia_prima"].get("material", "No especificado")

                            # ── Prompt experto en planos CNC ─────────────────
                            json_example = (
                                '{"resumen": "Eje de arranque acero, multiples diametros escalonados, spline externo", '
                                '"advertencias": ["Tolerancia .0005 en diametros criticos", "Runout max .005"], '
                                '"operaciones": [{"label": "Op 10", "tipo_maq": "Lathe 2 Axis", '
                                '"descripcion": "Torneado exterior de todos los diametros escalonados y chaflanes", '
                                '"setup_hrs": 2.0, "ciclo_hrs": 0.5, "paralelo": false}]}'
                            )

                            prompt = (
                                "You are an expert CNC manufacturing process engineer with 20 years of experience "
                                "reading engineering drawings and planning machining operations for a metal shop.\n\n"

                                "MACHINE CAPABILITIES:\n"
                                "1. Lathe 2 Axis: turning OD/ID, facing, grooving (axial), threading (visible on drawing), chamfers, radii. "
                                "NO milling, NO radial holes, NO keyways.\n"
                                "2. Mill-Turn C Axis: everything Lathe 2 Axis + radial holes, simple radial slots, hex/polygon OD, C-axis indexed features.\n"
                                "3. Mill-Turn CY Axis: everything Mill-Turn C + off-center milling (Y-axis), eccentric features, complex keyways, full milling without re-chucking.\n"
                                "4. Center Mill 3 Axis: full 3D milling, plates, prismatic parts, pockets, face milling. NOT efficient for long cylindrical parts.\n"
                                "5. Center Mill 4th Axis: everything Center Mill 3 + multi-face indexing, splines, helical grooves, gears, cams.\n\n"

                                "HOW TO ANALYZE THE DRAWING - FOLLOW THIS REASONING:\n"
                                "Step 1 - IDENTIFY PART TYPE: Is it a shaft/cylindrical part or a prismatic/plate part?\n"
                                "  - Cylindrical → start with Lathe 2 Axis\n"
                                "  - Prismatic → start with Center Mill 3 Axis\n\n"
                                "Step 2 - LOOK FOR THESE FEATURES (only if CLEARLY VISIBLE):\n"
                                "  - Multiple stepped diameters → Lathe 2 Axis (count them for cycle time)\n"
                                "  - Threads (shown as parallel lines) → add threading op in Lathe\n"
                                "  - Spline data table → Center Mill 4th Axis or Mill-Turn CY\n"
                                "  - Square/hex section → Mill-Turn CY Axis\n"
                                "  - Keyway/slot → Mill-Turn C or CY Axis\n"
                                "  - Radial holes → Mill-Turn C Axis minimum\n"
                                "  - Heat treatment notes → note as external process in advertencias\n"
                                "  - Surface finish requirements → affects cycle time\n"
                                "  - GD&T callouts (runout, concentricity) → increases setup complexity\n\n"
                                "Step 3 - ESTIMATE TIMES based on complexity:\n"
                                "  - Simple shaft <3 diameters: setup 1-2hrs, cycle 0.25-0.5hrs\n"
                                "  - Complex shaft >5 diameters + features: setup 2-4hrs, cycle 0.5-1.5hrs\n"
                                "  - Spline milling: setup 3-4hrs, cycle 1.0-2.0hrs\n"
                                "  - Square/hex milling: setup 1.5-2hrs, cycle 0.25-0.5hrs\n\n"
                                "Step 4 - NEVER INVENT: If you cannot clearly see a feature, do NOT add an operation for it.\n\n"

                                f"PART CONTEXT:\n"
                                f"- Material: {material_pieza}\n"
                                f"- Engineer notes: {notas_plano if notas_plano else 'None'}\n\n"

                                "Analyze the drawing following the steps above. "
                                "Respond ONLY with valid JSON, no extra text, no backticks:\n"
                                + json_example
                                + "\n\nTimes in hours: setup_hrs 0.5-8.0, ciclo_hrs (15min=0.25, 30min=0.5, 1hr=1.0)"
                            )

                            if usar_claude:
                                api_key = (st.secrets.get("ANTHROPIC_API_KEY", None) if hasattr(st, "secrets") else None) or os.environ.get("ANTHROPIC_API_KEY", "")
                                if not api_key:
                                    st.error("❌ No se encontró ANTHROPIC_API_KEY en secrets.toml")
                                    st.stop()
                                if is_pdf:
                                    msg_content = [
                                        {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": file_b64}},
                                        {"type": "text", "text": prompt}
                                    ]
                                else:
                                    ext  = plano_file.name.lower().split(".")[-1]
                                    mime = f"image/{'jpeg' if ext in ['jpg','jpeg'] else ext}"
                                    msg_content = [
                                        {"type": "image", "source": {"type": "base64", "media_type": mime, "data": file_b64}},
                                        {"type": "text", "text": prompt}
                                    ]
                                response = requests.post(
                                    "https://api.anthropic.com/v1/messages",
                                    headers={"Content-Type": "application/json", "x-api-key": api_key,
                                             "anthropic-version": "2023-06-01", "anthropic-beta": "pdfs-2024-09-25"},
                                    json={"model": "claude-opus-4-5", "max_tokens": 1500,
                                          "messages": [{"role": "user", "content": msg_content}]},
                                    timeout=90
                                )
                                data = response.json()
                                if "error" in data:
                                    st.error(f"❌ Error Claude: {data['error'].get('message', str(data['error']))}")
                                elif "content" not in data:
                                    st.error("❌ Respuesta inesperada:")
                                    st.json(data)
                                else:
                                    raw = data["content"][0]["text"].strip().replace("```json","").replace("```","").strip()
                                    plan = json.loads(raw)
                                    st.session_state[f"ai_result_{pieza['id']}"] = plan
                                    st.success("✅ Análisis completado con Claude")

                            else:
                                api_key = (st.secrets.get("OPENAI_API_KEY", None) if hasattr(st, "secrets") else None) or os.environ.get("OPENAI_API_KEY", "")
                                if not api_key:
                                    st.error("❌ No se encontró OPENAI_API_KEY en secrets.toml")
                                    st.stop()
                                if is_pdf:
                                    try:
                                        import fitz
                                        pix    = fitz.open(stream=file_bytes, filetype="pdf")[0].get_pixmap(matrix=fitz.Matrix(3,3))
                                        img_b64 = base64.b64encode(pix.tobytes("png")).decode("utf-8")
                                        img_url = f"data:image/png;base64,{img_b64}"
                                    except ImportError:
                                        st.error("❌ Instala pymupdf: pip install pymupdf --break-system-packages")
                                        st.stop()
                                else:
                                    ext = plano_file.name.lower().split(".")[-1]
                                    mime = f"image/{'jpeg' if ext in ['jpg','jpeg'] else ext}"
                                    img_url = f"data:{mime};base64,{file_b64}"
                                msg_content = [
                                    {"type": "text", "text": prompt},
                                    {"type": "image_url", "image_url": {"url": img_url, "detail": "high"}}
                                ]
                                response = requests.post(
                                    "https://api.openai.com/v1/chat/completions",
                                    headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                                    json={"model": "gpt-4o", "max_tokens": 1500,
                                          "messages": [{"role": "user", "content": msg_content}]},
                                    timeout=60
                                )
                                data = response.json()
                                if "error" in data:
                                    st.error(f"❌ Error GPT-4o: {data['error'].get('message', str(data['error']))}")
                                elif "choices" not in data:
                                    st.error("❌ Respuesta inesperada:")
                                    st.json(data)
                                else:
                                    msg_obj = data["choices"][0]["message"]
                                    ct = msg_obj.get("content")
                                    if not ct:
                                        st.error(f"❌ GPT-4o rechazó el plano")
                                        st.info("💡 Intenta con Claude")
                                    else:
                                        raw = ct.strip().replace("```json","").replace("```","").strip()
                                        plan = json.loads(raw)
                                        st.session_state[f"ai_result_{pieza['id']}"] = plan
                                        st.success("✅ Análisis completado con GPT-4o")

                        except json.JSONDecodeError:
                            st.error("❌ Error al parsear JSON de la IA")
                        except Exception as e:
                            st.error(f"❌ Error: {str(e)}")

            # Show AI results if available
            ai_result_key = f"ai_result_{pieza['id']}"
            if ai_result_key in st.session_state:
                plan = st.session_state[ai_result_key]

                st.markdown(f"**📋 Resumen:** {plan.get('resumen', '')}")

                advertencias = plan.get("advertencias", [])
                if advertencias:
                    warn_text = " · ".join(f"⚠️ {a}" for a in advertencias)
                    st.warning(warn_text)

                st.markdown("**💡 Operaciones sugeridas por la IA:**")
                ops_sugeridas = plan.get("operaciones", [])

                df_sugeridas = pd.DataFrame([{
                    "Operación":    op["label"],
                    "Tipo máquina": op["tipo_maq"],
                    "Descripción":  op.get("descripcion", ""),
                    "Setup (hrs)":  op["setup_hrs"],
                    "Ciclo (hrs)":  op["ciclo_hrs"],
                    "Paralelo":     "⚡ Sí" if op.get("paralelo") else "No",
                } for op in ops_sugeridas])
                st.dataframe(df_sugeridas, use_container_width=True, hide_index=True)

                if st.button("✅ Aplicar operaciones sugeridas", key=f"apply_{pieza['id']}"):
                    nuevas_ops = []
                    for idx_op, op in enumerate(ops_sugeridas):
                        new_id = idx_op + 1
                        tipo_val = op["tipo_maq"] if op["tipo_maq"] in TIPOS_MAQUINA else "Lathe 2 Axis"
                        setup_val = float(op["setup_hrs"])
                        ciclo_val = float(op["ciclo_hrs"])
                        paralelo_val = bool(op.get("paralelo", False))

                        nuevas_ops.append({
                            "id":           new_id,
                            "label":        op["label"],
                            "tipo_maq":     tipo_val,
                            "num_maquinas": 1,
                            "setup_hrs":    setup_val,
                            "ciclo_hrs":    ciclo_val,
                            "paralelo":     paralelo_val,
                        })

                        # Limpiar session_state de los widgets para que tomen los valores nuevos
                        for key in [
                            f"tipo_{pieza['id']}_{new_id}",
                            f"nm_{pieza['id']}_{new_id}",
                            f"setup_{pieza['id']}_{new_id}",
                            f"ciclo_{pieza['id']}_{new_id}",
                            f"par_{pieza['id']}_{new_id}",
                            f"lbl_{pieza['id']}_{new_id}",
                        ]:
                            if key in st.session_state:
                                del st.session_state[key]

                        # Pre-cargar los valores correctos en session_state
                        st.session_state[f"tipo_{pieza['id']}_{new_id}"]  = tipo_val
                        st.session_state[f"setup_{pieza['id']}_{new_id}"] = setup_val
                        st.session_state[f"ciclo_{pieza['id']}_{new_id}"] = ciclo_val
                        st.session_state[f"par_{pieza['id']}_{new_id}"]   = paralelo_val
                        st.session_state[f"nm_{pieza['id']}_{new_id}"]    = 1
                        st.session_state[f"lbl_{pieza['id']}_{new_id}"]   = op["label"]

                    st.session_state.piezas[pi]["operaciones"] = nuevas_ops
                    st.success(f"✅ {len(nuevas_ops)} operaciones aplicadas")
                    st.rerun()

        # ── Parámetros de operación PROPIOS de esta pieza ───────────────────
        with st.expander("▸  Parámetros de operación", expanded=False):
            st.markdown("<div class='op-params-box'>", unsafe_allow_html=True)
            pp1, pp2, pp3, pp4, pp5 = st.columns(5)
            with pp1:
                p_maq = st.number_input("Máq. activas", min_value=1, max_value=17,
            value=pieza.get("maq_activas", 7), key=f"pmaq_{pieza['id']}")
                st.session_state.piezas[pi]["maq_activas"] = p_maq
            with pp2:
                p_turn = st.selectbox("Turnos", [1, 2, 3],
            index=pieza.get("turnos", 1) - 1, key=f"pturn_{pieza['id']}")
                st.session_state.piezas[pi]["turnos"] = p_turn
            with pp3:
                p_hrs = st.number_input("Hrs/turno", min_value=6, max_value=12,
            value=pieza.get("hrs_turno", 8), key=f"phrs_{pieza['id']}")
                st.session_state.piezas[pi]["hrs_turno"] = p_hrs
            with pp4:
                p_dias = st.number_input("Días/mes", min_value=15, max_value=26,
            value=pieza.get("dias_mes", 21), key=f"pdias_{pieza['id']}")
                st.session_state.piezas[pi]["dias_mes"] = p_dias
            with pp5:
                p_efic = st.number_input("Eficiencia %", min_value=40, max_value=95,
            value=pieza.get("eficiencia", 65), key=f"pefic_{pieza['id']}")
                st.session_state.piezas[pi]["eficiencia"] = p_efic

            # Mostrar precios calculados para esta pieza
            precios_pieza, fijo_p, hrs_p = calcular_precios_por_tipo(
                p_maq, p_turn, p_hrs, p_dias, p_efic)
            precio_cols = st.columns(5)
            for col, tipo in zip(precio_cols, TIPOS_MAQUINA):
                with col:
                    st.caption(f"{ICONOS_TIPO[tipo]} {tipo}\n**{fmtc(precios_pieza[tipo])}/hr**")
            st.markdown("</div>", unsafe_allow_html=True)

        # ── Materia Prima ────────────────────────────────────────────────────
        with st.expander("▸  Materia prima", expanded=False):
            st.markdown("<div class='mat-prima-box'>", unsafe_allow_html=True)
            mp = pieza["materia_prima"]
            mpa, mpb, mpc = st.columns([1.5, 1.5, 1])
            with mpa:
                figura = st.selectbox("Figura geométrica", list(FIGURAS.keys()),
                    index=list(FIGURAS.keys()).index(mp["figura"]),
                    key=f"fig_{pieza['id']}",
                    format_func=lambda x: f"{FIGURAS[x]['icono']} {x}")
                st.session_state.piezas[pi]["materia_prima"]["figura"] = figura
            with mpb:
                material = st.selectbox("Material", list(MATERIALES_DENSIDAD.keys()),
                    index=list(MATERIALES_DENSIDAD.keys()).index(mp["material"])
                          if mp["material"] in MATERIALES_DENSIDAD else 0,
                    key=f"mpmat_{pieza['id']}")
                st.session_state.piezas[pi]["materia_prima"]["material"] = material
            with mpc:
                modos = ["Por kg", "Por tramo", "Manual"]
                modo_idx = modos.index(mp["modo"]) if mp["modo"] in modos else 0
                modo = st.radio("Costo", modos,
                    index=modo_idx,
                    key=f"mpmod_{pieza['id']}", horizontal=True)
                st.session_state.piezas[pi]["materia_prima"]["modo"] = modo

            dims_labels = FIGURAS[figura]["dims"]
            if figura != "Otro / Manual" and modo == "Por kg":
                IN_TO_MM = 25.4  # 1 pulgada = 25.4 mm

                # Labels adaptados a pulgadas
                dims_labels_in = [lbl.replace("(mm)", "(pulg.)") for lbl in dims_labels]

                dim_cols = st.columns(min(len(dims_labels_in), 4))
                dims_vals_in = []
                for di, (col, lbl) in enumerate(zip(dim_cols, dims_labels_in)):
                    with col:
                        # Valor almacenado en mm → convertir a pulgadas para mostrar
                        val_mm = float(mp["dims"][di]) if di < len(mp["dims"]) else 0.0
                        val_in = val_mm / IN_TO_MM if val_mm > 0 else 0.0
                        val_in_new = st.number_input(lbl, min_value=0.0,
                            value=val_in,
                            step=0.01, format="%.4f",
                            key=f"dim_{pieza['id']}_{di}")
                        dims_vals_in.append(val_in_new)
                        if val_in_new > 0:
                            st.caption(f"= {val_in_new * IN_TO_MM:.2f} mm")

                # Convertir de pulgadas a mm para guardar y calcular
                dims_vals = [v * IN_TO_MM for v in dims_vals_in]
                while len(dims_vals) < 4:
                    dims_vals.append(0.0)
                st.session_state.piezas[pi]["materia_prima"]["dims"] = dims_vals

                vol  = calcular_volumen(figura, dims_vals[:len(dims_labels)])
                peso = calcular_peso_kg(vol, MATERIALES_DENSIDAD.get(material, 7850))

                cp1, cp2, cp3 = st.columns(3)
                with cp1:
                    precio_kg = st.number_input("Precio material ($/kg)", min_value=0.0,
                        value=float(mp["precio_kg"]), step=5.0, key=f"pkg_{pieza['id']}")
                    st.session_state.piezas[pi]["materia_prima"]["precio_kg"] = precio_kg
                with cp2:
                    desp = st.number_input("Desperdicio (%)", min_value=0.0, max_value=80.0,
                        value=float(mp["desperdicio"]), step=1.0, key=f"desp_{pieza['id']}")
                    st.session_state.piezas[pi]["materia_prima"]["desperdicio"] = desp
                with cp3:
                    peso_d    = peso * (1 + desp/100)
                    costo_mat = peso_d * precio_kg
                    st.markdown(
                        f"<div class='peso-result' style='font-size:15px'>"
                        f"Peso/pza: <b>{peso:.4f} kg</b><br>"
                        f"c/desp.: <b>{peso_d:.4f} kg</b><br>"
                        f"<span style='font-size:18px'>Costo: <b>{fmtc(costo_mat)}</b></span>"
                        f"</div>",
                        unsafe_allow_html=True)
            elif modo == "Por tramo":
                # Todo en PULGADAS — conversión interna a mm para cálculos
                KERF_IN = 4.0 / 25.4   # 4mm → pulgadas = 0.157"

                corte_previo = st.checkbox(
                    "✂️ La barra requiere corte previo (barra larga → tramos)",
                    value=mp.get("corte_previo", False),
                    key=f"cprevio_{pieza['id']}",
                    help="Activa si la barra original es más larga que lo que entra al torno"
                )
                st.session_state.piezas[pi]["materia_prima"]["corte_previo"] = corte_previo

                if corte_previo:
                    st.markdown("**✂️ Etapa 1 — Corte de barra larga en tramos**")
                    ba1, ba2, ba3 = st.columns(3)
                    with ba1:
                        largo_barra = st.number_input('Largo de la barra (pulg.)',
                            min_value=0.1, max_value=500.0,
                            value=float(mp.get("largo_barra", 144.0)),
                            step=0.5, key=f"lbarra_{pieza['id']}")
                        st.session_state.piezas[pi]["materia_prima"]["largo_barra"] = largo_barra
                    with ba2:
                        largo_corte = st.number_input('Largo tramo de corte (pulg.)',
                            min_value=0.1, max_value=200.0,
                            value=float(mp.get("largo_corte", 26.0)),
                            step=0.5, key=f"lcorte_{pieza['id']}")
                        st.session_state.piezas[pi]["materia_prima"]["largo_corte"] = largo_corte
                    with ba3:
                        costo_barra = st.number_input('Costo de la barra ($MXN)',
                            min_value=0.0, max_value=999999.0,
                            value=float(mp.get("costo_tramo", 0.0)),
                            step=10.0, key=f"cbarra_{pieza['id']}")
                        st.session_state.piezas[pi]["materia_prima"]["costo_tramo"] = costo_barra

                    tramos_barra    = int(largo_barra / (largo_corte + KERF_IN))
                    sobra_barra     = largo_barra - tramos_barra * (largo_corte + KERF_IN)
                    costo_por_tramo = costo_barra / tramos_barra if tramos_barra > 0 else 0

                    st.markdown(
                        f"<div style='background:#e8eeff;border-radius:8px;"
                        f"padding:8px 14px;font-size:12px;margin-bottom:8px'>"
                        f"Kerf: <b>0.157 in</b> (4mm) &nbsp;&middot;&nbsp;"
                        f"Tramos por barra: <b>{tramos_barra}</b> &nbsp;&middot;&nbsp;"
                        f"Sobrante: <b>{sobra_barra:.3f} in</b> &nbsp;&middot;&nbsp;"
                        f"Costo por tramo: <b>{fmtc(costo_por_tramo)}</b></div>",
                        unsafe_allow_html=True)

                    largo_tramo_in  = largo_corte
                    costo_tramo     = costo_por_tramo
                    st.markdown("**🔩 Etapa 2 — Piezas por tramo en el torno**")

                else:
                    tr1, tr2 = st.columns(2)
                    with tr1:
                        largo_tramo_in = st.number_input('Largo del tramo (pulg.)',
                            min_value=0.1, max_value=200.0,
                            value=float(mp.get("largo_tramo", 31.5)),
                            step=0.5, key=f"ltramo_{pieza['id']}")
                        st.session_state.piezas[pi]["materia_prima"]["largo_tramo"] = largo_tramo_in
                    with tr2:
                        costo_tramo = st.number_input('Costo del tramo ($MXN)',
                            min_value=0.0, max_value=999999.0,
                            value=float(mp.get("costo_tramo", 0.0)),
                            step=10.0, key=f"ctramo_{pieza['id']}")
                        st.session_state.piezas[pi]["materia_prima"]["costo_tramo"] = costo_tramo

                # Datos comunes — largo de pieza y agarre en pulgadas
                pt1, pt2 = st.columns(2)
                with pt1:
                    largo_pieza_in = st.number_input('Largo de la pieza (pulg.)',
                        min_value=0.001, max_value=100.0,
                        value=float(mp.get("largo_pieza", 1.575)),
                        step=0.01, key=f"lpieza_{pieza['id']}")
                    st.session_state.piezas[pi]["materia_prima"]["largo_pieza"] = largo_pieza_in
                with pt2:
                    agarre_in = st.number_input('Agarre torno (pulg.)',
                        min_value=0.0, max_value=10.0,
                        value=float(mp.get("agarre", 0.984)),
                        step=0.1, key=f"agarre_{pieza['id']}",
                        help="Material que se pierde en el agarre del chuck")
                    st.session_state.piezas[pi]["materia_prima"]["agarre"] = agarre_in

                # Resultado final
                largo_util_in = largo_tramo_in - agarre_in
                piezas_tramo  = int(largo_util_in / (largo_pieza_in + KERF_IN))
                sobra_in      = largo_util_in - piezas_tramo * (largo_pieza_in + KERF_IN)
                costo_pza_t   = costo_tramo / piezas_tramo if piezas_tramo > 0 else 0

                color_res = "#EAF3DE" if piezas_tramo > 0 else "#FCEBEB"
                color_res = '#EAF3DE' if piezas_tramo > 0 else '#FCEBEB'
                st.markdown(
                    f"<div style='background:{color_res};border-radius:8px;padding:12px 16px;margin-top:8px'>"
                    f"<span style='font-size:12px'>Largo util: <b>{largo_util_in:.3f} in</b> &nbsp;&middot;&nbsp;"
                    f"Kerf: <b>0.157 in</b> (4mm) &nbsp;&middot;&nbsp;"
                    f"Piezas por tramo: <b>{piezas_tramo}</b> &nbsp;&middot;&nbsp;Sobrante: <b>{sobra_in:.3f} in</b></span><br>"
                    f"<span style='font-size:18px;font-weight:700;color:#185FA5'>Costo material/pza: {fmtc(costo_pza_t)}</span></div>",
                    unsafe_allow_html=True)
            else:
                # Modo Manual
                costo_manual = st.number_input(
                    "Costo de materia prima por pieza ($MXN)",
                    min_value=0.0,
                    value=float(mp["costo_manual"]),
                    step=10.0,
                    key=f"cman_{pieza['id']}",
                    help="Ingresa el precio de la materia prima por pieza ya cortada"
                )
                st.session_state.piezas[pi]["materia_prima"]["costo_manual"] = costo_manual

            # ── Proveedor de materia prima + cotización adjunta ─────────
            st.markdown("---")
            provmp_c1, provmp_c2 = st.columns([2, 2])
            with provmp_c1:
                prov_mp = st.text_input(
                    "🏭 Proveedor de materia prima",
                    value=mp.get("proveedor", ""),
                    key=f"provmp_{pieza['id']}",
                    placeholder="Nombre del proveedor"
                )
                st.session_state.piezas[pi]["materia_prima"]["proveedor"] = prov_mp
            with provmp_c2:
                cot_mp_file = st.file_uploader(
                    "📎 Cotización del proveedor (PDF/imagen)",
                    type=["pdf", "png", "jpg", "jpeg"],
                    key=f"cotmpfile_{pieza['id']}",
                    help="Se guardará junto con la cotización"
                )
                if cot_mp_file is not None:
                    import base64 as _b64
                    file_bytes_mp = cot_mp_file.read()
                    b64_mp = _b64.b64encode(file_bytes_mp).decode("utf-8")
                    st.session_state.piezas[pi]["materia_prima"]["cotizacion_mp_nombre"] = cot_mp_file.name
                    st.session_state.piezas[pi]["materia_prima"]["cotizacion_mp_b64"]    = b64_mp
                    st.success(f"✅ Archivo cargado: {cot_mp_file.name}")
                # Mostrar archivo existente si ya hay uno guardado
                nombre_mp_guardado = mp.get("cotizacion_mp_nombre", "")
                b64_mp_guardado    = mp.get("cotizacion_mp_b64", "")
                if nombre_mp_guardado and b64_mp_guardado and cot_mp_file is None:
                    import base64 as _b64
                    file_bytes_mg = _b64.b64decode(b64_mp_guardado)
                    st.markdown(f"📄 Archivo guardado: **{nombre_mp_guardado}**")
                    st.download_button(
                        f"⬇️ Descargar {nombre_mp_guardado}",
                        data=file_bytes_mg,
                        file_name=nombre_mp_guardado,
                        mime="application/octet-stream",
                        key=f"dlcotmp_{pieza['id']}"
                    )
            st.markdown("</div>", unsafe_allow_html=True)

        # ── Tratamiento, cantidad y demanda ──────────────────────────────────
        with st.expander("▸  Tratamiento y recubrimiento", expanded=False):
            ct1, ct2, ct3 = st.columns([2, 1, 1])
            with ct1:
                trat = st.selectbox("Tratamiento / Recubrimiento", TRATAMIENTOS_LISTA,
                    index=TRATAMIENTOS_LISTA.index(pieza["tratamiento"]),
                    key=f"trat_{pieza['id']}")
                st.session_state.piezas[pi]["tratamiento"] = trat
            with ct2:
                costo_trat = st.number_input("Costo trat./pza ($)", min_value=0.0,
                    value=float(pieza["costo_trat"]), step=5.0,
                    key=f"ctrat_{pieza['id']}", disabled=(trat=="Ninguno"))
                st.session_state.piezas[pi]["costo_trat"] = 0.0 if trat=="Ninguno" else costo_trat
            with ct3:
                dias_trat = st.number_input("Días adicionales", min_value=0, max_value=60,
                    value=int(pieza["dias_trat"]), key=f"dtrat_{pieza['id']}",
                    disabled=(trat=="Ninguno"))
                st.session_state.piezas[pi]["dias_trat"] = 0 if trat=="Ninguno" else dias_trat

            # ── Proveedor de tratamiento + cotización adjunta ────────────
            st.markdown("---")
            prov_t1, prov_t2 = st.columns([2, 2])
            with prov_t1:
                prov_trat = st.text_input(
                    "🏭 Proveedor de tratamiento",
                    value=pieza.get("proveedor_trat", ""),
                    key=f"provtrat_{pieza['id']}",
                    placeholder="Nombre del proveedor"
                )
                st.session_state.piezas[pi]["proveedor_trat"] = prov_trat
            with prov_t2:
                cot_file = st.file_uploader(
                    "📎 Cotización del proveedor (PDF/imagen)",
                    type=["pdf", "png", "jpg", "jpeg"],
                    key=f"cotfile_{pieza['id']}",
                    help="Se guardará junto con la cotización"
                )
                if cot_file is not None:
                    import base64 as _b64
                    file_bytes = cot_file.read()
                    b64_str = _b64.b64encode(file_bytes).decode("utf-8")
                    st.session_state.piezas[pi]["cotizacion_trat_nombre"] = cot_file.name
                    st.session_state.piezas[pi]["cotizacion_trat_b64"]    = b64_str
                    st.success(f"✅ Archivo cargado: {cot_file.name}")
                # Mostrar archivo existente si ya hay uno guardado
                nombre_guardado = pieza.get("cotizacion_trat_nombre", "")
                b64_guardado    = pieza.get("cotizacion_trat_b64", "")
                if nombre_guardado and b64_guardado and cot_file is None:
                    import base64 as _b64
                    file_bytes_g = _b64.b64decode(b64_guardado)
                    st.markdown(f"📄 Archivo guardado: **{nombre_guardado}**")
                    st.download_button(
                        f"⬇️ Descargar {nombre_guardado}",
                        data=file_bytes_g,
                        file_name=nombre_guardado,
                        mime="application/octet-stream",
                        key=f"dlcot_{pieza['id']}"
                    )

            # Demanda mensual (para semáforo de turnos)
            demanda = st.number_input("📦 Demanda mensual requerida (pzas/mes)",
                min_value=0, max_value=999999,
                value=int(pieza.get("demanda_mensual", 0)),
                key=f"dem_{pieza['id']}",
                help="Para calcular turnos necesarios — deja en 0 si no aplica")
            st.session_state.piezas[pi]["demanda_mensual"] = demanda
            cant = pieza["cantidad"]

        # ── Márgenes de utilidad por componente ─────────────────────────────
        usar_global = st.checkbox(
            "Usar margen global para esta pieza",
            value=pieza.get("usar_margen_global", False),
            key=f"mg_{pieza['id']}"
        )
        st.session_state.piezas[pi]["usar_margen_global"] = usar_global

        if usar_global:
            mg_col, = st.columns([1])
            with mg_col:
                mg = st.number_input("Margen global (%)", min_value=0, max_value=200,
                    value=margen_global, key=f"mgval_{pieza['id']}",
                    disabled=True,
                    help="Se usa el margen global del sidebar")
        else:
            mg1, mg2, mg3 = st.columns(3)
            with mg1:
                m_mo = st.number_input("💰 Utilidad mano de obra (%)",
                    min_value=0, max_value=200,
                    value=int(pieza.get("margen_mo", 35)),
                    key=f"mmo_{pieza['id']}")
                st.session_state.piezas[pi]["margen_mo"] = m_mo
            with mg2:
                m_mat = st.number_input("🧱 Utilidad material (%)",
                    min_value=0, max_value=200,
                    value=int(pieza.get("margen_mat", 35)),
                    key=f"mmat_{pieza['id']}")
                st.session_state.piezas[pi]["margen_mat"] = m_mat
            with mg3:
                m_trat = st.number_input("🔬 Utilidad tratamiento (%)",
                    min_value=0, max_value=200,
                    value=int(pieza.get("margen_trat", 35)),
                    key=f"mtrat_{pieza['id']}",
                    disabled=(trat == "Ninguno"))
                st.session_state.piezas[pi]["margen_trat"] = m_trat

        # Semáforo de viabilidad
        if demanda > 0:
            ciclo_ref = max((op["ciclo_hrs"] for op in pieza["operaciones"]), default=0.25)
            setup_ref = max((op["setup_hrs"] for op in pieza["operaciones"]), default=0.5)
            num_ref   = max((op["num_maquinas"] for op in pieza["operaciones"]), default=1)
            info = calcular_semaforo(demanda, ciclo_ref, setup_ref,
                                     p_hrs, p_dias, p_efic, num_ref)
            if info:
                colores = {"🟢": "#EAF3DE", "🟡": "#FAEEDA",
                           "🟠": "#FFF0E8", "🔴": "#FCEBEB"}
                bg = colores.get(info["semaforo"], "#f8f9fc")
                st.markdown(
                    f"<div class='semaforo-box' style='background:{bg}'>"
                    f"<b>{info['semaforo']} {info['status']}</b> &nbsp;&middot;&nbsp;"
                    f"Hrs requeridas: <b>{info['hrs_req']:.1f}</b> /mes &nbsp;&middot;&nbsp;"
                    f"Hrs disponibles: <b>{info['hrs_disp']:.1f}</b> "
                    f"({info['turnos']} turno(s)) &nbsp;&middot;&nbsp;"
                    f"Utilizacion: <b>{info['utilizacion']:.0f}%</b></div>",
                    unsafe_allow_html=True)

        # ── Operaciones ──────────────────────────────────────────────────────
        with st.expander(f"▸  Operaciones — #{pi+1}  {desc or ''}  {ndwg or ''}",
                         expanded=False):
            if st.button("➕ Agregar operación", key=f"addop_{pieza['id']}"):
                noid = max(o["id"] for o in pieza["operaciones"]) + 1
                st.session_state.piezas[pi]["operaciones"].append(nueva_operacion(noid))
                st.rerun()

            h = st.columns([1, 2.5, 1, 1, 1, 1, 1, 0.5])
            for col, lbl in zip(h, ["Nombre","Tipo máquina","# Máq",
                                    "Setup (hrs)","Ciclo (hrs)","Paralelo",
                                    "Costo/pza",""]):
                with col:
                    st.markdown(f"<span class='op-header'>{lbl}</span>",
                                unsafe_allow_html=True)

            ops_a_eliminar = []
            for oi, op in enumerate(pieza["operaciones"]):
                cols = st.columns([1, 2.5, 1, 1, 1, 1, 1, 0.5])
                key_tipo  = f"tipo_{pieza['id']}_{op['id']}"
                key_nm    = f"nm_{pieza['id']}_{op['id']}"
                key_setup = f"setup_{pieza['id']}_{op['id']}"
                key_ciclo = f"ciclo_{pieza['id']}_{op['id']}"

                with cols[0]:
                    lbl2 = st.text_input("n", value=op["label"],
                        key=f"lbl_{pieza['id']}_{op['id']}",
                        label_visibility="collapsed")
                    st.session_state.piezas[pi]["operaciones"][oi]["label"] = lbl2

                with cols[1]:
                    tipo_sel = st.selectbox("t", TIPOS_MAQUINA,
                        index=TIPOS_MAQUINA.index(op.get("tipo_maq","Lathe 2 Axis")),
                        key=key_tipo,
                        format_func=lambda x: f"{ICONOS_TIPO.get(x,'')} {x}",
                        label_visibility="collapsed")
                    st.session_state.piezas[pi]["operaciones"][oi]["tipo_maq"] = tipo_sel

                with cols[2]:
                    nm = st.number_input("nm", min_value=1, max_value=5,
                        value=op["num_maquinas"], key=key_nm,
                        label_visibility="collapsed")
                    st.session_state.piezas[pi]["operaciones"][oi]["num_maquinas"] = nm

                with cols[3]:
                    setup = st.number_input("s", min_value=0.0, max_value=999.0,
                        value=float(op["setup_hrs"]), step=0.1, key=key_setup,
                        label_visibility="collapsed")
                    st.session_state.piezas[pi]["operaciones"][oi]["setup_hrs"] = setup

                with cols[4]:
                    ciclo = st.number_input("c", min_value=0.0, max_value=999.0,
                        value=float(op["ciclo_hrs"]), step=0.01, key=key_ciclo,
                        label_visibility="collapsed")
                    st.session_state.piezas[pi]["operaciones"][oi]["ciclo_hrs"] = ciclo

                with cols[5]:
                    if oi > 0:
                        par = st.checkbox("⚡", value=op["paralelo"],
                            key=f"par_{pieza['id']}_{op['id']}")
                        st.session_state.piezas[pi]["operaciones"][oi]["paralelo"] = par
                    else:
                        st.caption("1ra op")

                with cols[6]:
                    # Costo/pza en tiempo real
                    tipo_rt2  = st.session_state.get(key_tipo, tipo_sel)
                    nm_rt2    = int(st.session_state.get(key_nm, nm))
                    setup_rt2 = float(st.session_state.get(key_setup, setup))
                    ciclo_rt2 = float(st.session_state.get(key_ciclo, ciclo))
                    ph2       = precios_pieza.get(tipo_rt2, 1200) * nm_rt2
                    sp2       = setup_rt2 / max(cant, 1)
                    cp2       = (sp2 + ciclo_rt2) * ph2
                    st.markdown(
                        f"<div style='margin-top:4px;padding:6px 0;"
                        f"font-size:15px;font-weight:700;color:#27500A;"
                        f"text-align:left;line-height:2.2'>{fmtc(cp2)}</div>",
                        unsafe_allow_html=True
                    )

                with cols[7]:
                    if len(pieza["operaciones"]) > 1:
                        if st.button("×", key=f"delop_{pieza['id']}_{op['id']}"):
                            ops_a_eliminar.append(oi)

                # Costo inline — usa precios de ESTA pieza
                tipo_rt  = st.session_state.get(key_tipo, tipo_sel)
                nm_rt    = int(st.session_state.get(key_nm, nm))
                setup_rt = float(st.session_state.get(key_setup, setup))
                ciclo_rt = float(st.session_state.get(key_ciclo, ciclo))
                ph       = precios_pieza.get(tipo_rt, 1200) * nm_rt
                sp       = setup_rt / max(cant, 1)
                tp       = sp + ciclo_rt
                cp_op    = tp * ph


            if ops_a_eliminar:
                st.session_state.piezas[pi]["operaciones"] = [
                    o for oi2, o in enumerate(pieza["operaciones"])
                    if oi2 not in ops_a_eliminar]
                st.rerun()

            # ── Sumatoria de Setup y Ciclo (respeta paralelo) ───────────
            if len(pieza["operaciones"]) > 1:
                ops_curr = st.session_state.piezas[pi]["operaciones"]

                # Setup total = suma de todos (cada op tiene su propio setup)
                total_setup = sum(op["setup_hrs"] for op in ops_curr)
                total_setup_pza = total_setup / max(cant, 1)

                # Ciclo total = camino crítico (igual que calcular_pieza)
                etapas = []
                for op in ops_curr:
                    if not op.get("paralelo", False) or not etapas:
                        etapas.append([op])
                    else:
                        etapas[-1].append(op)
                ciclo_critico = sum(
                    max(o["ciclo_hrs"] for o in e) for e in etapas
                )

                tiempo_total_pza = total_setup_pza + ciclo_critico

                # Nota de paralelo
                hay_paralelo = any(op.get("paralelo", False) for op in ops_curr)
                nota_paralelo = " &nbsp;<span style='color:#854F0B'>(⚡ camino crítico — paralelo aplicado)</span>" if hay_paralelo else ""

                st.markdown(
                    f"<div style='background:#E6F1FB;border-left:3px solid #185FA5;"
                    f"border-radius:0 0 6px 6px;padding:8px 14px;font-size:13px;"
                    f"margin-top:4px;font-size:15px;color:#0C447C'>"
                    f"<b>Σ Totales:</b> &nbsp;"
                    f"Setup total: <b>{total_setup:.2f} hrs</b> "
                    f"(÷{cant} pzas = <b>{total_setup_pza*60:.2f} min/pza</b>) &nbsp;·&nbsp; "
                    f"Ciclo (camino crítico): <b>{ciclo_critico*60:.2f} min/pza</b>{nota_paralelo} &nbsp;·&nbsp; "
                    f"<b>Tiempo total/pza: {tiempo_total_pza*60:.2f} min</b>"
                    f"</div>",
                    unsafe_allow_html=True
                )

            flujo = " → ".join(
                ("⚡ " if (oi2>0 and o["paralelo"]) else "") +
                f"{ICONOS_TIPO.get(o.get('tipo_maq',''),'⚙️')} **{o['label']}** ({o.get('tipo_maq','')})"
                for oi2, o in enumerate(pieza["operaciones"])
            )
            st.caption(f"Flujo: {flujo}")

        # Resumen rápido
        res = calcular_pieza(pieza, margen_global)
        tipo_ped = pieza.get("tipo_pedido", "Pedido único")

        if tipo_ped == "Por proyecto":
            moq_val = pieza.get("moq", 0)
            eau_val = pieza.get("eau", 0)
            total_moq = res["precio_pza"] * moq_val
            total_eau = res["precio_pza"] * eau_val

            r1, r2, r3, r4 = st.columns(4)
            r1.metric("Maquinado/pza",   fmtc(res["costo_maq"]))
            r2.metric("Material/pza",    fmtc(res["costo_material"]))
            r3.metric("Tratamiento/pza", fmtc(res["costo_trat"]))
            with r4:
                st.markdown(
                    f"<div style='padding:4px 0'>"
                    f"<div style='font-size:14px;color:#6b7280;font-weight:400;margin-bottom:4px'>Precio/pza</div>"
                    f"<div style='font-size:2rem;font-weight:700;color:#16a34a;line-height:1.2'>{fmtc(res['precio_pza'])}</div>"
                    f"</div>", unsafe_allow_html=True)

            st.markdown(
                f"<div style='display:flex;gap:12px;margin-top:8px'>"
                f"<div style='flex:1;background:#e8eeff;border:1px solid #1a5cff;"
                f"border-radius:8px;padding:12px 16px;text-align:center'>"
                f"<div style='font-size:11px;color:#5a6278;text-transform:uppercase;"
                f"font-weight:600'>📦 Total MOQ ({moq_val:,} pzas/mes)</div>"
                f"<div style='font-size:24px;font-weight:800;color:#1a5cff'>"
                f"{fmtc(total_moq)}</div>"
                f"</div>"
                f"<div style='flex:1;background:#f0f4e8;border:1px solid #5a9e2f;"
                f"border-radius:8px;padding:12px 16px;text-align:center'>"
                f"<div style='font-size:11px;color:#5a6278;text-transform:uppercase;"
                f"font-weight:600'>📅 Total EAU ({eau_val:,} pzas/año)</div>"
                f"<div style='font-size:24px;font-weight:800;color:#3a7a1a'>"
                f"{fmtc(total_eau)}</div>"
                f"</div>"
                f"</div>",
                unsafe_allow_html=True)
        else:
            r1, r2, r3, r4, r5 = st.columns(5)
            r1.metric("Maquinado/pza",    fmtc(res["costo_maq"]))
            r2.metric("Material/pza",     fmtc(res["costo_material"]))
            r3.metric("Tratamiento/pza",  fmtc(res["costo_trat"]))
            with r4:
                st.markdown(
                    f"<div style='padding:4px 0'>"
                    f"<div style='font-size:14px;color:#6b7280;font-weight:400;margin-bottom:4px'>Precio/pza</div>"
                    f"<div style='font-size:2rem;font-weight:700;color:#16a34a;line-height:1.2'>{fmtc(res['precio_pza'])}</div>"
                    f"</div>", unsafe_allow_html=True)
            r5.metric(f"Total {cant} pzas", fmtc(res["total"]))

        st.markdown("</div>", unsafe_allow_html=True)

    if piezas_a_eliminar:
        for idx in sorted(piezas_a_eliminar, reverse=True):
            st.session_state.piezas.pop(idx)
        st.rerun()

# ══ TAB 2: COTIZACIÓN ════════════════════════════════════════════════════════
with tab2:
    # ── Botón guardar en Supabase ─────────────────────────────────────────
    sv1, sv2 = st.columns([3, 1])
    with sv2:
        if st.button("💾 Guardar cotización", use_container_width=True,
                     help="Guardar en Google Sheets"):
            guardar_cotizacion()
    st.markdown("---")

    st.markdown(
        f"<div style='background:#f0f2f7;border-radius:10px;padding:16px 20px;"
        f"margin-bottom:16px;border-left:4px solid #1a5cff;'>"
        f"<b>{num_cot}</b> &nbsp;&middot;&nbsp;"
        f"Cliente: <b>{cliente or '—'}</b> &nbsp;&middot;&nbsp;"
        f"{atencion or '—'} &nbsp;&middot;&nbsp; {ciudad or '—'} &nbsp;&middot;&nbsp;"
        f"{datetime.now().strftime('%d/%m/%Y')}",
        unsafe_allow_html=True)

    st.markdown("#### Resumen de piezas")
    filas         = []
    total_general = 0
    for i, pieza in enumerate(st.session_state.piezas):
        res = calcular_pieza(pieza, margen_global)
        total_general += res["total"]
        mp = pieza["materia_prima"]
        sem = res["semaforo"]
        tipo_ped_i = pieza.get("tipo_pedido", "Pedido único")
        moq_i = pieza.get("moq", 0)
        eau_i = pieza.get("eau", 0)

        if tipo_ped_i == "Por proyecto":
            total_moq_i = res["precio_pza"] * moq_i
            total_eau_i = res["precio_pza"] * eau_i
            total_display = fmtc(total_moq_i)
            cant_display  = f"MOQ:{moq_i:,} / EAU:{eau_i:,}"
        else:
            total_display = fmtc(res["total"])
            cant_display  = str(pieza["cantidad"])

        filas.append({
            "Item":          i + 1,
            "Núm. Dibujo":   pieza["num_dibujo"] or "—",
            "Descripción":   pieza["descripcion"] or "—",
            "Tipo pedido":   tipo_ped_i,
            "Cantidad":      cant_display,
            "Material":      mp["material"],
            "Tratamiento":   pieza["tratamiento"],
            "P. Unitario":   fmtc(res["precio_pza"]),
            "Total MOQ":     fmtc(res["precio_pza"] * moq_i) if tipo_ped_i == "Por proyecto" else "—",
            "Total EAU":     fmtc(res["precio_pza"] * eau_i) if tipo_ped_i == "Por proyecto" else "—",
            "Total":         total_display,
        })

    st.dataframe(pd.DataFrame(filas), use_container_width=True, hide_index=True)

    iva        = total_general * 0.16
    total_neto = total_general + iva

    col1, col2 = st.columns(2)
    with col1:
        st.markdown(
            f"<div class=\"result-box\">"
            f"<div class=\"label\">SUBTOTAL</div>"
            f"<div class=\"price\">{fmtc(total_general)}</div>"
            f"<div class=\"label\">MXN &middot; {len(st.session_state.piezas)} pieza(s)</div>",
            unsafe_allow_html=True)
    with col2:
        alt_mon = f"MXN: ${total_general:,.2f}" if moneda_cot=="USD" else f"USD: ${total_general/tipo_cambio:,.2f}"
        st.markdown(
            "<div class='total-box'>"
            "<div style='font-size:10px;color:#6b7280;text-transform:uppercase;letter-spacing:0.06em'>"
            "Total neto (IVA 16%)</div>"
            f"<div style='font-size:32px;font-weight:500;color:#0f1b3d;margin:4px 0'>{fmtc(total_neto)}</div>"
            f"<div style='font-size:12px;color:#6b7280'>IVA: {fmtc(iva)} &nbsp;&middot;&nbsp; {alt_mon}</div>"
            "</div>",
            unsafe_allow_html=True)

    st.markdown("---")
    c1, c2, c3, c4 = st.columns(4)
    c1.info(f"📅 {vigencia}")
    c2.info(f"🚚 {t_entrega}")
    c3.info(f"💳 {cond_pago}")
    c4.info(f"💱 {moneda_cot}")

    with st.expander("📊 Desglose detallado por pieza"):
        for i, pieza in enumerate(st.session_state.piezas):
            res = calcular_pieza(pieza, margen_global)
            mp  = pieza["materia_prima"]
            st.markdown(
                f"**#{i+1} — {pieza['descripcion'] or '—'} · "
                f"{pieza['num_dibujo'] or '—'} · {mp['material']}** "
                f"| {pieza.get('turnos',1)} turno(s) · "
                f"{pieza.get('maq_activas',7)} máq. activas · "
                f"{pieza.get('eficiencia',65)}% efic."
            )
            df_ops = pd.DataFrame([{
                "Operación":    op["label"],
                "Tipo máquina": op["tipo_maq"],
                "Modo":         "⚡ Paralelo" if (oi>0 and op["paralelo"]) else "⛓ Serie",
                "$/hr":         fmtc(op["precio_hr"]),
                "Setup/pza":    f"{op['setup_pza']*60:.2f} min",
                "Ciclo/pza":    f"{op['ciclo_hrs']*60:.2f} min",
                "Costo/pza":    fmtc(op["costo_pza"]),
            } for oi, op in enumerate(res["ops_resultado"])])
            st.dataframe(df_ops, use_container_width=True, hide_index=True)
            m1,m2,m3,m4,m5 = st.columns(5)
            m1.metric("Maquinado/pza",    fmtc(res["costo_maq"]))
            m2.metric("Material/pza",     fmtc(res["costo_material"]))
            m3.metric("Tratamiento/pza",  fmtc(res["costo_trat"]))
            m4.metric("Precio final/pza", fmtc(res["precio_pza"]))
            m5.metric("Total orden",      fmtc(res["total"]))
            st.markdown("---")

    if st.button("💾 Guardar cotización", use_container_width=True):
        guardar_cotizacion()

# ══ TAB 3: HISTORIAL ══════════════════════════════════════════════════════════
with tab3:
    st.markdown("#### 🗂️ Historial de cotizaciones")

    col_ref, col_btn = st.columns([3,1])
    with col_btn:
        if st.button("🔄 Actualizar", use_container_width=True):
            st.rerun()

    cotizaciones_db = cargar_cotizaciones()

    if not cotizaciones_db:
        # Fallback a historial local
        if not st.session_state.get("cotizaciones"):
            st.info("No hay cotizaciones guardadas. Usa 💾 en la pestaña Cotización.")
        else:
            st.dataframe(pd.DataFrame([{
                "Cotización": c["numero"], "Fecha": c["fecha"],
                "Cliente": c["cliente"], "Total": fmtc(c["total_neto"]),
            } for c in st.session_state.cotizaciones]), use_container_width=True, hide_index=True)
    else:
        c1, c2, c3 = st.columns(3)
        with c1: buscar_cot = st.text_input("🔍 Núm. cotización", placeholder="COT-")
        with c2: buscar_cli = st.text_input("🔍 Cliente",          placeholder="Nombre")
        with c3: buscar_dwg = st.text_input("🔍 Núm. dibujo",      placeholder="DWG-")

        filtradas = cotizaciones_db
        if buscar_cot: filtradas = [c for c in filtradas if buscar_cot.upper() in (c.get("numero","")).upper()]
        if buscar_cli: filtradas = [c for c in filtradas if buscar_cli.upper() in (c.get("cliente","")).upper()]
        if buscar_dwg: filtradas = [c for c in filtradas if buscar_dwg.upper() in (c.get("num_dibujos","")).upper()]

        ESTADOS = ["borrador", "enviada", "ganada", "perdida"]
        ICONOS  = {"borrador": "📝", "enviada": "📤", "ganada": "✅", "perdida": "❌"}
        COLORES = {"borrador": "#6b7280", "enviada": "#185FA5", "ganada": "#27500A", "perdida": "#991b1b"}

        if filtradas:
            # Tabla con status editable inline
            cols_header = st.columns([1.0, 1.5, 1.5, 1.0, 2.0, 0.6, 1.1, 0.6, 0.9, 1.5])
            for col, h in zip(cols_header, ["Cotización", "Fecha", "Cliente", "Núm. Dibujo", "Descripción", "Cant.", "Total", "Moneda", "Status", "Cambiar status"]):
                with col:
                    st.markdown(f"<span style='font-size:11px;font-weight:600;color:#9aa3b8;text-transform:uppercase;letter-spacing:0.06em'>{h}</span>", unsafe_allow_html=True)
            st.markdown("<hr style='margin:4px 0 8px'>", unsafe_allow_html=True)

            for ci, c in enumerate(filtradas):
                status_actual = c.get("status", "borrador").lower()
                if status_actual not in ESTADOS:
                    status_actual = "borrador"
                color  = COLORES.get(status_actual, "#6b7280")
                icono  = ICONOS.get(status_actual, "📝")
                # Mostrar núm. dibujos (pueden ser varios separados por |)
                dwgs  = c.get("num_dibujos",   "—") or "—"
                descs = c.get("descripciones", "—") or "—"

                fecha_raw = c.get("fecha", c.get("created_at",""))
                cols_row = st.columns([1.0, 1.5, 1.5, 1.0, 2.0, 0.6, 1.1, 0.6, 0.9, 1.5])
                with cols_row[0]: st.markdown(f"**{c.get('numero','')}**")
                with cols_row[1]: st.markdown(fecha_raw[:16])
                with cols_row[2]: st.markdown(c.get("cliente","—"))
                # Mostrar items verticalmente (dwg / desc / cant por línea)
                items_lista = c.get("items_lista", [])
                if items_lista:
                    dwg_html  = "<br>".join(i["dwg"]        for i in items_lista)
                    desc_html = "<br>".join(i["desc"]       for i in items_lista)
                    cant_html = "<br>".join(f"<b>{i['cant']}</b>" for i in items_lista)
                else:
                    dwg_html  = dwgs
                    desc_html = descs
                    cant_html = f"<b>{c.get('cantidad_total','—')}</b>"
                with cols_row[3]: st.markdown(dwg_html,  unsafe_allow_html=True)
                with cols_row[4]: st.markdown(desc_html, unsafe_allow_html=True)
                with cols_row[5]: st.markdown(cant_html, unsafe_allow_html=True)
                with cols_row[6]:
                    if items_lista and any(i.get("total",0) for i in items_lista):
                        total_html = "<br>".join(fmtc(i.get("total",0)) for i in items_lista)
                        st.markdown(total_html, unsafe_allow_html=True)
                    else:
                        st.markdown(fmtc(float(c.get("total_neto", 0) or 0)))
                with cols_row[7]: st.markdown(c.get("moneda","MXN"))
                with cols_row[8]:
                    st.markdown(f"<span style='background:{color};color:white;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600'>{icono} {status_actual.upper()}</span>", unsafe_allow_html=True)
                with cols_row[9]:
                    nuevo = st.selectbox("s", [e for e in ESTADOS if e != status_actual],
                        key=f"hs_{ci}", label_visibility="collapsed")
                    if st.button("Actualizar", key=f"hu_{ci}", use_container_width=True):
                        ok, err = actualizar_status_gsheet(c.get("numero",""), nuevo)
                        if ok:
                            st.success(f"✅ Status actualizado a {nuevo}")
                            st.rerun()
                        else:
                            st.error(f"❌ {err}")

            st.markdown("---")
            numeros   = [c.get("numero","").strip() for c in filtradas if c.get("numero","").strip()]
            sel_num   = st.selectbox("📂 Abrir cotización:", ["— Selecciona —"] + numeros)

            if sel_num != "— Selecciona —":
                cot_sel = next((c for c in filtradas if c.get("numero") == sel_num), None)
                if cot_sel:
                    col_a, col_b = st.columns(2)
                    with col_a:
                        st.markdown(f"**Cliente:** {cot_sel.get('cliente','')}")
                        st.markdown(f"**Total:** {fmtc(float(cot_sel.get('total_neto',0)))}")
                    with col_b:
                        st.markdown(f"**Fecha:** {cot_sel.get('fecha', cot_sel.get('created_at',''))[:10]}")
                        st.markdown(f"**Moneda:** {cot_sel.get('moneda','MXN')}")

                    if st.button(f"📂 Cargar cotización {sel_num}", type="primary"):
                        try:
                            datos_raw = cot_sel.get("datos_json", "{}")
                            # Buscar JSON válido si datos_json está vacío o corrupto
                            if not (isinstance(datos_raw, str) and datos_raw.strip().startswith("{")):
                                datos_raw = "{}"
                                # Intentar recuperarlo de cualquier celda de la fila
                                for v in cot_sel.values():
                                    if isinstance(v, str) and v.strip().startswith("{") and '"piezas"' in v:
                                        try:
                                            json.loads(v)
                                            datos_raw = v
                                            break
                                        except Exception:
                                            pass
                            try:
                                datos = json.loads(datos_raw)
                            except Exception:
                                datos = {}
                            # Manejar ambos formatos: lista directa o dict con "piezas"
                            if isinstance(datos, list):
                                piezas_cargadas = datos
                                cond = {}
                            else:
                                piezas_cargadas = datos.get("piezas", [])
                                cond  = datos.get("cond_generales", {})

                            # ── Paso 1: Borrar TODOS los keys de widgets de piezas ──────────
                            prefixes_to_clear = [
                                "ndwg_", "desc_", "cant_", "tped_", "moq_", "eau_",
                                "pmaq_", "pturn_", "phrs_", "pdias_", "pefic_",
                                "fig_", "mpmat_", "mpmod_", "pkg_", "desp_",
                                "trat_", "ctrat_", "dtrat_", "dem_",
                                "mmo_", "mmat_", "mtrat_", "mg_",
                                "tipo_", "nm_", "setup_", "ciclo_", "par_", "lbl_",
                                "ltramo_", "ctramo_", "lpieza_", "agarre_", "cman_",
                                "dim_", "cprevio_", "lbarra_", "lcorte_", "cbarra_",
                                "plano_", "notas_plano_", "ia_engine_",
                                "toggle_prev_", "analyze_",
                                "provtrat_", "provmp_",
                                "cotfile_", "cotmpfile_",
                            ]
                            keys_to_delete = [k for k in list(st.session_state.keys())
                                if any(k.startswith(p) for p in prefixes_to_clear)]
                            for k in keys_to_delete:
                                del st.session_state[k]

                            # ── Paso 2: Pre-escribir los valores de cada pieza en session_state ──
                            # Esto garantiza que los widgets los lean correctamente en el rerun
                            # aunque el parámetro value= sea ignorado si el key ya existía.
                            for p in piezas_cargadas:
                                pid = p["id"]
                                mp  = p.get("materia_prima", {})
                                # Identificación
                                st.session_state[f"ndwg_{pid}"]  = p.get("num_dibujo", "")
                                st.session_state[f"desc_{pid}"]  = p.get("descripcion", "")
                                tipo_ped = p.get("tipo_pedido", "Pedido único")
                                st.session_state[f"tped_{pid}"]  = tipo_ped
                                if tipo_ped == "Por proyecto":
                                    st.session_state[f"moq_{pid}"] = int(p.get("moq", 0))
                                    st.session_state[f"eau_{pid}"] = int(p.get("eau", 0))
                                else:
                                    st.session_state[f"cant_{pid}"] = int(p.get("cantidad", 1))
                                # Parámetros de operación
                                st.session_state[f"pmaq_{pid}"]  = int(p.get("maq_activas", 7))
                                st.session_state[f"pturn_{pid}"] = int(p.get("turnos", 1))
                                st.session_state[f"phrs_{pid}"]  = int(p.get("hrs_turno", 8))
                                st.session_state[f"pdias_{pid}"] = int(p.get("dias_mes", 21))
                                st.session_state[f"pefic_{pid}"] = int(p.get("eficiencia", 65))
                                # Materia prima
                                fig_val = mp.get("figura", "Redondo (barra)")
                                st.session_state[f"fig_{pid}"]   = fig_val
                                st.session_state[f"mpmat_{pid}"] = mp.get("material", "Inox 303")
                                st.session_state[f"mpmod_{pid}"] = mp.get("modo", "Por tramo")
                                st.session_state[f"pkg_{pid}"]   = float(mp.get("precio_kg", 0.0))
                                st.session_state[f"desp_{pid}"]  = float(mp.get("desperdicio", 10.0))
                                st.session_state[f"cman_{pid}"]  = float(mp.get("costo_manual", 0.0))
                                # Dimensiones
                                dims = mp.get("dims", [0.0, 0.0, 0.0, 0.0])
                                for di, dv in enumerate(dims[:4]):
                                    st.session_state[f"dim_{pid}_{di}"] = float(dv)
                                # Modo tramo
                                st.session_state[f"cprevio_{pid}"] = bool(mp.get("corte_previo", False))
                                st.session_state[f"ltramo_{pid}"]  = float(mp.get("largo_tramo", 31.5))
                                st.session_state[f"lpieza_{pid}"]  = float(mp.get("largo_pieza", 1.575))
                                st.session_state[f"agarre_{pid}"]  = float(mp.get("agarre", 0.984))
                                st.session_state[f"ctramo_{pid}"]  = float(mp.get("costo_tramo", 0.0))
                                st.session_state[f"lbarra_{pid}"]  = float(mp.get("largo_barra", 144.0))
                                st.session_state[f"lcorte_{pid}"]  = float(mp.get("largo_corte", 26.0))
                                st.session_state[f"cbarra_{pid}"]  = float(mp.get("costo_tramo", 0.0))
                                # Tratamiento
                                st.session_state[f"trat_{pid}"]     = p.get("tratamiento", "Ninguno")
                                st.session_state[f"ctrat_{pid}"]    = float(p.get("costo_trat", 0.0))
                                st.session_state[f"dtrat_{pid}"]    = int(p.get("dias_trat", 0))
                                st.session_state[f"dem_{pid}"]      = int(p.get("demanda_mensual", 0))
                                st.session_state[f"provtrat_{pid}"] = p.get("proveedor_trat", "")
                                # Proveedor materia prima
                                st.session_state[f"provmp_{pid}"]   = p.get("materia_prima", {}).get("proveedor", "")
                                # Plano — se restaura directo desde piezas_cargadas (no necesita widget key)
                                # plano_b64, plano_nombre, plano_tipo están en el dict de la pieza
                                # Nota: cotizacion_mp_b64 se restaura directo desde piezas_cargadas, no necesita key de widget
                                # Márgenes
                                st.session_state[f"mg_{pid}"]     = bool(p.get("usar_margen_global", False))
                                st.session_state[f"mmo_{pid}"]    = int(p.get("margen_mo", 35))
                                st.session_state[f"mmat_{pid}"]   = int(p.get("margen_mat", 35))
                                st.session_state[f"mtrat_{pid}"]  = int(p.get("margen_trat", 35))
                                # Operaciones
                                for op in p.get("operaciones", []):
                                    oid = op["id"]
                                    st.session_state[f"lbl_{pid}_{oid}"]   = op.get("label", f"Op {oid*10}")
                                    st.session_state[f"tipo_{pid}_{oid}"]  = op.get("tipo_maq", "Lathe 2 Axis")
                                    st.session_state[f"nm_{pid}_{oid}"]    = int(op.get("num_maquinas", 1))
                                    st.session_state[f"setup_{pid}_{oid}"] = float(op.get("setup_hrs", 0.5))
                                    st.session_state[f"ciclo_{pid}_{oid}"] = float(op.get("ciclo_hrs", 0.25))
                                    st.session_state[f"par_{pid}_{oid}"]   = bool(op.get("paralelo", False))

                            # ── Paso 3: Asignar las piezas al session_state ──────────────────
                            st.session_state.piezas = piezas_cargadas

                            # ── Paso 4: Datos del sidebar y condiciones ──────────────────────
                            st.session_state.num_cot_generado = cot_sel.get("numero", "")
                            st.session_state.sufijo_cot      = cot_sel.get("numero", "").split("-")[0] if "-" in cot_sel.get("numero","") else "COT"
                            st.session_state.sufijo_anterior = st.session_state.sufijo_cot
                            st.session_state["_cliente"]    = cot_sel.get("cliente", "")
                            st.session_state["_atencion"]   = cot_sel.get("atencion", "")
                            st.session_state["_direccion"]  = cot_sel.get("direccion", "")
                            st.session_state["_cp"]         = cot_sel.get("cp", "")
                            st.session_state["_ciudad"]     = cot_sel.get("ciudad", "")
                            st.session_state["_pais"]       = cot_sel.get("pais", "")
                            _m = cot_sel.get("moneda", "MXN")
                            st.session_state["_moneda"] = _m if _m in ("MXN","USD") else "MXN"
                            try:
                                st.session_state["_tipo_cambio"] = float(cot_sel.get("tipo_cambio", 17.31) or 17.31)
                            except (ValueError, TypeError):
                                st.session_state["_tipo_cambio"] = 17.31
                            try:
                                st.session_state["_margen"] = int(float(cot_sel.get("margen_global", 35) or 35))
                            except (ValueError, TypeError):
                                st.session_state["_margen"] = 35
                            st.session_state["_vigencia"]    = cond.get("vigencia", "15 Días")
                            st.session_state["_t_entrega"]   = cond.get("tiempo_entrega", "22-30 días hábiles")
                            st.session_state["_cond_pago"]   = cond.get("cond_pago", "40% anticipo - 60% contra-entrega")

                            st.success(f"✅ Proyecto {sel_num} cargado completo — ve a Piezas y Ruteo")
                            st.rerun()
                        except Exception as e:
                            st.error(f"❌ Error al cargar: {str(e)}")
        else:
            st.warning("No se encontraron cotizaciones.")
st.markdown("---")
st.caption("JAAN Manufacturing · Sistemas de Manufactura Industrial JAAN CNC S.A. de C.V · RFC SAM2008079G8")
