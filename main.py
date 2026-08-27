import os
import telebot
import psycopg2
import time
import threading
from datetime import datetime, timezone, timedelta

# Reemplazá con tu TOKEN de Telegram y tu URL de Neon
TOKEN = os.getenv('TELEGRAM_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')
bot = telebot.TeleBot(TOKEN, threaded=False)

ESTADO_MONITOREO = "calentar"
CHAT_ID = None
UMBRAL_QUENCH_MK = 1500.0
ULTIMO_ESCALON_FRIO = 300

def obtener_datos_neon():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute('SELECT fecha, c1_50k, c2_4k, c5_still, c6_mxc, estado FROM monitoreo_crio WHERE id = 1')
        row = cur.fetchone()
        cur.close()
        conn.close()
        return row
    except:
        return None

def vigilancia_criostato():
    global ULTIMO_ESCALON_FRIO
    estaba_desconectado = False  # Rastrea si venimos de una caída

    while True:
        if CHAT_ID and ESTADO_MONITOREO != "calentar":
            row = obtener_datos_neon()
            if row:
                fecha, c50k, c4k, still, mxc, estado = row
                tz_ar = timezone(timedelta(hours=-3))
                ahora = datetime.now(tz_ar)
                diferencia_seg = (ahora - fecha.replace(tzinfo=tz_ar)).total_seconds()
                
                # 1. Detección de Caída
                if diferencia_seg > 180 or estado == "OFFLINE":
                    if not estaba_desconectado:
                        bot.send_message(CHAT_ID, "⚠️ *ALERTA:* Pérdida de comunicación con la PC del laboratorio (Posible corte de luz o red).", parse_mode="Markdown")
                        estaba_desconectado = True
                    time.sleep(15)
                    continue

                # 2. Detección de Reconexión
                if estaba_desconectado:
                    bot.send_message(CHAT_ID, "🟢 *RECONECTADO:* Se restableció la señal con la PC del laboratorio. El monitoreo sigue activo.", parse_mode="Markdown")
                    estaba_desconectado = False

                # 3. Monitoreo de Emergencia / Enfriamiento
                if ESTADO_MONITOREO == "uso" and mxc is not None and mxc > UMBRAL_QUENCH_MK:
                    for _ in range(3):
                        bot.send_message(CHAT_ID, f"🚨 ¡EMERGENCIA CRIOSTATO! MXC a {mxc} mK 🚨")
                        time.sleep(2)

                elif ESTADO_MONITOREO == "enfriar" and c4k is not None:
                    escalon_actual = int(c4k / 10) * 10
                    if escalon_actual < ULTIMO_ESCALON_FRIO:
                        bot.send_message(CHAT_ID, f"❄️ Enfriando: 4K-FLANGE cruzó los {escalon_actual} K")
                        ULTIMO_ESCALON_FRIO = escalon_actual

        time.sleep(15)@bot.message_handler(commands=['start'])


def start(message):
    global CHAT_ID
    CHAT_ID = message.chat.id
    bot.reply_to(message, "🤖 Bot online en Render. Conectado a la base de datos del laboratorio.")

@bot.message_handler(commands=['help', 'ayuda'])
def mostrar_ayuda(message):
    texto = (
        "📖 *Guía de Comandos del Bot*\n\n"
        "• **/temp** — Muestra las temperaturas guardadas en la nube.\n"
        "• **/uso** — Habilita alarmas de emergencia (> 4 K en MXC).\n"
        "• **/enfriar** — Manda alerta cada 10 K que baja el plato 4K-FLANGE.\n"
        "• **/calentar** — Silencia las alertas automáticas.\n"
        "• **/start** — Vincula este chat para recibir alertas."
    )
    bot.reply_to(message, texto, parse_mode="Markdown")

@bot.message_handler(commands=['temp'])
def reporte_temperaturas(message):
    row = obtener_datos_neon()
    if not row:
        bot.reply_to(message, "❌ No hay registros en la base de datos.")
        return

    fecha, c50k, c4k, still, mxc, estado = row
    tz_ar = timezone(timedelta(hours=-3))
    ahora = datetime.now(tz_ar)
    diferencia_seg = (ahora - fecha.replace(tzinfo=tz_ar)).total_seconds()

    if diferencia_seg > 180 or estado == "OFFLINE":
        status_str = f"🔴 *SIN COMUNICACIÓN* (Hace {int(diferencia_seg/60)} min)"
    else:
        status_str = "🟢 *ONLINE*"

    texto = f"🌡️ *Temperaturas del Criostato*\nEstado: {status_str}\n\n"
    texto += f"• *50K-FLANGE*: {c50k if c50k else 'N/I'} K\n"
    texto += f"• *4K-FLANGE*: {c4k if c4k else 'N/I'} K\n"
    texto += f"• *STILL-FLANGE*: {still if still else 'N/I'} mK\n"
    texto += f"• *MXC*: {mxc if mxc else 'N/I'} mK\n"
    
    bot.reply_to(message, texto, parse_mode="Markdown")

@bot.message_handler(commands=['uso'])
def modo_uso(message):
    global ESTADO_MONITOREO
    ESTADO_MONITOREO = "uso"
    bot.reply_to(message, "✅ Modo USO activado. Monitoreo de emergencia habilitado.")

@bot.message_handler(commands=['enfriar'])
def modo_enfriar(message):
    global ESTADO_MONITOREO, ULTIMO_ESCALON_FRIO
    ESTADO_MONITOREO = "enfriar"
    row = obtener_datos_neon()
    if row and row[2]:
        ULTIMO_ESCALON_FRIO = int(row[2] / 10) * 10
    bot.reply_to(message, "❄️ Modo ENFRIAMIENTO activado (guiado por 4K-FLANGE).")

@bot.message_handler(commands=['calentar'])
def modo_calentar(message):
    global ESTADO_MONITOREO
    ESTADO_MONITOREO = "calentar"
    bot.reply_to(message, "🔥 Modo CALENTAMIENTO. Alertas silenciadas.")

hilo = threading.Thread(target=vigilancia_criostato, daemon=True)
hilo.start()

bot.infinity_polling()