import requests
import time
import psycopg2
import re
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

class MagnetDriver:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.tn = None

    def conectar(self):
        if self.tn:
            try:
                self.tn.close()
            except Exception:
                pass
        try:
            self.tn = Telnet(self.host, self.port, timeout=3)
            time.sleep(0.3)
            try:
                self.tn.read_eager() # Limpia el banner "American Magnetics 430..."
            except Exception:
                pass
            return True
        except Exception as e:
            print(f"🔴 Error Telnet: {e}")
            self.tn = None
            return False

    def consultar(self, cmd):
        if not self.tn and not self.conectar():
            return None
        try:
            self.tn.write(cmd.encode('ascii') + b"\n")
            time.sleep(0.2)
            # Leer todo lo disponible
            res = self.tn.read_very_eager().decode('ascii', errors='ignore').strip()
            return res
        except Exception:
            self.conectar()
            return None

    def enviar_rampa(self, target_b):
        if not self.tn and not self.conectar():
            return False
        try:
            comandos = [
                f'CONFigure:FIELD:TARGet {target_b}',
                'CONFigure:RAMP:RATE:FIELD 1,0.25,1',
                'RAMP'
            ]
            for cmd in comandos:
                self.tn.write(cmd.encode('ascii') + b"\n")
                time.sleep(0.2)
            return True
        except Exception:
            self.conectar()
            return False

driver_iman = MagnetDriver(MAGNET_IP, MAGNET_PORT)

def parsear_campo(texto_raw):
    if not texto_raw:
        return None
    
    # Extrae absolutamente todos los números del texto de respuesta
    numeros = re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", texto_raw)
    
    # Recorremos los números de atrás para adelante (el último es la medición real)
    for num_str in reversed(numeros):
        try:
            val = float(num_str)
            # Filtro físico: un imán estándar no supera los 10 T. Así ignoramos el '430'
            if abs(val) <= 10.0:
                return round(val, 4)
        except ValueError:
            continue
            
    return None

def leer_campo_actual():
    res = driver_iman.consultar('FIELD:MAGnet?')
    return parsear_campo(res)

def leer_canal(canal):
    try:
        req = requests.get(f"http://{CRIO_IP}:5001/channel/measurement/latest", timeout=2)
        data = req.json()
        if data.get('channel_nr') == canal:
            return data.get('temperature')
    except Exception:
        return None
    return None

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
    conn.commit()
    cur.close()
    conn.close()

init_db()
print("🚀 Agente local activo (Ignorando Banner 430)...")

temps = {'50K': None, '4K': None, 'STILL': None, 'MXC': None}

while True:
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute('SELECT campo_target FROM monitoreo_crio WHERE id = 1')
        row = cur.fetchone()
        
        if row and row[0] is not None:
            target_b = float(row[0])
            print(f"🚨 ORDEN TELEGRAM: Cambiar a {target_b} T")
            if abs(target_b) <= 7.0:
                if driver_iman.enviar_rampa(target_b):
                    cur.execute('UPDATE monitoreo_crio SET campo_target = NULL WHERE id = 1')
                    conn.commit()
                    print(f"✅ Rampa iniciada.")
            else:
                cur.execute('UPDATE monitoreo_crio SET campo_target = NULL WHERE id = 1')
                conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        pass

    for _ in range(3):
        for nombre, p in PLATOS.items():
            val = leer_canal(p['ch'])
            if val is not None:
                temps[nombre] = round(val * p['mult'], p['round'])
        time.sleep(0.4)

    campo_act = leer_campo_actual()

    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        tz_ar = timezone(timedelta(hours=-3))
        ahora = datetime.now(tz_ar)
        estado_conexion = "ONLINE" if any(v is not None for v in temps.values()) else "OFFLINE"

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
        ''', (ahora, temps['50K'], temps['4K'], temps['STILL'], temps['MXC'], campo_act, estado_conexion))

        conn.commit()
        cur.close()
        conn.close()
        print(f"[{ahora.strftime('%H:%M:%S')}] Campo: {campo_act} T | Status: {estado_conexion}")
    except Exception:
        pass

    time.sleep(2)