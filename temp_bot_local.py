import requests
import time
import psycopg2
from telnetlib import Telnet
from datetime import datetime, timezone, timedelta

DATABASE_URL = 'postgresql://neondb_owner:npg_eDJ9A0uvUitH@ep-restless-paper-aet1mqw8-pooler.c-2.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require'

# Configuración Telnet de la fuente del imán (ajustá la IP/Puerto si varía)
TELNET_IP = '192.168.1.1'
TELNET_PORT = 7180 # Puerto estándar de comunicación

PLATOS = {
    '50K':  {'ch': 1, 'mult': 1.0,  'round': 3},
    '4K':   {'ch': 2, 'mult': 1.0,  'round': 4},
    'STILL':{'ch': 5, 'mult': 1e3,  'round': 2},
    'MXC':  {'ch': 6, 'mult': 1e3,  'round': 2}
}

def init_db():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS monitoreo_crio (
            id INT PRIMARY KEY DEFAULT 1,
            fecha TIMESTAMP,
            c1_50k REAL,
            c2_4k REAL,
            c5_still REAL,
            c6_mxc REAL,
            campo_actual REAL,
            campo_target REAL,
            estado TEXT,
            CONSTRAINT single_row CHECK (id = 1)
        )
    ''')
    # Garantiza que las columnas existan si la tabla ya había sido creada antes
    cur.execute('ALTER TABLE monitoreo_crio ADD COLUMN IF NOT EXISTS campo_actual REAL;')
    cur.execute('ALTER TABLE monitoreo_crio ADD COLUMN IF NOT EXISTS campo_target REAL;')
    conn.commit()
    cur.close()
    conn.close()

def consulta(tn, code):
    tn.write(code.encode('ascii') + b"\n")
    return tn.read_until(b"\n").decode('ascii').strip()

def orden(tn, code):
    tn.write(code.encode('ascii') + b"\n")

def leer_campo_magnetico():
    try:
        tn = Telnet(TELNET_IP, TELNET_PORT, timeout=3)
        val = float(consulta(tn, 'FIELD:MAGnet?'))
        tn.close()
        return val
    except Exception:
        return None

def ejecutar_cambio_campo(target_b):
    """Setea el target con ramp_rate de 0.25 T/min usando tus comandos SCPI"""
    if 0.0 <= target_b <= 7.0:
        try:
            print(f"⚙️ Cambiando campo magnético a {target_b} T...")
            tn = Telnet(TELNET_IP, TELNET_PORT, timeout=5)
            orden(tn, f'CONFigure:FIELD:TARGet {target_b}')
            orden(tn, 'CONFigure:RAMP:RATE:FIELD 1,0.25,1') # Rate asignado a 0.25 T/min
            orden(tn, 'RAMP')
            tn.close()
            return True
        except Exception as e:
            print(f"Error al enviar comandos Telnet al imán: {e}")
            return False
    return False

def leer_canal(canal):
    try:
        req = requests.get("http://192.168.1.1:5001/channel/measurement/latest", timeout=3)
        data = req.json()
        if data.get('channel_nr') == canal:
            return data.get('temperature')
    except Exception:
        return None
    return None

init_db()
print("🚀 Agente local con control de campo iniciado...")

temps = {'50K': None, '4K': None, 'STILL': None, 'MXC': None}

while True:
    # 1. Leer temperaturas
    for _ in range(8):
        for nombre, p in PLATOS.items():
            val = leer_canal(p['ch'])
            if val is not None:
                temps[nombre] = round(val * p['mult'], p['round'])
        time.sleep(1)

    # 2. Leer Campo Magnético actual
    campo_act = leer_campo_magnetico()

    # 3. Consultar si hay órdenes pendientes de cambio de campo desde Telegram
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute('SELECT campo_target FROM monitoreo_crio WHERE id = 1')
        row = cur.fetchone()
        
        if row and row[0] is not None:
            target_b = float(row[0])
            exito = ejecutar_cambio_campo(target_b)
            if exito:
                # Limpiar orden para no ejecutar repetidamente
                cur.execute('UPDATE monitoreo_crio SET campo_target = NULL WHERE id = 1')
                conn.commit()

        tz_ar = timezone(timedelta(hours=-3))
        ahora = datetime.now(tz_ar)
        estado = "ONLINE" if any(v is not None for v in temps.values()) else "OFFLINE"

        # 4. Actualizar estado en Neon
        cur.execute('''
            INSERT INTO monitoreo_crio (id, fecha, c1_50k, c2_4k, c5_still, c6_mxc, campo_actual, estado)
            VALUES (1, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                fecha = EXCLUDED.fecha,
                c1_50k = EXCLUDED.c1_50k,
                c2_4k = EXCLUDED.c2_4k,
                c5_still = EXCLUDED.c5_still,
                c6_mxc = EXCLUDED.c6_mxc,
                campo_actual = EXCLUDED.campo_actual,
                estado = EXCLUDED.estado
        ''', (ahora, temps['50K'], temps['4K'], temps['STILL'], temps['MXC'], campo_act, estado))
        
        conn.commit()
        cur.close()
        conn.close()
        print(f"[{ahora.strftime('%H:%M:%S')}] Neon actualizado | Campo: {campo_act} T | Estado: {estado}")

    except Exception as e:
        print(f"Error en el ciclo del agente local: {e}")

    time.sleep(5)