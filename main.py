import os
import telebot
import psycopg2
import time
import threading
from datetime import datetime, timezone, timedelta

TOKEN = os.getenv('TELEGRAM_TOKEN', '8822705364:AAFIiJ1rFs441HL3Drr08wfHSoeA2YoQYnc')
DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://neondb_owner:npg_eDJ9A0uvUitH@ep-restless-paper-aet1mqw8-pooler.c-2.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require')

bot = telebot.TeleBot(TOKEN, threaded=False)

ESTADO_MONITOREO = "calentar"
CHAT_ID = None
UMBRAL_QUENCH_MK = 1500.0
ULTIMO_ESCALON_FRIO = 300

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

def vigilancia_criostato():
    global ULTIMO_ESCALON_FRIO
    estaba_desconectado = False

    while True:
        if CHAT_ID and ESTADO_MONITOREO != "calentar":
            row = obtener_datos_neon()
            if row:
                fecha, c50k, c4k, still, mxc, estado, campo_actual, campo_target = row
                tz_ar = timezone(timedelta(hours=-3))
                ahora = datetime.now(tz_ar)
                diferencia_seg = (ahora - fecha.replace(tzinfo=tz_ar)).total_seconds()
                
                # 1. Detección de Caída de Comunicación (ALERTA REPETITIVA)
                if diferencia_seg > 180 or estado == "OFFLINE":
                    if not estaba_desconectado:
                        for _ in range(3):
                            bot.send_message(
                                CHAT_ID, 
                                "🚨 *¡ALERTA MÁXIMA: PÉRDIDA DE COMUNICACIÓN!* 🚨\nNo hay respuesta de la PC del laboratorio.", 
                                parse_mode="Markdown"
                            )
                            time.sleep(2)
                        estaba_desconectado = True
                    
                    elif ESTADO_MONITOREO == "uso":
                        for _ in range(3):
                            bot.send_message(
                                CHAT_ID, 
                                "🚨 *¡ALERTA: CONTINUAMOS SIN COMUNICACIÓN CON EL LAB!* 🚨", 
                                parse_mode="Markdown"
                            )
                            time.sleep(2)
                        time.sleep(120)  # Reitera cada 2 minutos
                        continue

                    time.sleep(15)
                    continue

                # 2. Reconexión
                if estaba_desconectado:
                    bot.send_message(CHAT_ID, "🟢 *RECONECTADO:* Se restableció la señal con el laboratorio.", parse_mode="Markdown")
                    estaba_desconectado = False

                # 3. Emergencia por Temperatura
                if ESTADO_MONITOREO == "uso" and mxc is not None and mxc > UMBRAL_QUENCH_MK:
                    for _ in range(3):
                        bot.send_message(CHAT_ID, f"🚨 ¡EMERGENCIA CRIOSTATO! MXC a {mxc} mK 🚨", parse_mode="Markdown")
                        time.sleep(2)

                # 4. Enfriamiento
                elif ESTADO_MONITOREO == "enfriar" and c4k is not None:
                    escalon_actual = int(c4k / 10) * 10
                    if escalon_actual < ULTIMO_ESCALON_FRIO:
                        bot.send_message(CHAT_ID, f"❄️ Enfriando: 4K-FLANGE cruzó los {escalon_actual} K")
                        ULTIMO_ESCALON_FRIO = escalon_actual

        time.sleep(15)

@bot.message_handler(commands=['start'])
def start(message):
    global CHAT_ID
    CHAT_ID = message.chat.id
    bot.reply_to(message, "🤖 Bot online en Render. Conectado al laboratorio.")

@bot.message_handler(commands=['help', 'ayuda'])
def mostrar_ayuda(message):
    texto = (
        "📖 *Guía de Comandos del Bot*\n\n"
        "• **/temp** — Temperaturas de los platos, estado del enlace y campo magnético actual.\n"
        "• **/campo <valor>** — Setea el campo magnético en el rango de -7.0 T a +7.0 T (|B| ≤ 7 T) con rampa fija de 0.25 T/min. Ejemplos: `/campo 2.5` o `/campo -1.0`\n"
        "• **/uso** — Habilita alarmas de emergencia por alta temperatura (> 1500 mK en MXC) y alertas repetitivas por corte de comunicación.\n"
        "• **/enfriar** — Alerta progresiva cada 10 K que baja el plato 4K-FLANGE.\n"
        "• **/calentar** — Silencia las alertas automáticas de monitoreo.\n"
        "• **/start** — Vincula este chat para recibir avisos."
    )
    bot.reply_to(message, texto, parse_mode="Markdown")

