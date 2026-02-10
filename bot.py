import asyncio
import logging
import os
import random
from typing import Optional, Tuple, List
from datetime import datetime
import html
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
            "<b>𝐂𝐔𝐋𝐓𝐎 𝐃𝐈 𝐏𝐎𝐒𝐄𝐈𝐃𝐎𝐍𝐄</b> ⚓️\n\n"
            "🌊 Benvenuto, eremita.\n"
            "La tua presenza è riconosciuta dal Monastero.\n\n"
            "Puoi utilizzare i seguenti strumenti sacri:\n"
            "• /generacodice – <i>Genera un nuovo codice per un fedele</i>\n"
            "• /controllacodice – <i>Controlla o estingui un codice esistente</i>"
            "• /modulomensa –<i>Inizia la registrazione di un modulo mensa</i>",
            parse_mode="HTML"
        )

    elif role == "initiate":
        await update.message.reply_text(
            "<b>𝐂𝐔𝐋𝐓𝐎 𝐃𝐈 𝐏𝐎𝐒𝐄𝐈𝐃𝐎𝐍𝐄</b> ⚓️\n\n"
            "🌊 Benvenuto, iniziato.\n"
            "Il Monastero riconosce la tua appartenenza.\n\n"
            "Al momento non hai comandi disponibili, ma la tua presenza è preziosa.",
            parse_mode="HTML"
        )

    else:
        await update.message.reply_text(
            "<b>𝐂𝐔𝐋𝐓𝐎 𝐃𝐈 𝐏𝐎𝐒𝐄𝐈𝐃𝐎𝐍𝐄</b> ⚓️\n\n"
            "⛔ Non sei autorizzato a utilizzare questo bot.",
            parse_mode="HTML"
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
        "<b>𝐂𝐔𝐋𝐓𝐎 𝐃𝐈 𝐏𝐎𝐒𝐄𝐈𝐃𝐎𝐍𝐄</b> ⚓️\n\n"
        f"🔱 Ho generato il codice sacro: <b>{code}</b>\n\n"
        "Ora inviami il <b>nickname del fedele</b> a cui deve essere assegnato.",
        parse_mode="HTML"
    )
    context.user_data["gen_messages_to_delete"].append(msg.message_id)
    context.user_data["gen_messages_to_delete"].append(update.message.message_id)

    return GEN_GET_NICK


async def generacodice_get_nick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    role = get_role(update.effective_user.id)
    if role != "hermit":
        await update.message.reply_text(
            "<b>𝐂𝐔𝐋𝐓𝐎 𝐃𝐈 𝐏𝐎𝐒𝐄𝐈𝐃𝐎𝐍𝐄</b> ⚓️\n\n"
            "⛔ Non sei autorizzato a compiere questo rito.",
            parse_mode="HTML"
        )
        return ConversationHandler.END

    nick = update.message.text.strip()
    code = context.user_data.get("gen_code")

    if not code:
        await update.message.reply_text(
            "<b>𝐂𝐔𝐋𝐓𝐎 𝐃𝐈 𝐏𝐎𝐒𝐄𝐈𝐃𝐎𝐍𝐄</b> ⚓️\n\n"
            "⚠️ Qualcosa è andato storto.\n"
            "Riprova il rito con /generacodice.",
            parse_mode="HTML"
        )
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
        "<b>𝐂𝐔𝐋𝐓𝐎 𝐃𝐈 𝐏𝐎𝐒𝐄𝐈𝐃𝐎𝐍𝐄</b> ⚓️\n\n"
        "📋 <b>Riepilogo generazione codice</b>\n\n"
        "Controlla le informazioni prima di procedere:\n\n"
        f"• 🆔 ID: <i>verrà assegnato alla conferma</i>\n"
        f"• 👤 Player: <b>{nick}</b>\n"
        f"• 🔐 Codice: <b>{code}</b>"
    )

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Conferma", callback_data="gen_confirm"),
                InlineKeyboardButton("❌ Annulla", callback_data="gen_cancel"),
            ]
        ]
    )

    msg = await update.effective_chat.send_message(text, reply_markup=keyboard, parse_mode="HTML")
    context.user_data["gen_summary_message_id"] = msg.message_id

    return ConversationHandler.END


