import requests
import time
import psycopg2
from telnetlib import Telnet
from datetime import datetime, timezone, timedelta

DATABASE_URL = 'postgresql://neondb_owner:npg_eDJ9A0uvUitH@ep-restless-paper-aet1mqw8-pooler.c-2.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require'

MAGNET_IP = '192.168.1.27'
MAGNET_PORT = 7180

CRIO_IP = '192.168.1.1'

PLATOS = {
    '50K':  {'ch': 1, 'mult': 1.0,  'round': 3},
    '4K':   {'ch': 2, 'mult': 1.0,  'round': 4},
    'STILL':{'ch': 5, 'mult': 1e3,  'round': 2},
    'MXC':  {'ch': 6, 'mult': 1e3,  'round': 2}
}

def consulta(tn, code):
    tn.write(code.encode('ascii') + b"\n")
    return tn.read_until(b"\n").decode('ascii').strip()

def orden(tn, code):
    tn.write(code.encode('ascii') + b"\n")

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
    cur.execute('ALTER TABLE monitoreo_crio ADD COLUMN IF NOT EXISTS campo_actual REAL;')
    cur.execute('ALTER TABLE monitoreo_crio ADD COLUMN IF NOT EXISTS campo_target REAL;')
    conn.commit()
    cur.close()
    conn.close()

def leer_campo_magnetico():
    try:
        tn = Telnet(MAGNET_IP, MAGNET_PORT, timeout=3)
        res = consulta(tn, 'FIELD:MAGnet?')
        tn.close()
        return float(res)
    except Exception as e:
        print(f"Error al leer campo desde {MAGNET_IP}: {e}")
        return None

def ejecutar_cambio_campo(target_b):
    if abs(target_b) <= 7.0:
        try:
            print(f"⚙️ Aplicando rampa hacia {target_b} T (Rate: 0.25 T/min)...")
            tn = Telnet(MAGNET_IP, MAGNET_PORT, timeout=5)
            orden(tn, f'CONFigure:FIELD:TARGet {target_b}')
            orden(tn, 'CONFigure:RAMP:RATE:FIELD 1,0.25,1') # Rate fijo a 0.25 T/min
            orden(tn, 'RAMP')
            tn.close()
            return True
        except Exception as e:
            print(f"Error al enviar comandos Telnet: {e}")
            return False
    return False

def leer_canal(canal):
    try:
        req = requests.get(f"http://{CRIO_IP}:5001/channel/measurement/latest", timeout=3)
        data = req.json()
        if data.get('channel_nr') == canal:
            return data.get('temperature')
    except Exception:
        return None
    return None

init_db()
print("🚀 Agente local activo (Conectado a Neon e Imán 192.168.1.27)...")

temps = {'50K': None, '4K': None, 'STILL': None, 'MXC': None}

while True:
    # 1. Lectura de temperaturas
    for _ in range(5):
        for nombre, p in PLATOS.items():
            val = leer_canal(p['ch'])
            if val is not None:
                temps[nombre] = round(val * p['mult'], p['round'])
        time.sleep(1)

    # 2. Lectura del campo magnético
    campo_act = leer_campo_magnetico()

    # 3. Verificación de órdenes desde Telegram y actualización en Neon
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute('SELECT campo_target FROM monitoreo_crio WHERE id = 1')
        row = cur.fetchone()
        
        if row and row[0] is not None:
            target_b = float(row[0])
            if ejecutar_cambio_campo(target_b):
                cur.execute('UPDATE monitoreo_crio SET campo_target = NULL WHERE id = 1')
                conn.commit()

        tz_ar = timezone(timedelta(hours=-3))
        ahora = datetime.now(tz_ar)
        estado = "ONLINE" if any(v is not None for v in temps.values()) else "OFFLINE"

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
        print(f"[{ahora.strftime('%H:%M:%S')}] Sincronizado | Campo: {campo_act} T | Status: {estado}")

    except Exception as e:
        print(f"Error en sincronización con Neon: {e}")

    time.sleep(3)