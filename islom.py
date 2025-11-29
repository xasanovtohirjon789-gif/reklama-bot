# reklama_bot.py
# Aiogram v3 bilan yozilgan Telegram reklama bot (yagona fayl)
# Muallif: namuna
# Til: Uzbek (kommentlar ham o'zbekcha)

import asyncio
import aiosqlite
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.client.bot import DefaultBotProperties
from aiogram.filters import Command
# Text filter not used; using F (Field) expressions instead
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters.state import StateFilter
import os
import logging
from logging.handlers import RotatingFileHandler

# ====== SOZLAMALAR ======
# Bot token (safely provided by user). Do NOT share this token publicly.
BOT_TOKEN = "8577562365:AAECgiXfTHU2CX8PIyoGLN_Knmj236nEU8Q"
# Bir nechta admin bo'lsa, ularni tuple/list ga joylang
ADMIN_IDS = (7614962801, 6641028152)  # <-- Asosiy admin Telegram ID(lar)

DB_PATH = "reklama_bot.db"

# ====== Boshlang'ich tekshiruv ======
if BOT_TOKEN == "PUT_YOUR_BOT_TOKEN_HERE":
    raise RuntimeError("Iltimos, BOT_TOKEN ni reklama_bot.py ichida to'ldiring.")

# ====== Bot va dispatcher ======
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

# Configure logging to file for better diagnostics
LOG_PATH = os.path.join(os.path.dirname(__file__), "bot_debug.log")
logger = logging.getLogger()
if not logger.handlers:
    logger.setLevel(logging.INFO)
    fh = RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=3, encoding='utf-8')
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    # also keep console handler
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)

# ====== FSM holatlari ======
class AddAdStates(StatesGroup):
    choosing_section = State()
    waiting_for_content = State()
    choosing_groups = State()
    confirming_send = State()

class AddSectionStates(StatesGroup):
    waiting_section_name = State()

class AddGroupStates(StatesGroup):
    waiting_group_id = State()
    waiting_group_link = State()
    waiting_group_name = State()

# ====== Yordamchi — DB inits ======
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            joined_at TEXT
        );
        CREATE TABLE IF NOT EXISTS sections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id TEXT NOT NULL,
            link TEXT,
            name TEXT,
            sort_order INTEGER
        );
        CREATE TABLE IF NOT EXISTS ads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            section_id INTEGER,
            admin_id INTEGER,
            text_content TEXT,
            media_file_id TEXT,
            media_type TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS ad_groups (
            ad_id INTEGER,
            group_id INTEGER
        );
        """)
        # default: add ADMIN_IDS to admins table if not exists
        for aid in ADMIN_IDS:
            await db.execute("INSERT OR IGNORE INTO admins (id) VALUES (?)", (aid,))
        await db.commit()


async def is_admin(user_id: int) -> bool:
    """Return True if user_id is in ADMIN_IDS or exists in admins table."""
    if user_id in ADMIN_IDS:
        return True
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT 1 FROM admins WHERE id = ?", (user_id,))
        row = await cur.fetchone()
        return bool(row)

# ====== DB helper functions ======
async def add_user(user: types.User):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (id, username, first_name, last_name, joined_at) VALUES (?, ?, ?, ?, ?)",
            (user.id, user.username or "", user.first_name or "", user.last_name or "", datetime.utcnow().isoformat())
        )
        await db.commit()

async def get_sections():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, name FROM sections ORDER BY id")
        rows = await cur.fetchall()
        return rows

async def add_section(name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO sections (name, created_at) VALUES (?, ?)", (name, datetime.utcnow().isoformat()))
        await db.commit()
        cur = await db.execute("SELECT last_insert_rowid()")
        r = await cur.fetchone()
        return r[0]

async def delete_section(section_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM sections WHERE id = ?", (section_id,))
        await db.execute("DELETE FROM ads WHERE section_id = ?", (section_id,))
        await db.commit()

async def add_group(tg_id: str, link: str, name: str, sort_order: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO groups (tg_id, link, name, sort_order) VALUES (?, ?, ?, ?)",
                         (tg_id, link, name, sort_order))
        await db.commit()

async def get_groups():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, tg_id, link, name, sort_order FROM groups ORDER BY sort_order ASC")
        rows = await cur.fetchall()
        return rows


async def delete_group(group_id: int):
    """Delete a group and any ad_group links referencing it."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM groups WHERE id = ?", (group_id,))
        await db.execute("DELETE FROM ad_groups WHERE group_id = ?", (group_id,))
        await db.commit()