async def generacodice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    user = query.from_user
    role = get_role(user.id)
    if role != "hermit":
        await query.edit_message_text(
            "<b>𝐂𝐔𝐋𝐓𝐎 𝐃𝐈 𝐏𝐎𝐒𝐄𝐈𝐃𝐎𝐍𝐄</b> ⚓️\n\n"
            "⛔ Non sei autorizzato.",
            parse_mode="HTML"
        )
        return

    data = query.data
    code = context.user_data.get("gen_code")
    owner = context.user_data.get("gen_owner")

    if data == "gen_cancel":
        await query.edit_message_text(
            "<b>𝐂𝐔𝐋𝐓𝐎 𝐃𝐈 𝐏𝐎𝐒𝐄𝐈𝐃𝐎𝐍𝐄</b> ⚓️\n\n"
            "❌ Richiesta annullata.",
            parse_mode="HTML"
        )
        context.user_data.pop("gen_code", None)
        context.user_data.pop("gen_owner", None)
        return

    if data == "gen_confirm":
        if not code or not owner:
            await query.edit_message_text(
                "<b>𝐂𝐔𝐋𝐓𝐎 𝐃𝐈 𝐏𝐎𝐒𝐄𝐈𝐃𝐎𝐍𝐄</b> ⚓️\n\n"
                "⚠️ Dati mancanti.\n"
                "Riprova il rito con /generacodice.",
                parse_mode="HTML"
            )
            return

        # Salva su DB (controllando ancora unicità)
        existing = await asyncio.to_thread(db_get_code, code)
        if existing is not None:
            await query.edit_message_text(
                "<b>𝐂𝐔𝐋𝐓𝐎 𝐃𝐈 𝐏𝐎𝐒𝐄𝐈𝐃𝐎𝐍𝐄</b> ⚓️\n\n"
                "⚠️ Il codice generato esiste già.\n"
                "Riprova il rito con /generacodice.",
                parse_mode="HTML"
            )
            context.user_data.pop("gen_code", None)
            context.user_data.pop("gen_owner", None)
            return

        row = await asyncio.to_thread(db_insert_code, code, owner, user.id)

        # Messaggio finale all'eremita
        text = (
            "<b>𝐂𝐔𝐋𝐓𝐎 𝐃𝐈 𝐏𝐎𝐒𝐄𝐈𝐃𝐎𝐍𝐄</b> ⚓️\n\n"
            "✅ <b>Codice creato con successo!</b>\n\n"
            "Ecco i dettagli del codice sacro:\n\n"
            f"• 🆔 ID: <b>{row['id']}</b>\n"
            f"• 👤 Player: <b>{row['owner']}</b>\n"
            f"• 🔐 Codice: <b>{row['code']}</b>\n"
            f"• 🕰️ Creato alle: <b>{row['created_at']}</b>"
        )
        await query.edit_message_text(text, parse_mode="HTML")

        # Notifica al gruppo direzione
        dir_text = (
            "<b>📜 NUOVO CODICE GENERATO</b>\n\n"
            f"• 🆔 ID: <b>{row['id']}</b>\n"
            f"• 👤 Player: <b>{row['owner']}</b>\n"
            f"• 🔐 Codice: <b>{row['code']}</b>\n"
            f"• 🧙‍♂️ Creato da: <b>{user.full_name}</b> (ID {user.id})\n"
            f"• 🕰️ Orario: <b>{row['created_at']}</b>"
        )

        try:
            await context.bot.send_message(
                chat_id=DIRECTION_CHAT_ID,
                text=dir_text,
                message_thread_id=299,
                parse_mode="HTML"
            )
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
        "<b>𝐂𝐔𝐋𝐓𝐎 𝐃𝐈 𝐏𝐎𝐒𝐄𝐈𝐃𝐎𝐍𝐄</b> ⚓️\n\n"
        "🔍 Inserisci il <b>codice sacro</b> (4 cifre) che desideri verificare.",
        parse_mode="HTML"
    )
    context.user_data["check_prompt_message_id"] = msg.message_id
    context.user_data["check_user_command_message_id"] = update.message.message_id
    return CHECK_GET_CODE


