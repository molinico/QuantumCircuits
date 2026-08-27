import os
import telebot
import psycopg2
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta

TOKEN = os.getenv('TELEGRAM_TOKEN', '8822705364:AAFIiJ1rFs441HL3Drr08wfHSoeA2YoQYnc')
DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://neondb_owner:npg_eDJ9A0uvUitH@ep-restless-paper-aet1mqw8-pooler.c-2.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require')

bot = telebot.TeleBot(TOKEN, threaded=False)

ESTADO_MONITOREO = "uso"
# ⚠️ REEMPLAZA ESTO POR TU NÚMERO. Ej: CHAT_ID = 123456789
CHAT_ID = 2072390029  
UMBRAL_QUENCH_MK = 1500.0
ULTIMO_ESCALON_FRIO = 300

# --- 1. SERVIDOR HTTP PARA HEALTH CHECK EN RENDER ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Criostato Online")
    def log_message(self, format, *args):
        return

threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.getenv("PORT", 8080))), HealthCheckHandler).serve_forever(), daemon=True).start()

# --- 2. LECTURA Y CÁLCULO DE TIEMPO BLINDADO ---
def obtener_datos_neon():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute('SELECT fecha, c1_50k, c2_4k, c5_still, c6_mxc, estado, campo_actual, campo_target FROM monitoreo_crio WHERE id = 1')
        row = cur.fetchone()
        cur.close()
        conn.close()
        return row
    except Exception:
        return None

def calcular_segundos_atraso(fecha_db):
    if not fecha_db:
        return 999999
    # Render usa UTC. Restamos 3 horas exactas para empatar con la base de datos de Argentina
    ahora_ar = datetime.utcnow() - timedelta(hours=3)
    # Limpiamos cualquier zona horaria fantasma para hacer una resta matemática pura
    fecha_db_limpia = fecha_db.replace(tzinfo=None)
    
    segundos = (ahora_ar - fecha_db_limpia).total_seconds()
    # Si por desincronización de relojes da negativo, asumimos 0
    return max(0, segundos)

# --- 3. HILO DE VIGILANCIA Y ALERTAS (CORREGIDO) ---
def vigilancia_criostato():
    global ULTIMO_ESCALON_FRIO
    estaba_desconectado = False

    while True:
        try:
            if CHAT_ID and ESTADO_MONITOREO != "calentar":
                row = obtener_datos_neon()
                if row:
                    fecha, c50k, c4k, still, mxc, estado, campo_actual, campo_target = row
                    diferencia_seg = calcular_segundos_atraso(fecha)

                    # ALERTA DE CORTE (Más de 90 segundos sin datos)
                    if diferencia_seg > 90 or estado == "OFFLINE":
                        if not estaba_desconectado:
                            bot.send_message(CHAT_ID, f"🚨 *¡PÉRDIDA DE COMUNICACIÓN!* 🚨\nHace {int(diferencia_seg)} seg que la PC no responde. Posible corte de luz o red.", parse_mode="Markdown")
                            estaba_desconectado = True
                    else:
                        if estaba_desconectado:
                            bot.send_message(CHAT_ID, "🟢 *RECONECTADO:* Señal del laboratorio restablecida.", parse_mode="Markdown")
                            estaba_desconectado = False

                    # OTRAS ALERTAS
                    if ESTADO_MONITOREO == "uso" and mxc is not None and mxc > UMBRAL_QUENCH_MK:
                        bot.send_message(CHAT_ID, f"🔥 ¡EMERGENCIA CRIOSTATO! MXC subió a {mxc} mK")

                    elif ESTADO_MONITOREO == "enfriar" and c4k is not None:
                        escalon_actual = int(c4k / 10) * 10
                        if escalon_actual < ULTIMO_ESCALON_FRIO:
                            bot.send_message(CHAT_ID, f"❄️ Enfriando: 4K-FLANGE cruzó los {escalon_actual} K")
                            ULTIMO_ESCALON_FRIO = escalon_actual

        except Exception as e:
            print(f"Error en vigilancia: {e}")

        time.sleep(15)

threading.Thread(target=vigilancia_criostato, daemon=True).start()

# --- 4. COMANDOS TELEGRAM ---
@bot.message_handler(commands=['start'])
def start(message):
    global CHAT_ID
    CHAT_ID = message.chat.id
    bot.reply_to(message, f"🤖 Bot vinculado.\n⚠️ *Tu CHAT_ID es: {CHAT_ID}*\nPoné este número fijo en el código de Render para que las alertas no se borren si el servidor se reinicia.", parse_mode="Markdown")

@bot.message_handler(commands=['temp'])
def reporte_temperaturas(message):
    try:
        row = obtener_datos_neon()
        if not row:
            bot.reply_to(message, "❌ Base de datos vacía.")
            return

        fecha, c50k, c4k, still, mxc, estado, campo_actual, campo_target = row
        dif_seg = calcular_segundos_atraso(fecha)

        if dif_seg > 90 or estado == "OFFLINE":
            minutos = int(dif_seg / 60)
            status_str = f"🔴 *SIN COMUNICACIÓN* (Hace {minutos} min / {int(dif_seg)} seg)"
        else:
            status_str = "🟢 *ONLINE*"

        texto = f"🌡️ *Estado del Criostato*\nConexión: {status_str}\n\n"
        texto += f"• *50K-FLANGE*: {c50k if c50k is not None else 'N/I'} K\n"
        texto += f"• *4K-FLANGE*: {c4k if c4k is not None else 'N/I'} K\n"
        texto += f"• *STILL-FLANGE*: {still if still is not None else 'N/I'} mK\n"
        texto += f"• *MXC*: {mxc if mxc is not None else 'N/I'} mK\n\n"
        texto += f"🧲 *Campo Magnético*: {f'{campo_actual:.3f}' if campo_actual is not None else 'N/I'} T\n"
        
        bot.reply_to(message, texto, parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")

while True:
    try:
        bot.infinity_polling(timeout=10, long_polling_timeout=5)
    except Exception as e:
        time.sleep(5)