async def add_ad(section_id: int, admin_id: int, text_content: str, media_file_id: str, media_type: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("INSERT INTO ads (section_id, admin_id, text_content, media_file_id, media_type, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                     (section_id, admin_id, text_content or "", media_file_id or "", media_type or "", datetime.utcnow().isoformat()))
        await db.commit()
        cur2 = await db.execute("SELECT last_insert_rowid()")
        r = await cur2.fetchone()
        return r[0]

async def link_ad_to_groups(ad_id: int, group_ids: list):
    async with aiosqlite.connect(DB_PATH) as db:
        for gid in group_ids:
            await db.execute("INSERT INTO ad_groups (ad_id, group_id) VALUES (?, ?)", (ad_id, gid))
        await db.commit()

async def get_all_user_ids():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id FROM users")
        rows = await cur.fetchall()
        return [r[0] for r in rows]

# ====== Reply keyboards ======
def admin_main_kb():
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="/addad"), KeyboardButton(text="/sections")],
            [KeyboardButton(text="/addgroup"), KeyboardButton(text="/groupslist")],
            [KeyboardButton(text="/delgroup"), KeyboardButton(text="/exit")],
            
        ], resize_keyboard=True
    )
    return kb

def user_main_kb():
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📋 Bo'limlarni ko'rish")]], resize_keyboard=True
    )
    return kb

# ====== Start handler (foydalanuvchilar ro'yxatiga qo'shish) ======
@dp.message(Command(commands=["start"]))
async def cmd_start(message: types.Message):
    try:
        await add_user(message.from_user)
        # agar admin bo'lsa admin klaviatura, aks holda oddiy
        try:
            admin_flag = await is_admin(message.from_user.id)
        except Exception:
            admin_flag = message.from_user.id in ADMIN_IDS

        if admin_flag:
            await message.answer("Salom, Admin! Reklama botga xush kelibsiz.", reply_markup=admin_main_kb())
        else:
            await message.answer("Salom! Reklamalarni ko'rish uchun bo'limni tanlang.", reply_markup=user_main_kb())
    except Exception:
        # log va foydalanuvchiga ogohlantirish
        import logging
        logging.exception("Error in /start handler")
        try:
            await message.answer("Xatolik yuz berdi. Iltimos, keyinroq urinib ko'ring.")
        except Exception:
            pass


@dp.message(Command(commands=["addad"]))
async def admin_add_ad_command(message: types.Message, state: FSMContext):
    """Wrapper so that /addad command triggers the same flow as the keyboard button."""
    import logging
    logging.info("/addad command handler invoked by %s (%s)", message.from_user.id, message.from_user.full_name)
    try:
        await message.reply("Reklama qo'shish jarayoni boshlandi...")
        await admin_add_ad_start(message, state)
    except Exception as e:
        logging.exception("Error while starting add ad flow")
        try:
            await message.reply("Xatolik yuz berdi: 'addad' bosilganda. Iltimos, administratorga murojaat qiling.")
        except Exception:
            pass


@dp.message(F.text == "/addad")
async def admin_add_ad_via_text(message: types.Message, state: FSMContext):
    """Handle cases where reply keyboard sends the text '/addad' instead of a command."""
    import logging
    logging.info("/addad text handler invoked by %s (%s): %s", message.from_user.id, message.from_user.full_name, message.text)
    try:
        await message.reply("Reklama qo'shish jarayoni boshlandi...")
        await admin_add_ad_start(message, state)
    except Exception:
        logging.exception("Error in admin_add_ad_via_text")
        try:
            await message.reply("Xatolik yuz berdi: addad.")
        except Exception:
            pass