async def controllacodice_get_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    role = get_role(user.id)
    if role != "hermit":
        await update.message.reply_text(
            "<b>𝐂𝐔𝐋𝐓𝐎 𝐃𝐈 𝐏𝐎𝐒𝐄𝐈𝐃𝐎𝐍𝐄</b> ⚓️\n\n"
            "⛔ Non sei autorizzato a compiere questo rito.",
            parse_mode="HTML"
        )
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
        text = (
            "<b>𝐂𝐔𝐋𝐓𝐎 𝐃𝐈 𝐏𝐎𝐒𝐄𝐈𝐃𝐎𝐍𝐄</b> ⚓️\n\n"
            f"❌ Il codice <b>{code}</b> non è stato trovato o non è più attivo."
        )
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
                parse_mode="HTML"
            )
        else:
            await update.effective_chat.send_message(text, reply_markup=keyboard, parse_mode="HTML")
        return ConversationHandler.END

    status = "🟢 <b>ATTIVO</b>" if row["active"] else "🔴 <b>ESTINTO</b>"

    text = (
        "<b>𝐂𝐔𝐋𝐓𝐎 𝐃𝐈 𝐏𝐎𝐒𝐄𝐈𝐃𝐎𝐍𝐄</b> ⚓️\n\n"
        f"📜 <b>Dettagli del codice {code}</b>\n\n"
        f"• 🆔 ID: <b>{row['id']}</b>\n"
        f"• 👤 Player: <b>{row['owner']}</b>\n"
        f"• 🕰️ Creato alle: <b>{row['created_at']}</b>\n"
        f"• 🔒 Stato: {status}"
    )

    if row["active"]:
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🔥 Estingui codice", callback_data=f"extinguish:{code}"
                    ),
                    InlineKeyboardButton("❌ Annulla", callback_data="check_close"),
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
            parse_mode="HTML"
        )
    else:
        msg = await update.effective_chat.send_message(text, reply_markup=keyboard, parse_mode="HTML")
        context.user_data["check_prompt_message_id"] = msg.message_id

    context.user_data["check_code"] = code
    return ConversationHandler.END


async def controllacodice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    user = query.from_user
    role = get_role(user.id)
    if role != "hermit":
        await query.edit_message_text(
            "<b>𝐂𝐔𝐋𝐓𝐎 𝐃𝐈 𝐏𝐎𝐒𝐄𝐈𝐃𝐎𝐍𝐄</b> ⚓️\n\n"
            "⛔ Non sei autorizzato.",
            parse_mode="HTML"
        )
        return

    data = query.data

    if data == "check_close":
        await query.edit_message_text(
            "<b>𝐂𝐔𝐋𝐓𝐎 𝐃𝐈 𝐏𝐎𝐒𝐄𝐈𝐃𝐎𝐍𝐄</b> ⚓️\n\n"
            "🔚 Operazione conclusa.",
            parse_mode="HTML"
        )
        context.user_data.pop("check_code", None)
        return

    if data.startswith("extinguish:"):
        code = data.split(":", 1)[1]

        text = (
            "<b>𝐂𝐔𝐋𝐓𝐎 𝐃𝐈 𝐏𝐎𝐒𝐄𝐈𝐃𝐎𝐍𝐄</b> ⚓️\n\n"
            f"🔥 Sei sicuro di voler <b>estingue­re</b> il codice <b>{code}</b>?"
        )

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

        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")
        return

    if data.startswith("extinguish_confirm:"):
        code = data.split(":", 1)[1]
        row = await asyncio.to_thread(db_extinguish_code, code)

        if row is None:
            await query.edit_message_text(
                "<b>𝐂𝐔𝐋𝐓𝐎 𝐃𝐈 𝐏𝐎𝐒𝐄𝐈𝐃𝐎𝐍𝐄</b> ⚓️\n\n"
                f"⚠️ Il codice <b>{code}</b> non è attivo o non esiste più.",
                parse_mode="HTML"
            )
            return

        text = (
            "<b>𝐂𝐔𝐋𝐓𝐎 𝐃𝐈 𝐏𝐎𝐒𝐄𝐈𝐃𝐎𝐍𝐄</b> ⚓️\n\n"
            f"🔥 <b>Codice {code} estinto con successo.</b>\n\n"
            f"• 🆔 ID: <b>{row['id']}</b>\n"
            f"• 👤 Player: <b>{row['owner']}</b>\n"
            f"• 🕰️ Creato alle: <b>{row['created_at']}</b>\n"
            f"• 🔒 Stato: <b>ESTINTO</b>"
        )

        await query.edit_message_text(text, parse_mode="HTML")

        # Notifica al gruppo direzione
        dir_text = (
            "<b>⚠️ CODICE ESTINTO</b>\n\n"
            f"• 🆔 ID: <b>{row['id']}</b>\n"
            f"• 👤 Player: <b>{row['owner']}</b>\n"
            f"• 🔐 Codice: <b>{row['code']}</b>\n"
            f"• 🧙‍♂️ Estinto da: <b>{user.full_name}</b> (ID {user.id})"
        )

        try:
            await context.bot.send_message(
                chat_id=DIRECTION_CHAT_ID,
                text=dir_text,
                message_thread_id=299,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error("Errore invio messaggio direzione: %s", e)

        context.user_data.pop("check_code", None)


async def modulomensa_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Salviamo chi sta compilando il modulo
    user = update.effective_user
    context.user_data["mensa_registratore_id"] = user.id
    context.user_data["mensa_registratore_username"] = user.username or "Nessun_username"

    msg = await update.message.reply_text(
        "<b>𝐂𝐔𝐋𝐓𝐎 𝐃𝐈 𝐏𝐎𝐒𝐄𝐈𝐃𝐎𝐍𝐄</b> ⚓️\n\n"
        "📝 Per iniziare la registrazione del modulo mensa, inserisci il <b>nickname del fedele</b> "
        "a cui è stato consegnato il cibo.\n\n"
        "Assicurati che il nome sia corretto prima di procedere.",
        parse_mode="HTML"
    )

    context.user_data["mensa_msg"] = msg
    return MOD_MENSA_NICK



async def modulomensa_get_nick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nick = update.message.text.strip()
    context.user_data["mensa_nick"] = nick

    await update.message.delete()

    msg = context.user_data["mensa_msg"]
    await msg.edit_text(
        "<b>𝐂𝐔𝐋𝐓𝐎 𝐃𝐈 𝐏𝐎𝐒𝐄𝐈𝐃𝐎𝐍𝐄</b> ⚓️\n\n"
        "🍽️ Inserisci ora la <b>quantità di cibo</b> distribuita al fedele.\n\n"
        "Puoi indicare porzioni, sacchetti o una descrizione breve.",
        parse_mode="HTML"
    )

    return MOD_MENSA_QTY



async def modulomensa_get_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    qty = update.message.text.strip()
    context.user_data["mensa_qty"] = qty

    await update.message.delete()

    nick = context.user_data["mensa_nick"]
    registratore = context.user_data["mensa_registratore_username"]
    msg = context.user_data["mensa_msg"]

    await msg.edit_text(
        "<b>𝐂𝐔𝐋𝐓𝐎 𝐃𝐈 𝐏𝐎𝐒𝐄𝐈𝐃𝐎𝐍𝐄</b> ⚓️\n\n"
        "📋 Sei arrivato alla fine della registrazione.\n\n"
        "Qui sotto trovi il <i>resoconto</i> delle informazioni inserite. "
        "Controlla che siano corrette e conferma il modulo:\n\n"
        f"• 👤 Fedele: <b>{nick}</b>\n"
        f"• 🍽️ Quantità: <b>{qty}</b>\n"
        f"• 🧙‍♂️ Registrato da: <b>@{registratore}</b>\n\n"
        "Vuoi confermare la registrazione?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Conferma", callback_data="mensa_confirm")],
            [InlineKeyboardButton("❌ Annulla", callback_data="mensa_cancel")]
        ])
    )

    return MOD_MENSA_CONFIRM



