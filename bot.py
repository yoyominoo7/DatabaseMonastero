import asyncio
import logging
import os
import random
from typing import Optional, Tuple, List

import psycopg
from psycopg.rows import dict_row
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
GEN_GET_NICK = 1
CHECK_GET_CODE = 2
MOD_MENSA_NICK, MOD_MENSA_QTY, MOD_MENSA_CONFIRM = range(300, 303)
# Env vars
BOT_TOKEN = os.environ["BOT_TOKEN"]
WEBHOOK_URL = os.environ["WEBHOOK_URL"]
PORT = int(os.environ.get("PORT", "10000"))
DATABASE_URL = os.environ["DATABASE_URL"]
DIRECTION_CHAT_ID = int(os.environ["DIRECTION_CHAT_ID"])

INITIATES_IDS = {
    int(x.strip())
    for x in os.environ.get("INITIATES_IDS", "").split(",")
    if x.strip().isdigit()
}
HEREMITS_IDS = {
    int(x.strip())
    for x in os.environ.get("HEREMITS_IDS", "").split(",")
    if x.strip().isdigit()
}


# ---------- DB helpers ----------

def get_conn():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def ensure_tables():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS codes (
                id          SERIAL PRIMARY KEY,
                code        VARCHAR(4) UNIQUE NOT NULL,
                owner       TEXT NOT NULL,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                created_by  BIGINT NOT NULL,
                active      BOOLEAN NOT NULL DEFAULT TRUE
            );
            """
        )
        conn.commit()


def db_get_code(code: str) -> Optional[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM codes WHERE code = %s;", (code,))
        row = cur.fetchone()
        return row


def db_insert_code(code: str, owner: str, created_by: int) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO codes (code, owner, created_by)
            VALUES (%s, %s, %s)
            RETURNING *;
            """,
            (code, owner, created_by),
        )
        row = cur.fetchone()
        conn.commit()
        return row