# Fallback: catch any message that starts with "/addad" (robust against extra spaces/caps)
@dp.message(lambda message: bool(message.text and message.text.strip().lower().startswith('/addad')))
async def admin_add_ad_fallback(message: types.Message, state: FSMContext):
    import logging
    logging.info("Fallback /addad handler triggered by %s (%s) text: %s", message.from_user.id, message.from_user.full_name, message.text)
    try:
        await message.reply("Reklama qo'shish jarayoni boshlandi (fallback)...")
        await admin_add_ad_start(message, state)
    except Exception:
        logging.exception("Error in admin_add_ad_fallback")
        try:
            await message.reply("Xatolik yuz berdi: addad (fallback).")
        except Exception:
            pass


# Command wrappers for admin keyboard commands -> reuse existing handlers
@dp.message(Command(commands=["sections"]))
async def cmd_sections(message: types.Message):
    await manage_sections(message)

@dp.message(F.text == "/sections")
async def cmd_sections_text(message: types.Message):
    await manage_sections(message)

@dp.message(Command(commands=["addgroup"]))
async def cmd_addgroup(message: types.Message, state: FSMContext):
    await add_group_start(message, state)

@dp.message(F.text == "/addgroup")
async def cmd_addgroup_text(message: types.Message, state: FSMContext):
    await add_group_start(message, state)

@dp.message(Command(commands=["groupslist"]))
async def cmd_groupslist(message: types.Message):
    await show_groups(message)

@dp.message(F.text == "/groupslist")
async def cmd_groupslist_text(message: types.Message):
    await show_groups(message)

@dp.message(Command(commands=["exit"]))
async def cmd_exit(message: types.Message):
    await admin_exit(message)

@dp.message(F.text == "/exit")
async def cmd_exit_text(message: types.Message):
    await admin_exit(message)


@dp.message(Command(commands=["delgroup"]))
async def cmd_delgroup(message: types.Message):
    await show_groups_for_deletion(message)

@dp.message(F.text == "/delgroup")
async def cmd_delgroup_text(message: types.Message):
    await show_groups_for_deletion(message)


@dp.message(Command(commands=["admin"]))
async def cmd_admin(message: types.Message):
    """Open admin panel if user is admin."""
    try:
        if message.from_user.id not in ADMIN_IDS:
            await message.reply("Siz admin emassiz.")
            return
        await message.answer("Admin panel:", reply_markup=admin_main_kb())
    except Exception:
        import logging
        logging.exception("Error in /admin handler")
        try:
            await message.reply("Xatolik yuz berdi.")
        except Exception:
            pass