@bot.message_handler(commands=['temp'])
def reporte_temperaturas(message):
    row = obtener_datos_neon()
    if not row:
        bot.reply_to(message, "❌ No hay registros en la base de datos.")
        return

    fecha, c50k, c4k, still, mxc, estado, campo_actual, campo_target = row
    tz_ar = timezone(timedelta(hours=-3))
    ahora = datetime.now(tz_ar)
    diferencia_seg = (ahora - fecha.replace(tzinfo=tz_ar)).total_seconds()

    if diferencia_seg > 60 or estado == "OFFLINE":
        status_str = f"🔴 *SIN COMUNICACIÓN* (Hace {int(diferencia_seg/60)} min)"
    else:
        status_str = "🟢 *ONLINE*"

    texto = f"🌡️ *Estado del Criostato*\nConexión: {status_str}\n\n"
    texto += f"• *50K-FLANGE*: {c50k if c50k is not None else 'N/I'} K\n"
    texto += f"• *4K-FLANGE*: {c4k if c4k is not None else 'N/I'} K\n"
    texto += f"• *STILL-FLANGE*: {still if still is not None else 'N/I'} mK\n"
    texto += f"• *MXC*: {mxc if mxc is not None else 'N/I'} mK\n\n"
    texto += f"🧲 *Campo Magnético*: {f'{campo_actual:.3f}' if campo_actual is not None else 'N/I'} T\n"
    if campo_target is not None:
        texto += f"⏳ *Cambiando a*: {campo_target} T (Rampa en progreso)\n"
    
    bot.reply_to(message, texto, parse_mode="Markdown")

@bot.message_handler(commands=['campo'])
def set_campo(message):
    try:
        partes = message.text.split()
        if len(partes) < 2:
            bot.reply_to(message, "⚠️ Indicá el valor del campo en Teslas. Ejemplo: `/campo 2.5` o `/campo -1.5`", parse_mode="Markdown")
            return
        
        target_b = float(partes[1])
        
        # Validar que el módulo del campo sea <= 7 Teslas (-7.0 <= B <= 7.0)
        if abs(target_b) > 7.0:
            bot.reply_to(message, "❌ El módulo del campo magnético debe ser **mínimo 0 T y máximo 7 T** (Rango permitido: `-7.0 T` a `+7.0 T`).", parse_mode="Markdown")
            return

        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute('UPDATE monitoreo_crio SET campo_target = %s WHERE id = 1', (target_b,))
        conn.commit()
        cur.close()
        conn.close()

        bot.reply_to(message, f"⚙️ *Orden registrada:* Fijando campo en *{target_b} T* (Rampa fija: 0.25 T/min).", parse_mode="Markdown")

    except ValueError:
        bot.reply_to(message, "❌ Valor número inválido. Ejemplo: `/campo 1.5`", parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, f"❌ Error al registrar la orden: {e}")

@bot.message_handler(commands=['uso'])
def modo_uso(message):
    global ESTADO_MONITOREO
    ESTADO_MONITOREO = "uso"
    bot.reply_to(message, "✅ Modo USO activado. Alarmas de emergencia y comunicación habilitadas.")

@bot.message_handler(commands=['enfriar'])
def modo_enfriar(message):
    global ESTADO_MONITOREO, ULTIMO_ESCALON_FRIO
    ESTADO_MONITOREO = "enfriar"
    row = obtener_datos_neon()
    if row and row[2]:
        ULTIMO_ESCALON_FRIO = int(row[2] / 10) * 10
    bot.reply_to(message, "❄️ Modo ENFRIAMIENTO activado.")

@bot.message_handler(commands=['calentar'])
def modo_calentar(message):
    global ESTADO_MONITOREO
    ESTADO_MONITOREO = "calentar"
    bot.reply_to(message, "🔥 Modo CALENTAMIENTO. Alertas silenciadas.")

hilo = threading.Thread(target=vigilancia_criostato, daemon=True)
hilo.start()

bot.infinity_polling()