def db_extinguish_code(code: str) -> Optional[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE codes
            SET active = FALSE
            WHERE code = %s AND active = TRUE
            RETURNING *;
            """,
            (code,),
        )
        row = cur.fetchone()
        conn.commit()
        return row


def generate_unique_code() -> str:
    # 4 cifre, assicurandosi che non esista già
    while True:
        code = f"{random.randint(0, 9999):04d}"
        if db_get_code(code) is None:
            return code


# ---------- Ruoli ----------

def get_role(user_id: int) -> Optional[str]:
    if user_id in HEREMITS_IDS:
        return "hermit"
    if user_id in INITIATES_IDS:
        return "initiate"
    return None


async def ensure_authorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    user = update.effective_user
    role = get_role(user.id)
    if role is None:
        await update.effective_message.reply_text(
            "Non sei autorizzato a utilizzare questo bot."
        )
        return None
    return role


# ---------- /start ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    role = get_role(user.id)

    if role == "hermit":
        await update.message.reply_text(
            "Benvenuto, eremita.\n\n"
            "Puoi usare:\n"
            "/generacodice – genera un nuovo codice per un player\n"
            "/controllacodice – controlla o estingui un codice esistente"
        )
    elif role == "initiate":
        await update.message.reply_text(
            "Benvenuto, iniziato.\n\n"
            "Per ora non hai comandi disponibili, ma sei parte del monastero."
        )
    else:
        await update.message.reply_text(
            "Non sei autorizzato a utilizzare questo bot."
        )


# ---------- /generacodice ----------

async def generacodice_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    role = await ensure_authorized(update, context)
    if role != "hermit":
        return ConversationHandler.END

    # Genera codice univoco
    code = await asyncio.to_thread(generate_unique_code)

    context.user_data["gen_code"] = code
    context.user_data["gen_messages_to_delete"] = []

    msg = await update.message.reply_text(
        f"Ho generato il codice: {code}\n\n"
        "Ora inviami il nickname del player a cui intestarlo."
    )
    context.user_data["gen_messages_to_delete"].append(msg.message_id)
    context.user_data["gen_messages_to_delete"].append(update.message.message_id)

    return GEN_GET_NICK


async def generacodice_get_nick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    role = get_role(update.effective_user.id)
    if role != "hermit":
        await update.message.reply_text("Non sei autorizzato.")
        return ConversationHandler.END

    nick = update.message.text.strip()
    code = context.user_data.get("gen_code")

    if not code:
        await update.message.reply_text("Qualcosa è andato storto, riprova /generacodice.")
        return ConversationHandler.END

    context.user_data["gen_owner"] = nick
    context.user_data["gen_messages_to_delete"].append(update.message.message_id)

    # Cancella tutti i messaggi del processo
    chat_id = update.effective_chat.id
    for mid in context.user_data.get("gen_messages_to_delete", []):
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=mid)
        except Exception as e:
            logger.warning("Impossibile cancellare messaggio %s: %s", mid, e)

    # Invia resoconto con bottoni
    text = (
        "Riepilogo generazione codice:\n\n"
        f"ID: (verrà assegnato alla conferma)\n"
        f"Player: {nick}\n"
        f"Codice: {code}\n"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Conferma", callback_data="gen_confirm"),
                InlineKeyboardButton("❌ Annulla", callback_data="gen_cancel"),
            ]
        ]
    )
    msg = await update.effective_chat.send_message(text, reply_markup=keyboard)
    context.user_data["gen_summary_message_id"] = msg.message_id

    return ConversationHandler.END


async def generacodice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    user = query.from_user
    role = get_role(user.id)
    if role != "hermit":
        await query.edit_message_text("Non sei autorizzato.")
        return

    data = query.data
    code = context.user_data.get("gen_code")
    owner = context.user_data.get("gen_owner")

    if data == "gen_cancel":
        await query.edit_message_text("Richiesta annullata.")
        context.user_data.pop("gen_code", None)
        context.user_data.pop("gen_owner", None)
        return

    if data == "gen_confirm":
        if not code or not owner:
            await query.edit_message_text("Dati mancanti, riprova /generacodice.")
            return

        # Salva su DB (controllando ancora unicità)
        existing = await asyncio.to_thread(db_get_code, code)
        if existing is not None:
            await query.edit_message_text(
                "Il codice generato esiste già. Riprova /generacodice."
            )
            context.user_data.pop("gen_code", None)
            context.user_data.pop("gen_owner", None)
            return

        row = await asyncio.to_thread(db_insert_code, code, owner, user.id)

        # Messaggio finale all'eremita
        text = (
            "Codice creato con successo!\n\n"
            f"ID: {row['id']}\n"
            f"Player: {row['owner']}\n"
            f"Codice: {row['code']}\n"
            f"Creato alle: {row['created_at']}\n"
        )
        await query.edit_message_text(text)

        # Notifica al gruppo direzione
        dir_text = (
            "📜 Nuovo codice generato\n\n"
            f"ID: {row['id']}\n"
            f"Player: {row['owner']}\n"
            f"Codice: {row['code']}\n"
            f"Creato da: {user.full_name} (id {user.id})\n"
            f"Orario: {row['created_at']}\n"
        )
        try:
            await context.bot.send_message(chat_id=DIRECTION_CHAT_ID, text=dir_text)
        except Exception as e:
            logger.error("Errore invio messaggio direzione: %s", e)

        # Pulisci dati temporanei
        context.user_data.pop("gen_code", None)
        context.user_data.pop("gen_owner", None)


# ---------- /controllacodice ----------

async def controllacodice_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    role = await ensure_authorized(update, context)
    if role != "hermit":
        return ConversationHandler.END

    msg = await update.message.reply_text(
        "Inserisci il codice (4 cifre) da verificare."
    )
    context.user_data["check_prompt_message_id"] = msg.message_id
    context.user_data["check_user_command_message_id"] = update.message.message_id
    return CHECK_GET_CODE


async def controllacodice_get_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    role = get_role(user.id)
    if role != "hermit":
        await update.message.reply_text("Non sei autorizzato.")
        return ConversationHandler.END

    code = update.message.text.strip()
    chat_id = update.effective_chat.id

    # Elimina il messaggio dell'utente
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
    except Exception as e:
        logger.warning("Impossibile cancellare messaggio utente: %s", e)

    # Recupera info codice
    row = await asyncio.to_thread(db_get_code, code)

    if row is None:
        text = f"Codice {code} non trovato o non attivo."
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Chiudi", callback_data="check_close")]]
        )
        # Modifica l'ultimo messaggio del bot
        prompt_id = context.user_data.get("check_prompt_message_id")
        if prompt_id:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=prompt_id,
                text=text,
                reply_markup=keyboard,
            )
        else:
            await update.effective_chat.send_message(text, reply_markup=keyboard)
        return ConversationHandler.END

    status = "ATTIVO" if row["active"] else "ESTINTO"
    text = (
        f"Dettagli codice {code}:\n\n"
        f"ID: {row['id']}\n"
        f"Player: {row['owner']}\n"
        f"Creato alle: {row['created_at']}\n"
        f"Stato: {status}\n"
    )

    if row["active"]:
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🔥 Estingui codice", callback_data=f"extinguish:{code}"
                    ),
                    InlineKeyboardButton("Annulla", callback_data="check_close"),
                ]
            ]
        )
    else:
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Chiudi", callback_data="check_close")]]
        )

    prompt_id = context.user_data.get("check_prompt_message_id")
    if prompt_id:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=prompt_id,
            text=text,
            reply_markup=keyboard,
        )
    else:
        msg = await update.effective_chat.send_message(text, reply_markup=keyboard)
        context.user_data["check_prompt_message_id"] = msg.message_id

    context.user_data["check_code"] = code
    return ConversationHandler.END


async def controllacodice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    user = query.from_user
    role = get_role(user.id)
    if role != "hermit":
        await query.edit_message_text("Non sei autorizzato.")
        return

    data = query.data

    if data == "check_close":
        await query.edit_message_text("Operazione conclusa.")
        context.user_data.pop("check_code", None)
        return

    if data.startswith("extinguish:"):
        code = data.split(":", 1)[1]
        # Chiedi conferma
        text = f"Sei sicuro di voler estinguere il codice {code}?"
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Conferma estinzione", callback_data=f"extinguish_confirm:{code}"
                    ),
                    InlineKeyboardButton("❌ Annulla", callback_data="check_close"),
                ]
            ]
        )
        await query.edit_message_text(text, reply_markup=keyboard)
        return

    if data.startswith("extinguish_confirm:"):
        code = data.split(":", 1)[1]
        row = await asyncio.to_thread(db_extinguish_code, code)
        if row is None:
            await query.edit_message_text(
                f"Il codice {code} non è attivo o non esiste più."
            )
            return

        text = (
            f"Codice {code} estinto con successo.\n\n"
            f"ID: {row['id']}\n"
            f"Player: {row['owner']}\n"
            f"Creato alle: {row['created_at']}\n"
            f"Stato: ESTINTO\n"
        )
        await query.edit_message_text(text)

        # Notifica al gruppo direzione
        dir_text = (
            "⚠️ Codice estinto\n\n"
            f"ID: {row['id']}\n"
            f"Player: {row['owner']}\n"
            f"Codice: {row['code']}\n"
            f"Estinto da: {user.full_name} (id {user.id})\n"
        )
        try:
            await context.bot.send_message(chat_id=DIRECTION_CHAT_ID, text=dir_text)
        except Exception as e:
            logger.error("Errore invio messaggio direzione: %s", e)

        context.user_data.pop("check_code", None)

async def modulomensa_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("Inserisci il nickname del fedele:")
    context.user_data["mod_mensa_msg"] = msg  # messaggio da modificare
    return MOD_MENSA_NICK

async def modulomensa_get_nick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nick = update.message.text.strip()
    context.user_data["mod_mensa_nick"] = nick

    # elimina messaggio utente
    await update.message.delete()

    # modifica messaggio bot
    msg = context.user_data["mod_mensa_msg"]
    await msg.edit_text("Inserisci la quantità di cibo distribuita:")

    return MOD_MENSA_QTY

async def modulomensa_get_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    qty = update.message.text.strip()
    context.user_data["mod_mensa_qty"] = qty

    await update.message.delete()

    nick = context.user_data["mod_mensa_nick"]

    msg = context.user_data["mod_mensa_msg"]
    await msg.edit_text(
        f"**Riepilogo modulo mensa:**\n"
        f"- Fedele: `{nick}`\n"
        f"- Quantità: `{qty}`\n\n"
        "Confermi la registrazione?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Conferma", callback_data="mensa_confirm")],
            [InlineKeyboardButton("Annulla", callback_data="mensa_cancel")]
        ])
    )

    return MOD_MENSA_CONFIRM

async def modulomensa_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "mensa_cancel":
        await query.edit_message_text("Registrazione annullata.")
        return ConversationHandler.END

    if query.data == "mensa_confirm":
        nick = context.user_data["mod_mensa_nick"]
        qty = context.user_data["mod_mensa_qty"]

        # salva nel database
        save_mensa_record(nick, qty)

        # invia nel gruppo direzione
        await context.bot.send_message(
            chat_id=ID_GRUPPO_DIREZIONE,
            text=(
                "📜 *Nuova registrazione mensa*\n"
                f"- Fedele: `{nick}`\n"
                f"- Quantità: `{qty}`\n"
                f"- Data: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
            ),
            parse_mode="Markdown"
        )

        await query.edit_message_text("Modulo mensa registrato con successo.")
        return ConversationHandler.END

def save_mensa_record(nick, qty):
    conn = psycopg.connect(DB_URL)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO mensa (nickname, quantita, data) VALUES (%s, %s, NOW())",
        (nick, qty)
    )
    conn.commit()
    cur.close()
    conn.close()

# ---------- main / webhook ----------

def main() -> None:
    ensure_tables()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # Handlers
    application.add_handler(CommandHandler("start", start))

    gen_conv = ConversationHandler(
        entry_points=[CommandHandler("generacodice", generacodice_entry)],
        states={
            GEN_GET_NICK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, generacodice_get_nick)
            ],
        },
        fallbacks=[],
    )
    application.add_handler(gen_conv)
    application.add_handler(CallbackQueryHandler(generacodice_callback, pattern="^gen_"))

    check_conv = ConversationHandler(
        entry_points=[CommandHandler("controllacodice", controllacodice_entry)],
        states={
            CHECK_GET_CODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, controllacodice_get_code)
            ],
        },
        fallbacks=[],
    )
    application.add_handler(check_conv)
    application.add_handler(
        CallbackQueryHandler(
            controllacodice_callback,
            pattern="^(check_close|extinguish:|extinguish_confirm:)"
        )
    )

    # --- MODULO MENSA ---
    mensa_conv = ConversationHandler(
        entry_points=[CommandHandler("modulomensa", modulomensa_entry)],
        states={
            MOD_MENSA_NICK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, modulomensa_get_nick)
            ],
            MOD_MENSA_QTY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, modulomensa_get_qty)
            ],
            MOD_MENSA_CONFIRM: [
                CallbackQueryHandler(modulomensa_callback, pattern="^mensa_")
            ],
        },
        fallbacks=[],
    )
    application.add_handler(mensa_conv)

    # --- WEBHOOK ---
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=BOT_TOKEN,
        webhook_url=f"https://databasemonastero.onrender.com/{BOT_TOKEN}",
    )


if __name__ == "__main__":
    main()