# ====== Admin matn tugma handlerlari ======
@dp.message(F.text == "🔸 Reklama qo'shish")
async def admin_add_ad_start(message: types.Message, state: FSMContext):
    # tekshir: admin emasmi?
    try:
        if not await is_admin(message.from_user.id):
            await message.reply("Bu admin panel. Sizda ruxsat yo'q.")
            return
    except Exception:
        if message.from_user.id not in ADMIN_IDS:
            await message.reply("Bu admin panel. Sizda ruxsat yo'q.")
            return
    import logging
    logging.info("admin_add_ad_start invoked by %s (%s)", message.from_user.id, getattr(message.from_user, 'full_name', ''))
    try:
        sections = await get_sections()
    except Exception:
        logging.exception("Failed to load sections in admin_add_ad_start")
        await message.reply("Serverda xatolik bor — bo'limlarni yuklab bo'lmadi.")
        return
    rows = []
    if sections:
        for s in sections:
            rows.append([InlineKeyboardButton(text=f"{s[1]} (id:{s[0]})", callback_data=f"choose_section:{s[0]}")])
    rows.append([InlineKeyboardButton(text="➕ Yangi bo'lim qo'shish", callback_data="add_section")])
    rows.append([InlineKeyboardButton(text="↩️ Bekor qilish", callback_data="cancel")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await message.answer("Bo‘lim tanlang yoki yangi bo‘lim yarating:", reply_markup=kb)
    await state.clear()

@dp.callback_query(F.data.startswith("choose_section:"))
async def callback_choose_section(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    section_id = int(cb.data.split(":")[1])
    await state.update_data(section_id=section_id)
    await cb.message.answer("Endi reklama matni yoki rasm yuboring (matn + rasm ham bo'lsa, avval rasm, keyin matn mumkin).")
    await state.set_state(AddAdStates.waiting_for_content)

@dp.callback_query(F.data == "add_section")
async def callback_add_section(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    await cb.message.answer("Yangi bo‘lim nomini yuboring:")
    await state.set_state(AddSectionStates.waiting_section_name)

@dp.message(StateFilter(AddSectionStates.waiting_section_name))
async def process_new_section(message: types.Message, state: FSMContext):
    name = message.text.strip()
    sid = await add_section(name)
    await message.answer(f"Yangi bo'lim qo'shildi (id: {sid})")
    await state.clear()

@dp.callback_query(F.data == "cancel")
async def callback_cancel(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer("Bekor qilindi.")
    await state.clear()

# ====== Qabul qilish: reklama kontenti (matn yoki rasm) ======
@dp.message(StateFilter(AddAdStates.waiting_for_content))
async def receive_ad_content(message: types.Message, state: FSMContext):
    data = await state.get_data()
    section_id = data.get("section_id")
    if not section_id:
        await message.reply("Bo'lim topilmadi. Qayta boshlang.")
        await state.clear()
        return

    text_content = None
    media_file_id = None
    media_type = None

    if message.photo:
        media = message.photo[-1]
        media_file_id = media.file_id
        media_type = "photo"
        text_content = message.caption or ""
    elif message.text:
        text_content = message.text
    elif message.document:
        media_file_id = message.document.file_id
        media_type = "document"
        text_content = message.caption or ""
    else:
        await message.reply("Faqat matn, rasm yoki hujjat qabul qilinadi.")
        return

    # DB ga qo'shmaymiz hali — avval guruh tanlash, so'ngro yuboramiz
    await state.update_data(text_content=text_content, media_file_id=media_file_id, media_type=media_type)
    # endi guruhlarni tanlash
    groups = await get_groups()
    if not groups:
        await message.reply("Hech qanday guruh qo'shilmagan. Avval guruh qo'shing (➕ Guruh qo'shish).")
        await state.clear()
        return

    # Inline keyboard: har bir guruh uchun toggle (biz toggle ni state da ro'yxat bilan saqlaymiz)
    rows = []
    for g in groups:
        gid, tg_id, link, name, order = g
        label = f"{order}. {name or tg_id} ({tg_id})"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"toggle_group:{gid}")])
    rows.append([InlineKeyboardButton(text="✅ Tanlovni yakunlash", callback_data="finish_group_selection")])
    rows.append([InlineKeyboardButton(text="↩️ Bekor qilish", callback_data="cancel")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    # initialize chosen list
    await state.update_data(chosen_groups=[])
    await message.answer("Qaysi guruhlarga yuborilsin? (Tugmalardan kerakli guruhlarni tanlang, so'ng ✅ bosing)", reply_markup=kb)
    await state.set_state(AddAdStates.choosing_groups)

@dp.callback_query(F.data.startswith("toggle_group:"), StateFilter(AddAdStates.choosing_groups))
async def callback_toggle_group(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    gid = int(cb.data.split(":")[1])
    data = await state.get_data()
    chosen = data.get("chosen_groups", [])
    if gid in chosen:
        chosen.remove(gid)
        await cb.message.answer(f"Guruh id {gid} ro'yxatdan o'chirildi.")
    else:
        chosen.append(gid)
        await cb.message.answer(f"Guruh id {gid} tanlandi.")
    await state.update_data(chosen_groups=chosen)

@dp.callback_query(F.data == "finish_group_selection", StateFilter(AddAdStates.choosing_groups))
async def callback_finish_group_selection(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    data = await state.get_data()
    chosen = data.get("chosen_groups", [])
    if not chosen:
        await cb.message.answer("Hech qanday guruh tanlanmadi. Agar faqat foydalanuvchilarga yubormoqchi bo'lsangiz, davom eting.")
    # so'ngi tasdiq
    summary = "Reklama tayyor.\nTanlangan guruhlar: " + (", ".join(map(str, chosen)) if chosen else "Yo'q") + "\n\nYuborishni tasdiqlaysizmi?"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Yuborilsin ✅", callback_data="send_ad_confirm")],
        [InlineKeyboardButton(text="Bekor qilish ↩️", callback_data="cancel")]
    ])
    await cb.message.answer(summary, reply_markup=kb)
    await state.set_state(AddAdStates.confirming_send)

@dp.callback_query(F.data == "send_ad_confirm", StateFilter(AddAdStates.confirming_send))
async def callback_send_ad_confirm(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer("Yuborish boshlandi...")
    data = await state.get_data()
    section_id = data.get("section_id")
    text_content = data.get("text_content")
    media_file_id = data.get("media_file_id")
    media_type = data.get("media_type")
    chosen = data.get("chosen_groups", [])

    # DB ga reklama qo'shish
    ad_id = await add_ad(section_id=section_id, admin_id=cb.from_user.id, text_content=text_content, media_file_id=media_file_id, media_type=media_type)
    # ad -> groups
    if chosen:
        await link_ad_to_groups(ad_id, chosen)

    # 1) Foydalanuvchilarga yuborish
    user_ids = await get_all_user_ids()
    success_u = 0
    failed_u = 0
    for uid in user_ids:
        try:
            if media_file_id and media_type == "photo":
                await bot.send_photo(chat_id=uid, photo=media_file_id, caption=text_content or "")
            elif media_file_id and media_type == "document":
                await bot.send_document(chat_id=uid, document=media_file_id, caption=text_content or "")
            else:
                await bot.send_message(chat_id=uid, text=text_content or "")
            success_u += 1
            await asyncio.sleep(0.05)  # kichik kechikish
        except Exception as e:
            failed_u += 1
            # agar foydalanuvchi bloklagan bo'lsa yoki boshqa xatolik, davom etamiz
            continue

    # 2) Tanlangan guruhlarga yuborish + pinnash
    success_g = 0
    failed_g = 0
    for gid in chosen:
        # o'z DB da group tg_id saqlangan
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT tg_id FROM groups WHERE id = ?", (gid,))
            row = await cur.fetchone()
        if not row:
            failed_g += 1
            continue
        tg_id = row[0]
        try:
            if media_file_id and media_type == "photo":
                sent = await bot.send_photo(chat_id=tg_id, photo=media_file_id, caption=text_content or "")
            elif media_file_id and media_type == "document":
                sent = await bot.send_document(chat_id=tg_id, document=media_file_id, caption=text_content or "")
            else:
                sent = await bot.send_message(chat_id=tg_id, text=text_content or "")
            # pin qilamiz (agar botga ruxsat bo'lsa)
            try:
                # aiogram v3: pin_chat_message
                await bot.pin_chat_message(chat_id=tg_id, message_id=sent.message_id, disable_notification=True)
            except Exception:
                # pin qilinmasa xatolikni e'tiborsiz qoldiramiz
                pass
            success_g += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed_g += 1
            continue

    await cb.message.answer(f"Reklama yuborildi.\nFoydalanuvchilarga muvaffaqiyatli: {success_u}, xatolik: {failed_u}\nGuruhlarga muvaffaqiyatli: {success_g}, xatolik: {failed_g}")
    await state.clear()

# ====== Guruh qo'shish (admin) ======
@dp.message(F.text == "➕ Guruh qo'shish")
async def add_group_start(message: types.Message, state: FSMContext):
    try:
        if not await is_admin(message.from_user.id):
            await message.reply("Faqat adminlar uchun.")
            return
    except Exception:
        if message.from_user.id not in ADMIN_IDS:
            await message.reply("Faqat adminlar uchun.")
            return
    await message.answer("Guruh ID sini yuboring (masalan: -1001234567890).")
    await state.set_state(AddGroupStates.waiting_group_id)

@dp.message(StateFilter(AddGroupStates.waiting_group_id))
async def process_group_id(message: types.Message, state: FSMContext):
    gid = message.text.strip()
    await state.update_data(tg_id=gid)
    await message.answer("Guruh linkini yuboring (agar yo'q bo'lsa, '-' deb yuboring).")
    await state.set_state(AddGroupStates.waiting_group_link)

@dp.message(StateFilter(AddGroupStates.waiting_group_link))
async def process_group_link(message: types.Message, state: FSMContext):
    link = message.text.strip()
    await state.update_data(link=link)
    await message.answer("Guruh nomini yuboring (masalan: 'Tech Guruh').")
    await state.set_state(AddGroupStates.waiting_group_name)

@dp.message(StateFilter(AddGroupStates.waiting_group_name))
async def process_group_name(message: types.Message, state: FSMContext):
    name = message.text.strip()
    data = await state.get_data()
    tg_id = data.get("tg_id")
    link = data.get("link")
    # sort_order sifatida oxirgi +1 olamiz
    groups = await get_groups()
    max_order = max([g[4] for g in groups], default=0)
    await add_group(tg_id=tg_id, link=link if link != "-" else "", name=name, sort_order=max_order + 1)
    await message.answer(f"Guruh qo'shildi: {name} ({tg_id})")
    await state.clear()

# ====== Guruhlar ro'yxati ko'rsatish ======
@dp.message(F.text == "📋 Guruhlar ro'yxati")
async def show_groups(message: types.Message):
    groups = await get_groups()
    if not groups:
        await message.answer("Hozircha hech qanday guruh qo'shilmagan.")
        return
    text = "Guruhlar:\n"
    for g in groups:
        text += f"{g[4]}. {g[3] or g[1]} — {g[1]} — link: {g[2] or 'N/A'}\n"
    await message.answer(text)


async def show_groups_for_deletion(message: types.Message):
    """Show groups as inline buttons where each button deletes that group when pressed."""
    try:
        if not await is_admin(message.from_user.id):
            await message.reply("Faqat adminlar uchun.")
            return
    except Exception:
        if message.from_user.id not in ADMIN_IDS:
            await message.reply("Faqat adminlar uchun.")
            return
    groups = await get_groups()
    if not groups:
        await message.answer("Hozircha hech qanday guruh qo'shilmagan.")
        return
    rows = []
    for g in groups:
        gid, tg_id, link, name, order = g
        label = f"{order}. {name or tg_id} — {tg_id}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"delete_group:{gid}")])
    rows.append([InlineKeyboardButton(text="↩️ Bekor qilish", callback_data="cancel")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await message.answer("O'chirmoqchi bo'lgan guruhni tanlang:", reply_markup=kb)


@dp.callback_query(F.data.startswith("delete_group:"))
async def callback_delete_group(cb: types.CallbackQuery):
    await cb.answer()
    try:
        gid = int(cb.data.split(":")[1])
    except Exception:
        await cb.message.answer("Noto'g'ri guruh identifikatori.")
        return
    await delete_group(gid)
    try:
        # try removing inline keyboard from the original message
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await cb.message.answer(f"Guruh id={gid} muvaffaqiyatli o'chirildi.")

# ====== Bo'limlarni boshqarish (ko'rish, o'chirish) ======
@dp.message(F.text == "🗂 Bo'limlarni boshqarish")
async def manage_sections(message: types.Message):
    try:
        if not await is_admin(message.from_user.id):
            await message.reply("Faqat adminlar uchun.")
            return
    except Exception:
        if message.from_user.id not in ADMIN_IDS:
            await message.reply("Faqat adminlar uchun.")
            return
    sections = await get_sections()
    if not sections:
        await message.answer("Bo'limlar yo'q. Yangi bo'lim qo'shish uchun '🔸 Reklama qo'shish' orqali yoki '➕ Guruh qo'shish' orqali ishlating.")
        return
    rows = []
    for s in sections:
        rows.append([InlineKeyboardButton(text=f"📝 {s[1]} (id:{s[0]})", callback_data=f"section_view:{s[0]}")])
    rows.append([InlineKeyboardButton(text="↩️ Orqaga", callback_data="cancel")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await message.answer("Bo'limlarni boshqarish:", reply_markup=kb)

@dp.callback_query(F.data.startswith("section_view:"))
async def callback_section_view(cb: types.CallbackQuery):
    await cb.answer()
    sid = int(cb.data.split(":")[1])
    # bo'lim va uning reklamalari
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT name FROM sections WHERE id = ?", (sid,))
        row = await cur.fetchone()
        cur2 = await db.execute("SELECT id, text_content, media_type, created_at FROM ads WHERE section_id = ? ORDER BY id DESC", (sid,))
        ads = await cur2.fetchall()
    if not row:
        await cb.message.answer("Bo'lim topilmadi.")
        return
    name = row[0]
    text = f"Bo'lim: {name}\nReklamalar soni: {len(ads)}\n\n"
    for a in ads:
        aid, txt, mtype, created = a
        snippet = (txt[:100] + '...') if txt and len(txt) > 100 else (txt or "(rasm yoki hujjat)")
        text += f"ID:{aid} — {snippet} — {created}\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Bo'limni o'chirish", callback_data=f"delete_section:{sid}")],
        [InlineKeyboardButton(text="↩️ Orqaga", callback_data="cancel")]
    ])
    await cb.message.answer(text, reply_markup=kb)

@dp.callback_query(F.data.startswith("delete_section:"))
async def callback_delete_section(cb: types.CallbackQuery):
    await cb.answer()
    sid = int(cb.data.split(":")[1])
    await delete_section(sid)
    await cb.message.answer(f"Bo'lim id={sid} o'chirildi (va uning reklamalari ham).")

# ====== Oddiy foydalanuvchi: bo'limlarni ko'rish ======
@dp.message(F.text == "📋 Bo'limlarni ko'rish")
async def user_view_sections(message: types.Message):
    sections = await get_sections()
    if not sections:
        await message.answer("Hozircha bo'limlar mavjud emas.")
        return
    kb = InlineKeyboardMarkup(row_width=1)
    for s in sections:
        kb.add(InlineKeyboardButton(text=s[1], callback_data=f"user_section:{s[0]}"))
    await message.answer("Bo'limlardan birini tanlang:", reply_markup=kb)

@dp.callback_query(F.data.startswith("user_section:"))
async def callback_user_section(cb: types.CallbackQuery):
    await cb.answer()
    sid = int(cb.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, text_content, media_type, media_file_id, created_at FROM ads WHERE section_id = ? ORDER BY id DESC", (sid,))
        ads = await cur.fetchall()
    if not ads:
        await cb.message.answer("Bu bo'limda reklama hali yo'q.")
        return
    # faqat so'nggi 5 reklama namoyish qilamiz
    for a in ads[:5]:
        aid, txt, mtype, mfid, created = a
        try:
            if mtype == "photo" and mfid:
                await cb.message.answer_photo(photo=mfid, caption=txt or "")
            elif mtype == "document" and mfid:
                await cb.message.answer_document(document=mfid, caption=txt or "")
            else:
                await cb.message.answer(txt or "(rasm yoki hujjat)")
        except Exception:
            continue

# ====== Admin qilib chiqish tugmasi ======
@dp.message(F.text == "🔙 Chiqish")
async def admin_exit(message: types.Message):
    await message.answer("Asosiy menyu.", reply_markup=user_main_kb())

# ====== On startup ======
async def on_startup():
    await init_db()
    print("DB tayyor va bot ishga tushdi.")

# ====== Runner ======
async def main():
    await on_startup()
    # dispatcher start polling (aiogram v3)
    await dp.start_polling(bot)


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot to'xtatildi.")
