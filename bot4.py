#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import json
import sqlite3
import random
import logging
import asyncio
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
    ConversationHandler,
)
from telegram.constants import ParseMode

# ======================== التهيئة ========================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "bot_database.db")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(","))) if os.getenv("ADMIN_IDS") else []
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("BOT_TOKEN not set in environment")

# ======================== دوال مساعدة ========================
def escape_html(text: str) -> str:
    if not text:
        return ""
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

# ======================== قاعدة البيانات ========================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        question TEXT NOT NULL,
        options TEXT NOT NULL,
        answer INTEGER NOT NULL,
        explanation TEXT,
        category TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        state TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS answers_log (
        user_id INTEGER,
        question_id INTEGER,
        correct INTEGER,
        answered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, question_id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS bookmarks (
        user_id INTEGER,
        question_id INTEGER,
        PRIMARY KEY (user_id, question_id)
    )''')
    conn.commit()
    conn.close()

def get_db_connection():
    return sqlite3.connect(DB_PATH)

def load_questions_from_json():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM questions")
    pattern = os.path.join(BASE_DIR, '*.json')
    import glob
    files = glob.glob(pattern)
    files = [f for f in files if not f.endswith('_export.csv') and 'user_data' not in f]
    def extract_num(f):
        nums = re.findall(r'\d+', f)
        return int(nums[0]) if nums else float('inf')
    files.sort(key=extract_num)
    count = 0
    for file in files:
        try:
            with open(file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            raw = []
            if 'exam' in data and 'questions' in data['exam']:
                raw = data['exam']['questions']
            elif 'questions' in data:
                raw = data['questions']
            else:
                for val in data.values():
                    if isinstance(val, list) and val and isinstance(val[0], dict) and 'question' in val[0]:
                        raw.extend(val)
            for q in raw:
                if q.get('question') and q.get('options'):
                    c.execute(
                        "INSERT INTO questions (question, options, answer, explanation, category) VALUES (?, ?, ?, ?, ?)",
                        (q['question'], json.dumps(q['options']), q.get('answer', 0), q.get('explanation', ''), q.get('category', 'غير مصنف'))
                    )
                    count += 1
        except Exception as e:
            logger.error(f"خطأ في تحميل {file}: {e}")
    conn.commit()
    conn.close()
    logger.info(f"تم تحميل {count} سؤال من JSON إلى قاعدة البيانات")
    return count

def get_all_questions():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, question, options, answer, explanation, category FROM questions")
    rows = c.fetchall()
    conn.close()
    return rows

def get_question_by_id(qid):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, question, options, answer, explanation, category FROM questions WHERE id=?", (qid,))
    row = c.fetchone()
    conn.close()
    if row:
        options = json.loads(row[2])
        return (row[0], row[1], options, row[3], row[4], row[5])
    return None

def get_categories():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT DISTINCT category FROM questions ORDER BY category")
    rows = c.fetchall()
    conn.close()
    return [row[0] for row in rows]

def get_questions_by_category(category):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, question, options, answer, explanation, category FROM questions WHERE category=?", (category,))
    rows = c.fetchall()
    conn.close()
    return rows

# ======================== دوال حالة المستخدم ========================
def get_user_state(user_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT state FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    if row:
        state = json.loads(row[0])
        if 'used_questions' not in state:
            state['used_questions'] = []
    else:
        all_q = get_all_questions()
        qids = [q[0] for q in all_q]
        state = {
            'current_ids': qids,
            'current_index': 0,
            'answers': {},
            'bookmarks': [],
            'mode': 'normal',
            'quiz_time': None,
            'start_time': None,
            'used_questions': [],
        }
        c.execute("INSERT INTO users (user_id, state) VALUES (?, ?)", (user_id, json.dumps(state)))
        conn.commit()
    conn.close()
    return state

def save_user_state(user_id, state):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE users SET state=? WHERE user_id=?", (json.dumps(state), user_id))
    conn.commit()
    conn.close()

def get_answer_log(user_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT question_id, correct FROM answers_log WHERE user_id=?", (user_id,))
    rows = c.fetchall()
    conn.close()
    return {qid: bool(correct) for qid, correct in rows}

def log_answer(user_id, qid, correct):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO answers_log (user_id, question_id, correct, answered_at) VALUES (?, ?, ?, ?)",
              (user_id, qid, 1 if correct else 0, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def toggle_bookmark(user_id, qid):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT 1 FROM bookmarks WHERE user_id=? AND question_id=?", (user_id, qid))
    exists = c.fetchone()
    if exists:
        c.execute("DELETE FROM bookmarks WHERE user_id=? AND question_id=?", (user_id, qid))
        conn.commit()
        conn.close()
        return False
    else:
        c.execute("INSERT INTO bookmarks (user_id, question_id) VALUES (?, ?)", (user_id, qid))
        conn.commit()
        conn.close()
        return True

def get_bookmarks(user_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT question_id FROM bookmarks WHERE user_id=?", (user_id,))
    rows = c.fetchall()
    conn.close()
    return [row[0] for row in rows]

def get_wrong_questions(user_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT question_id FROM answers_log WHERE user_id=? AND correct=0", (user_id,))
    rows = c.fetchall()
    conn.close()
    return [row[0] for row in rows]

def get_weak_categories(user_id):
    log = get_answer_log(user_id)
    if not log:
        return []
    cat_stats = {}
    for qid, correct in log.items():
        q = get_question_by_id(qid)
        if q:
            cat = q[5] or 'غير مصنف'
            if cat not in cat_stats:
                cat_stats[cat] = {'correct': 0, 'total': 0}
            cat_stats[cat]['total'] += 1
            if correct:
                cat_stats[cat]['correct'] += 1
    weak = []
    for cat, stats in cat_stats.items():
        ratio = stats['correct'] / stats['total'] if stats['total'] > 0 else 0
        if ratio < 0.5:
            weak.append(cat)
    return weak

def get_unanswered_questions(user_id):
    answered = get_answer_log(user_id).keys()
    state = get_user_state(user_id)
    used = set(state.get('used_questions', []))
    all_q = get_all_questions()
    all_ids = [q[0] for q in all_q]
    available = [qid for qid in all_ids if qid not in answered and qid not in used]
    return available

# ======================== دوال واجهة المستخدم ========================
def build_main_menu(user_id=None):
    buttons = [
        [InlineKeyboardButton("📝 اختبار عادي", callback_data="start_quiz")],
        [InlineKeyboardButton("📝 اختبار مخصص", callback_data="custom_quiz")],
        [InlineKeyboardButton("📖 وضع التعلم", callback_data="study_mode")],
        [InlineKeyboardButton("📂 تصفية حسب الفئة", callback_data="categories")],
        [InlineKeyboardButton("⭐ إشاراتي", callback_data="bookmarks")],
        [InlineKeyboardButton("❌ الأخطاء فقط", callback_data="wrong_only")],
        [InlineKeyboardButton("💡 اقتراح أسئلة (نقاط ضعف)", callback_data="suggest")],
        [InlineKeyboardButton("📊 إحصائياتي", callback_data="stats")],
        [InlineKeyboardButton("📥 تصدير النتائج", callback_data="export")],
        [InlineKeyboardButton("🔄 إعادة تعيين", callback_data="reset")],
    ]
    if user_id and user_id in ADMIN_IDS:
        buttons.append([InlineKeyboardButton("⚙️ لوحة تحكم المشرف", callback_data="admin_panel")])
    return InlineKeyboardMarkup(buttons)

def build_back_button():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="menu")]])

def build_question_keyboard(qid, idx, total, state, time_left=None):
    buttons = []
    nav = []
    if idx > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"nav_prev_{idx}"))
    if idx < total - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"nav_next_{idx}"))
    nav.append(InlineKeyboardButton("📖 شرح", callback_data=f"show_explain_{qid}"))
    bookmarked = qid in state.get('bookmarks', [])
    star = "⭐" if bookmarked else "☆"
    nav.append(InlineKeyboardButton(star, callback_data=f"bookmark_{qid}"))
    buttons.append(nav)
    buttons.append([InlineKeyboardButton("🏠 القائمة", callback_data="menu")])
    if state.get('mode') == 'study':
        buttons.append([InlineKeyboardButton("🔄 إنهاء وضع التعلم", callback_data="exit_study")])
    if time_left is not None:
        mins, secs = divmod(time_left, 60)
        buttons.append([InlineKeyboardButton(f"⏱ {mins:02d}:{secs:02d}", callback_data="noop")])
    return InlineKeyboardMarkup(buttons)

def build_option_buttons(q, state):
    """أزرار الخيارات مع callback_data بصيغة answer_qid_i"""
    qid, question, options, answer, explanation, category = q
    buttons = []
    ans = state.get('answers', {})
    selected = ans.get(str(qid))
    answered = selected is not None
    
    for i, opt in enumerate(options):
        text = escape_html(opt)
        if answered:
            if i == answer:
                text = "✅ " + text
            elif i == selected:
                text = "❌ " + text
        # تغيير الصيغة إلى answer_ بدلاً من ans_
        callback = f"answer_{qid}_{i}" if not answered else "noop"
        buttons.append([InlineKeyboardButton(text, callback_data=callback)])
    return InlineKeyboardMarkup(buttons)

def format_question_header(q, idx, total, time_left=None):
    qid, question, options, answer, explanation, category = q
    cat = escape_html(category or "غير مصنف")
    q_text = escape_html(question)
    
    progress = int((idx / total) * 20) if total else 0
    bar = "█" * progress + "░" * (20 - progress)
    progress_text = f"<code>[{bar}] {int((idx/total)*100) if total else 0}%</code>"
    time_str = ""
    if time_left is not None:
        if time_left > 0:
            mins, secs = divmod(time_left, 60)
            time_str = f"⏳ <b>الوقت المتبقي:</b> <code>{mins:02d}:{secs:02d}</code>"
        else:
            time_str = "⏰ <b>انتهى الوقت!</b>"
    
    text = (
        f"📌 <b>السؤال {idx+1}/{total}</b>\n"
        f"📂 <b>الفئة:</b> {cat}\n"
        f"{progress_text}\n"
        f"{time_str}\n\n"
        f"<b>{q_text}</b>\n"
    )
    return text

# ======================== معالجات الأوامر ========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    state = get_user_state(user_id)
    save_user_state(user_id, state)
    welcome_text = (
        f"👋 <b>أهلاً بك {escape_html(user.first_name)} في بوت الأسئلة الشامل!</b>\n\n"
        "✨ <b>مزايا البوت:</b>\n"
        "• اختبارات عشوائية أو حسب الفئة\n"
        "• اختبار مخصص بعدد أسئلة محدد (بدون تكرار الأسئلة)\n"
        "• وضع التعلم لعرض السؤال مع الإجابة والشرح فوراً\n"
        "• مؤقت زمني في الاختبارات مع عرض تنازلي\n"
        "• إشارات مرجعية للأسئلة المهمة\n"
        "• تتبع الأخطاء واقتراح أسئلة لنقاط الضعف\n"
        "• إحصائيات متقدمة وتصدير النتائج\n"
        "• لوحة تحكم للمشرفين لإدارة الأسئلة\n\n"
        "استخدم الأزرار أدناه للبدء 🚀"
    )
    await update.message.reply_text(
        welcome_text,
        parse_mode=ParseMode.HTML,
        reply_markup=build_main_menu(user_id)
    )

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    await query.edit_message_text(
        "🏠 <b>القائمة الرئيسية</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=build_main_menu(user_id)
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 <b>الأوامر المتاحة:</b>\n"
        "/start - عرض القائمة الرئيسية\n"
        "/stats - عرض إحصائياتي\n"
        "/reset - إعادة تعيين التقدم\n"
        "/export - تصدير النتائج كملف CSV\n"
        "/shuffle - خلط الأسئلة الحالية\n"
        "/study - تفعيل وضع التعلم\n"
        "/normal - العودة للوضع العادي\n"
        "استخدم الأزرار للتفاعل.",
        parse_mode=ParseMode.HTML
    )

# ======================== اختبار مخصص ========================
async def custom_quiz_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['awaiting_custom_quiz'] = True
    await query.edit_message_text(
        "📝 <b>اختبار مخصص</b>\n"
        "أدخل عدد الأسئلة و المدة (بالدقائق) بالصيغة:\n\n"
        "<code>عدد_الأسئلة المدة</code>\n"
        "مثال: <code>10 5</code> (يعني 10 أسئلة و 5 دقائق)\n\n"
        "ملاحظة: سيتم اختيار الأسئلة من الأسئلة التي لم تجب عليها مسبقاً.",
        parse_mode=ParseMode.HTML
    )

async def handle_quiz_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('awaiting_custom_quiz'):
        return
    
    user_id = update.effective_user.id
    text = update.message.text.strip()
    parts = text.split()
    if len(parts) != 2:
        await update.message.reply_text(
            "❌ الصيغة غير صحيحة. استخدم: <code>10 5</code>",
            parse_mode=ParseMode.HTML
        )
        return
    
    try:
        count = int(parts[0])
        minutes = int(parts[1])
    except ValueError:
        await update.message.reply_text("❌ أرقام فقط. حاول مرة أخرى.")
        return
    
    if count <= 0 or minutes <= 0:
        await update.message.reply_text("❌ يجب أن تكون الأرقام أكبر من صفر.")
        return
    
    context.user_data['awaiting_custom_quiz'] = False
    
    available_qids = get_unanswered_questions(user_id)
    if not available_qids:
        await update.message.reply_text(
            "⚠️ لا توجد أسئلة متاحة للإجابة عليها.",
            reply_markup=build_main_menu(user_id)
        )
        return
    
    if count > len(available_qids):
        count = len(available_qids)
        await update.message.reply_text(f"⚠️ سيتم استخدام {count} سؤال.")
    
    selected_qids = random.sample(available_qids, count)
    
    state = get_user_state(user_id)
    state['current_ids'] = selected_qids
    state['current_index'] = 0
    state['answers'] = {}
    state['mode'] = 'normal'
    state['quiz_time'] = minutes * 60
    state['start_time'] = datetime.now().isoformat()
    state['used_questions'] = state.get('used_questions', []) + selected_qids
    save_user_state(user_id, state)
    
    job = context.job_queue.run_once(
        quiz_timeout,
        minutes * 60,
        user_id=user_id,
        name=f"quiz_timeout_{user_id}"
    )
    context.user_data['timeout_job'] = job
    
    await update.message.reply_text(
        f"⏱ بدء الاختبار: {count} سؤالاً، {minutes} دقيقة. حظاً موفقاً!",
        parse_mode=ParseMode.HTML
    )
    
    # ===== إرسال السؤال الأول =====
    first_qid = selected_qids[0]
    q = get_question_by_id(first_qid)
    if q:
        time_left = minutes * 60
        header_text = format_question_header(q, 0, len(selected_qids), time_left)
        option_keyboard = build_option_buttons(q, state)
        nav_keyboard = build_question_keyboard(first_qid, 0, len(selected_qids), state, time_left)
        combined_keyboard = InlineKeyboardMarkup(
            option_keyboard.inline_keyboard + nav_keyboard.inline_keyboard
        )
        await context.bot.send_message(
            chat_id=user_id,
            text=header_text,
            parse_mode=ParseMode.HTML,
            reply_markup=combined_keyboard
        )
    else:
        await update.message.reply_text("⚠️ حدث خطأ في تحميل السؤال الأول.")

# ======================== وظائف الاختبار ========================
async def start_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE, mode='normal'):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    state = get_user_state(user_id)
    all_q = get_all_questions()
    qids = [q[0] for q in all_q]
    state['current_ids'] = qids
    state['current_index'] = 0
    state['mode'] = mode
    state['answers'] = {}
    state['quiz_time'] = None
    state['start_time'] = None
    save_user_state(user_id, state)
    await show_current_question(update, context, user_id)

async def show_current_question(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id=None):
    if not user_id:
        if update.callback_query:
            user_id = update.callback_query.from_user.id
        elif update.message:
            user_id = update.effective_user.id
        else:
            return
    
    state = get_user_state(user_id)
    qids = state['current_ids']
    idx = state['current_index']
    if idx >= len(qids):
        await send_quiz_complete(update, context, user_id)
        return
    
    qid = qids[idx]
    q = get_question_by_id(qid)
    if not q:
        if update.callback_query:
            await update.callback_query.answer("السؤال غير موجود!")
        else:
            await update.message.reply_text("السؤال غير موجود!")
        return
    
    time_left = None
    if state.get('quiz_time') and state.get('start_time'):
        start = datetime.fromisoformat(state['start_time'])
        elapsed = (datetime.now() - start).total_seconds()
        time_left = max(0, state['quiz_time'] - elapsed)
        if time_left <= 0:
            await quiz_timeout(context, user_id=user_id)
            return
    
    show_explanation = (state.get('mode') == 'study')
    if show_explanation:
        state['answers'][str(qid)] = q[3]
        save_user_state(user_id, state)
    
    total = len(qids)
    header_text = format_question_header(q, idx, total, time_left)
    if show_explanation and q[4]:
        header_text += f"\n📖 <b>الشرح:</b>\n{escape_html(q[4])}"
    
    option_keyboard = build_option_buttons(q, state)
    nav_keyboard = build_question_keyboard(qid, idx, total, state, time_left)
    combined_keyboard = InlineKeyboardMarkup(
        option_keyboard.inline_keyboard + nav_keyboard.inline_keyboard
    )
    
    try:
        if update.callback_query:
            await update.callback_query.edit_message_text(
                header_text,
                parse_mode=ParseMode.HTML,
                reply_markup=combined_keyboard
            )
        else:
            await update.message.reply_text(
                header_text,
                parse_mode=ParseMode.HTML,
                reply_markup=combined_keyboard
            )
    except Exception as e:
        logger.error(f"خطأ في عرض السؤال: {e}")
        if update.callback_query:
            await update.callback_query.message.reply_text(
                header_text,
                parse_mode=ParseMode.HTML,
                reply_markup=combined_keyboard
            )
        else:
            await update.message.reply_text(
                header_text,
                parse_mode=ParseMode.HTML,
                reply_markup=combined_keyboard
            )

async def send_quiz_complete(update, context, user_id):
    state = get_user_state(user_id)
    total = len(state['current_ids'])
    answered = len(state['answers'])
    correct = 0
    for qid_str, opt in state['answers'].items():
        qid = int(qid_str)
        q = get_question_by_id(qid)
        if q and q[3] == opt:
            correct += 1
    text = (
        f"🎉 <b>انتهى الاختبار!</b>\n"
        f"📊 <b>النتيجة:</b>\n"
        f"✅ صحيح: {correct}\n"
        f"❌ خطأ: {answered - correct}\n"
        f"📝 تم الإجابة على {answered}/{total}"
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=build_main_menu(user_id)
        )
    else:
        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=build_main_menu(user_id)
        )

# ======================== معالجات الأزرار ========================
async def answer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة اختيار إجابة (صيغة answer_qid_i)"""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    
    if not data.startswith("answer_"):
        return
    
    try:
        parts = data.split("_")
        if len(parts) != 3:
            return
        qid = int(parts[1])
        opt = int(parts[2])
    except (ValueError, IndexError):
        return
    
    state = get_user_state(user_id)
    
    if qid not in state['current_ids']:
        await query.answer("السؤال ليس في القائمة الحالية!")
        return
    
    q = get_question_by_id(qid)
    if not q:
        await query.answer("السؤال غير موجود!")
        return
    
    if str(qid) in state.get('answers', {}):
        await query.answer("لقد أجبت بالفعل!")
        return
    
    correct = (opt == q[3])
    state['answers'][str(qid)] = opt
    log_answer(user_id, qid, correct)
    save_user_state(user_id, state)
    
    await show_current_question(update, context, user_id)

async def nav_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    if data.startswith("nav_prev_"):
        _, _, idx = data.split("_")
        idx = int(idx)
        state = get_user_state(user_id)
        if idx > 0:
            state['current_index'] = idx - 1
            save_user_state(user_id, state)
            await show_current_question(update, context, user_id)
    elif data.startswith("nav_next_"):
        _, _, idx = data.split("_")
        idx = int(idx)
        state = get_user_state(user_id)
        if idx < len(state['current_ids']) - 1:
            state['current_index'] = idx + 1
            save_user_state(user_id, state)
            await show_current_question(update, context, user_id)

async def explain_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    if data.startswith("show_explain_"):
        qid = int(data.split("_")[2])
        q = get_question_by_id(qid)
        if q:
            expl = escape_html(q[4]) if q[4] else "لا يوجد شرح."
            text = f"📖 <b>الشرح:</b>\n{expl}"
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=f"back_from_explain_{qid}")]])
            )

async def back_from_explain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    await show_current_question(update, context, user_id)

async def bookmark_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    if data.startswith("bookmark_"):
        qid = int(data.split("_")[1])
        bookmarked = toggle_bookmark(user_id, qid)
        state = get_user_state(user_id)
        if bookmarked:
            if qid not in state['bookmarks']:
                state['bookmarks'].append(qid)
        else:
            if qid in state['bookmarks']:
                state['bookmarks'].remove(qid)
        save_user_state(user_id, state)
        await show_current_question(update, context, user_id)

# ======================== باقي الدوال (وضع التعلم، إحصائيات، تصدير، إعادة تعيين، تصفية، إلخ) ========================
# (نفس الكود السابق، مع الحفاظ على نفس الدوال)

# ======================== دوال التشغيل ========================
async def run_webhook_async(application):
    WEBHOOK_URL = os.getenv("WEBHOOK_URL")
    if not WEBHOOK_URL:
        logger.error("WEBHOOK_URL غير معرف")
        return

    try:
        from aiohttp import web
    except ImportError:
        logger.error("مكتبة aiohttp غير مثبتة")
        return

    await application.initialize()
    await application.start()

    async def webhook_handler(request):
        try:
            data = await request.json()
            if not data:
                return web.Response(text="Empty", status=400)
            from telegram import Update
            update = Update.de_json(data, application.bot)
            await application.process_update(update)
            return web.Response(text="OK", status=200)
        except Exception as e:
            logger.error(f"خطأ في webhook: {e}", exc_info=True)
            return web.Response(text=f"Error: {e}", status=500)

    async def health_check(request):
        return web.Response(text="OK", status=200)

    async def root(request):
        return web.Response(text="Bot is running", status=200)

    app = web.Application()
    app.router.add_get("/", root)
    app.router.add_get("/health", health_check)
    app.router.add_post(f"/{TOKEN}", webhook_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"خادم webhook يعمل على المنفذ {port}")

    webhook_url = f"{WEBHOOK_URL}/{TOKEN}"
    await application.bot.set_webhook(webhook_url)
    logger.info(f"تم تعيين webhook إلى {webhook_url}")

    await asyncio.Event().wait()

def main():
    init_db()
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM questions")
    if c.fetchone()[0] == 0:
        load_questions_from_json()
    conn.close()

    application = Application.builder().token(TOKEN).build()

    # إضافة المعالجات
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("shuffle", shuffle_command))
    application.add_handler(CommandHandler("list_questions", list_questions_command))
    application.add_handler(CommandHandler("stats", lambda u, c: stats(u, c)))

    # اختبار مخصص
    application.add_handler(CallbackQueryHandler(custom_quiz_start, pattern="^custom_quiz$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_quiz_input))

    # ConversationHandlers للإدارة (بنفس الكود السابق)
    # ... (admin_add_conv, admin_delete_conv, admin_edit_conv) ...

    # معالجات الأزرار الأساسية - الترتيب مهم: نضع معالجات الأزرار في الأعلى
    application.add_handler(CallbackQueryHandler(answer_callback, pattern="^answer_"))
    application.add_handler(CallbackQueryHandler(nav_callback, pattern="^nav_"))
    application.add_handler(CallbackQueryHandler(explain_callback, pattern="^show_explain_"))
    application.add_handler(CallbackQueryHandler(back_from_explain, pattern="^back_from_explain_"))
    application.add_handler(CallbackQueryHandler(bookmark_callback, pattern="^bookmark_"))
    
    # معالجات القائمة والإعدادات
    application.add_handler(CallbackQueryHandler(menu, pattern="^menu$"))
    application.add_handler(CallbackQueryHandler(start_quiz, pattern="^start_quiz$"))
    application.add_handler(CallbackQueryHandler(study_mode, pattern="^study_mode$"))
    application.add_handler(CallbackQueryHandler(exit_study, pattern="^exit_study$"))
    application.add_handler(CallbackQueryHandler(suggest_questions, pattern="^suggest$"))
    application.add_handler(CallbackQueryHandler(categories, pattern="^categories$"))
    application.add_handler(CallbackQueryHandler(filter_category, pattern="^filter_cat_"))
    application.add_handler(CallbackQueryHandler(bookmarks, pattern="^bookmarks$"))
    application.add_handler(CallbackQueryHandler(wrong_only, pattern="^wrong_only$"))
    application.add_handler(CallbackQueryHandler(stats, pattern="^stats$"))
    application.add_handler(CallbackQueryHandler(export_results, pattern="^export$"))
    application.add_handler(CallbackQueryHandler(reset, pattern="^reset$"))
    application.add_handler(CallbackQueryHandler(admin_panel, pattern="^admin_panel$"))
    application.add_handler(CallbackQueryHandler(admin_import_json, pattern="^admin_import_json$"))
    application.add_handler(CallbackQueryHandler(admin_list, pattern="^admin_list$"))
    application.add_handler(CallbackQueryHandler(lambda u, c: u.callback_query.answer(), pattern="^noop$"))

    USE_WEBHOOK = os.getenv("USE_WEBHOOK", "false").lower() == "true"

    if USE_WEBHOOK:
        asyncio.run(run_webhook_async(application))
    else:
        logger.info("تشغيل البوت باستخدام Polling")
        application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
