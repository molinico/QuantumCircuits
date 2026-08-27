import requests
import time
import psycopg2
from datetime import datetime, timezone, timedelta

# Pegá tu URL de Neon
DATABASE_URL = 'postgresql://neondb_owner:npg_eDJ9A0uvUitH@ep-restless-paper-aet1mqw8-pooler.c-2.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require'

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
            estado TEXT,
            CONSTRAINT single_row CHECK (id = 1)
        )
    ''')
    conn.commit()
    cur.close()
    conn.close()

def leer_canal(canal):
    try:
        req = requests.get("http://192.168.1.1:5001/channel/measurement/latest", timeout=3)
        data = req.json()
        if data.get('channel_nr') == canal:
            return data.get('temperature')
    except:
        return None
    return None

init_db()
print("🚀 Agente local del criostato iniciado...")

temps = {'50K': None, '4K': None, 'STILL': None, 'MXC': None}

while True:
    for _ in range(10):
        for nombre, p in PLATOS.items():
            val = leer_canal(p['ch'])
            if val is not None:
                temps[nombre] = round(val * p['mult'], p['round'])
        time.sleep(1)

    tz_ar = timezone(timedelta(hours=-3))
    ahora = datetime.now(tz_ar)
    estado = "ONLINE" if any(v is not None for v in temps.values()) else "OFFLINE"

    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO monitoreo_crio (id, fecha, c1_50k, c2_4k, c5_still, c6_mxc, estado)
            VALUES (1, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                fecha = EXCLUDED.fecha,
                c1_50k = EXCLUDED.c1_50k,
                c2_4k = EXCLUDED.c2_4k,
                c5_still = EXCLUDED.c5_still,
                c6_mxc = EXCLUDED.c6_mxc,
                estado = EXCLUDED.estado
        ''', (ahora, temps['50K'], temps['4K'], temps['STILL'], temps['MXC'], estado))
        conn.commit()
        cur.close()
        conn.close()
        print(f"[{ahora.strftime('%H:%M:%S')}] Actualizado en Neon. Estado: {estado}")
    except Exception as e:
        print(f"Error al subir a Neon: {e}")

    time.sleep(5)