async def modulomensa_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "mensa_cancel":
        await query.edit_message_text(
            "<b>𝐂𝐔𝐋𝐓𝐎 𝐃𝐈 𝐏𝐎𝐒𝐄𝐈𝐃𝐎𝐍𝐄</b> ⚓️\n\n"
            "❌ Registrazione annullata.",
            parse_mode="HTML"
        )
        return ConversationHandler.END

    if query.data == "mensa_confirm":
        nick = context.user_data["mensa_nick"]
        qty = context.user_data["mensa_qty"]
        registratore_id = context.user_data["mensa_registratore_id"]
        registratore_username = context.user_data["mensa_registratore_username"]

        # Salvataggio nel DB
        save_mensa_record(nick, qty, registratore_id, registratore_username)

        # Invio nel gruppo direzione
        await context.bot.send_message(
            chat_id=DIRECTION_CHAT_ID,
            text=(
                "<b>📜 NUOVA REGISTRAZIONE MENSA</b>\n\n"
                f"• 👤 Fedele: <b>{nick}</b>\n"
                f"• 🍽️ Quantità: <b>{qty}</b>\n"
                f"• 🧙‍♂️ Registrato da: <b>@{registratore_username}</b> (ID: {registratore_id})\n"
                f"• 🕰️ Data: <b>{datetime.now().strftime('%d/%m/%Y %H:%M')}</b>"
            ),
            parse_mode="HTML",
            message_thread_id=297
        )

        await query.edit_message_text(
            "<b>𝐂𝐔𝐋𝐓𝐎 𝐃𝐈 𝐏𝐎𝐒𝐄𝐈𝐃𝐎𝐍𝐄</b> ⚓️\n\n"
            "✅ Modulo mensa registrato con successo.",
            parse_mode="HTML"
        )

        return ConversationHandler.END



def save_mensa_record(nick, qty, registratore_id, registratore_username):
    conn = psycopg.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO mensa (nickname, quantita, registratore_id, registratore_username, data)
        VALUES (%s, %s, %s, %s, NOW())
        """,
        (nick, qty, registratore_id, registratore_username)
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
