#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
بوت تلغرام لبنك الأسئلة الشامل (1000 سؤال)
المميزات:
- اختبار مخصص (عدد الأسئلة والزمن)
- تصفية حسب الفئة
- إشارات مرجعية
- مراجعة الأخطاء
- إحصائيات وتصدير النتائج
- واجهة تفاعلية بالأزرار
- استخدام HTML للتنسيق (آمن ولا يحتاج هروب خاص)
"""

import os
import json
import glob
import random
import csv
from datetime import datetime
from collections import defaultdict
from typing import List, Dict, Any, Optional
import logging

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

# ======================== التهيئة ========================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def escape_html(text: str) -> str:
    """هروب الأحرف الخاصة بـ HTML"""
    if not text:
        return ""
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

# ======================== إدارة الأسئلة ========================
class Question:
    __slots__ = ['id', 'question', 'options', 'answer', 'explanation', 'category', 'bookmarked']
    def __init__(self, data: Dict[str, Any]):
        self.id = data.get('id', hash(data.get('question', '')))
        self.question = data.get('question', '')
        self.options = data.get('options', [])
        self.answer = data.get('answer', 0)
        self.explanation = data.get('explanation', '')
        self.category = data.get('category', 'غير مصنف')
        self.bookmarked = False

class DataLoader:
    def __init__(self):
        self.all_questions: List[Question] = []
        self._load_from_files()
    
    def _load_from_files(self):
        pattern = os.path.join(BASE_DIR, '*.json')
        files = glob.glob(pattern)
        files = [f for f in files if not f.endswith('_export.csv') and 'user_data' not in f]
        import re
        def extract_num(f):
            nums = re.findall(r'\d+', f)
            return int(nums[0]) if nums else float('inf')
        files.sort(key=extract_num)
        if not files:
            logging.error("لم يتم العثور على أي ملف JSON للأسئلة!")
            return
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
                        self.all_questions.append(Question(q))
            except Exception as e:
                logging.error(f"خطأ في تحميل {file}: {e}")
        logging.info(f"تم تحميل {len(self.all_questions)} سؤال")

    def get_all(self) -> List[Question]:
        return self.all_questions

    def get_categories(self) -> List[str]:
        return sorted({q.category for q in self.all_questions})

# ======================== بيانات المستخدمين ========================
USER_DATA_DIR = os.path.join(BASE_DIR, "user_data")
if not os.path.exists(USER_DATA_DIR):
    os.makedirs(USER_DATA_DIR)

def load_user_data(user_id: int):
    path = os.path.join(USER_DATA_DIR, f"{user_id}.json")
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None

def save_user_data(user_id: int, data: dict):
    path = os.path.join(USER_DATA_DIR, f"{user_id}.json")
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def init_user_data(user_id: int, loader: DataLoader) -> dict:
    all_qs = loader.get_all()
    base_data = {
        'user_id': user_id,
        'question_ids': [q.id for q in all_qs],
        'current_ids': [q.id for q in all_qs],
        'current_index': 0,
        'answered': [False] * len(all_qs),
        'selected': [None] * len(all_qs),
        'correct_flags': [False] * len(all_qs),
        'bookmarks': [False] * len(all_qs),
        'answer_log': {},
        'mode': 'normal'
    }
    save_user_data(user_id, base_data)
    return base_data

def get_user_state(user_id: int, loader: DataLoader) -> dict:
    data = load_user_data(user_id)
    if data is None:
        data = init_user_data(user_id, loader)
    return data

def update_user_state(user_id: int, data: dict):
    save_user_data(user_id, data)

def get_question_by_id(qid: int, loader: DataLoader) -> Optional[Question]:
    for q in loader.get_all():
        if q.id == qid:
            return q
    return None

def get_current_question(user_data: dict, loader: DataLoader) -> Optional[Question]:
    idx = user_data['current_index']
    if idx >= len(user_data['current_ids']):
        return None
    qid = user_data['current_ids'][idx]
    return get_question_by_id(qid, loader)

# ======================== دوال الواجهة ========================
def build_main_menu():
    keyboard = [
        [InlineKeyboardButton("📝 اختبار مخصص", callback_data="custom_quiz")],
        [InlineKeyboardButton("📂 تصفية حسب الفئة", callback_data="categories")],
        [InlineKeyboardButton("⭐ إشاراتي", callback_data="bookmarks")],
        [InlineKeyboardButton("❌ الأخطاء فقط", callback_data="wrong")],
        [InlineKeyboardButton("📊 إحصائيات", callback_data="stats")],
        [InlineKeyboardButton("📥 تصدير النتائج", callback_data="export")],
        [InlineKeyboardButton("🔄 إعادة تعيين", callback_data="reset")],
    ]
    return InlineKeyboardMarkup(keyboard)

def build_back_button():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="menu")]])

def build_option_buttons(q: Question, user_data: dict, idx: int):
    buttons = []
    selected = user_data['selected'][idx] if idx < len(user_data['selected']) else None
    answered = user_data['answered'][idx] if idx < len(user_data['answered']) else False
    for i, opt in enumerate(q.options):
        text = f"{chr(65+i)}. {opt}"
        if answered:
            if i == q.answer:
                text = "✅ " + text
            elif i == selected:
                text = "❌ " + text
        callback = f"ans_{idx}_{i}" if not answered else "noop"
        buttons.append([InlineKeyboardButton(text, callback_data=callback)])
    
    nav_buttons = []
    if user_data['current_index'] > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️", callback_data="prev"))
    if user_data['current_index'] < len(user_data['current_ids']) - 1:
        nav_buttons.append(InlineKeyboardButton("➡️", callback_data="next"))
    nav_buttons.append(InlineKeyboardButton("📖 شرح", callback_data="explain"))
    nav_buttons.append(InlineKeyboardButton("⭐", callback_data="bookmark"))
    buttons.append(nav_buttons)
    buttons.append([InlineKeyboardButton("🏠 القائمة", callback_data="menu")])
    return InlineKeyboardMarkup(buttons)

# ======================== عرض السؤال ========================
async def show_question(update: Update, context: ContextTypes.DEFAULT_TYPE, user_data: dict, loader: DataLoader, show_explanation: bool = False):
    q = get_current_question(user_data, loader)
    if q is None:
        await update.callback_query.answer("لا يوجد سؤال!")
        return
    idx = user_data['current_index']
    total = len(user_data['current_ids'])
    
    # هروب النصوص
    question_text = escape_html(q.question)
    category_text = escape_html(q.category)
    explanation_text = escape_html(q.explanation) if q.explanation else "لا يوجد شرح."
    
    text = f"📌 <b>السؤال {idx+1}/{total}</b>\n📂 <b>الفئة:</b> {category_text}\n\n{question_text}\n\n"
    if show_explanation:
        text += f"📖 <b>الشرح:</b>\n{explanation_text}"
    
    reply_markup = build_option_buttons(q, user_data, idx)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode='HTML', reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, parse_mode='HTML', reply_markup=reply_markup)

async def show_question_message(update: Update, context: ContextTypes.DEFAULT_TYPE, user_data: dict, loader: DataLoader):
    q = get_current_question(user_data, loader)
    if q is None:
        await update.message.reply_text("لا يوجد سؤال!")
        return
    idx = user_data['current_index']
    total = len(user_data['current_ids'])
    
    question_text = escape_html(q.question)
    category_text = escape_html(q.category)
    
    text = f"📌 <b>السؤال {idx+1}/{total}</b>\n📂 <b>الفئة:</b> {category_text}\n\n{question_text}"
    reply_markup = build_option_buttons(q, user_data, idx)
    await update.message.reply_text(text, parse_mode='HTML', reply_markup=reply_markup)

# ======================== دوال مساعدة ========================
def get_stats(user_data: dict, loader: DataLoader) -> str:
    total = len(user_data['answer_log'])
    correct = sum(1 for v in user_data['answer_log'].values() if v)
    wrong = total - correct
    pct = (correct / total * 100) if total > 0 else 0
    result = f"📊 <b>الإحصائيات</b>\nإجمالي: {total}\n✅ صحيح: {correct}\n❌ خطأ: {wrong}\n🎯 النسبة: {pct:.1f}%\n"
    cat_stats = defaultdict(lambda: {'correct': 0, 'wrong': 0})
    for qid, status in user_data['answer_log'].items():
        q = get_question_by_id(qid, loader)
        if q:
            cat = q.category
            if status: cat_stats[cat]['correct'] += 1
            else: cat_stats[cat]['wrong'] += 1
    if cat_stats:
        result += "\n📂 <b>حسب الفئة:</b>\n"
        for cat, vals in cat_stats.items():
            c, w = vals['correct'], vals['wrong']
            if c + w > 0:
                result += f"  {cat}: {c}/{c+w} ({c/(c+w)*100:.0f}%)\n"
    return escape_html(result)

def export_user_results(user_id: int, user_data: dict, loader: DataLoader) -> str:
    path = os.path.join(USER_DATA_DIR, f"{user_id}_export.csv")
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['id', 'السؤال', 'اختيارك', 'الإجابة الصحيحة', 'صحيح؟', 'الفئة'])
        for idx, qid in enumerate(user_data['current_ids']):
            q = get_question_by_id(qid, loader)
            if not q: continue
            chosen = user_data['selected'][idx] if idx < len(user_data['selected']) else None
            user_ans = q.options[chosen] if chosen is not None else ''
            correct = user_data['correct_flags'][idx] if idx < len(user_data['correct_flags']) else False
            writer.writerow([q.id, q.question, user_ans, q.options[q.answer], 'نعم' if correct else 'لا', q.category])
    return path

def prepare_quiz(user_data: dict, selected_questions: List[Question]):
    user_data['current_ids'] = [q.id for q in selected_questions]
    user_data['current_index'] = 0
    user_data['answered'] = [False] * len(selected_questions)
    user_data['selected'] = [None] * len(selected_questions)
    user_data['correct_flags'] = [False] * len(selected_questions)
    user_data['bookmarks'] = [False] * len(selected_questions)
    return user_data

# ======================== المحادثات (Conversation) ========================
CUSTOM_QUIZ_STATE = 1

async def custom_quiz_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📝 <b>اختبار مخصص</b>\nأدخل عدد الأسئلة و المدة (بالدقائق) بالصيغة:\n\n<code>عدد_الأسئلة المدة</code>\nمثال: <code>20 5</code>\n(يعني 20 سؤالاً و 5 دقائق)",
        parse_mode='HTML'
    )
    return CUSTOM_QUIZ_STATE

async def custom_quiz_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    loader = context.bot_data['loader']
    text = update.message.text.strip()
    parts = text.split()
    if len(parts) != 2:
        await update.message.reply_text("❌ الصيغة غير صحيحة. استخدم: <code>20 5</code>", parse_mode='HTML')
        return CUSTOM_QUIZ_STATE
    
    try:
        count = int(parts[0])
        minutes = int(parts[1])
    except ValueError:
        await update.message.reply_text("❌ أرقام فقط. حاول مرة أخرى.")
        return CUSTOM_QUIZ_STATE
    
    all_q = loader.get_all()
    if count <= 0:
        await update.message.reply_text("❌ عدد الأسئلة يجب أن يكون أكبر من صفر.")
        return CUSTOM_QUIZ_STATE
    if count > len(all_q):
        count = len(all_q)
        await update.message.reply_text(f"⚠️ العدد المطلوب أكبر من المتاح، سيتم استخدام {count} سؤال.")
    
    selected = random.sample(all_q, count)
    user_data = get_user_state(user_id, loader)
    prepare_quiz(user_data, selected)
    update_user_state(user_id, user_data)
    
    context.user_data['mock_time'] = minutes * 60
    context.user_data['mock_start'] = datetime.now()
    context.user_data['user_id'] = user_id
    
    # جدولة إيقاف الاختبار بعد انتهاء الوقت
    job = context.job_queue.run_once(mock_timeout, minutes * 60, user_id=user_id, name=f"mock_{user_id}")
    context.user_data['job'] = job
    
    await update.message.reply_text(f"⏱ بدء الاختبار: {count} سؤالاً، {minutes} دقيقة. حظاً موفقاً!")
    await show_question_message(update, context, user_data, loader)
    return ConversationHandler.END

async def mock_timeout(context: ContextTypes.DEFAULT_TYPE):
    user_id = context.job.user_id
    loader = context.bot_data['loader']
    user_data = get_user_state(user_id, loader)
    stats = get_stats(user_data, loader)
    await context.bot.send_message(user_id, f"⏰ <b>انتهى الوقت!</b>\n{stats}", parse_mode='HTML')
    user_data['mode'] = 'normal'
    update_user_state(user_id, user_data)

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ تم الإلغاء.", reply_markup=build_main_menu())
    return ConversationHandler.END

# ======================== معالج الأزرار ========================
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    loader = context.bot_data['loader']
    user_data = get_user_state(user_id, loader)
    
    if data == "noop":
        return
    
    if data.startswith("ans_"):
        _, idx_str, opt_str = data.split("_")
        idx, opt = int(idx_str), int(opt_str)
        if user_data['answered'][idx]:
            await query.answer("لقد أجبت بالفعل!")
            return
        user_data['selected'][idx] = opt
        user_data['answered'][idx] = True
        qid = user_data['current_ids'][idx]
        q = get_question_by_id(qid, loader)
        correct = (opt == q.answer)
        user_data['correct_flags'][idx] = correct
        user_data['answer_log'][qid] = correct
        update_user_state(user_id, user_data)
        await show_question(update, context, user_data, loader)
        return
    
    if data == "next":
        if user_data['current_index'] < len(user_data['current_ids']) - 1:
            user_data['current_index'] += 1
            update_user_state(user_id, user_data)
            await show_question(update, context, user_data, loader)
        else:
            await query.answer("أنت في آخر سؤال!")
        return
    
    if data == "prev":
        if user_data['current_index'] > 0:
            user_data['current_index'] -= 1
            update_user_state(user_id, user_data)
            await show_question(update, context, user_data, loader)
        else:
            await query.answer("أنت في أول سؤال!")
        return
    
    if data == "explain":
        await show_question(update, context, user_data, loader, show_explanation=True)
        return
    
    if data == "bookmark":
        idx = user_data['current_index']
        user_data['bookmarks'][idx] = not user_data['bookmarks'][idx]
        update_user_state(user_id, user_data)
        await show_question(update, context, user_data, loader)
        return
    
    if data == "menu":
        await query.edit_message_text("🏠 <b>القائمة الرئيسية</b>", parse_mode='HTML', reply_markup=build_main_menu())
        return
    
    if data == "custom_quiz":
        await custom_quiz_start(update, context)
        return
    
    if data == "categories":
        cats = loader.get_categories()
        buttons = []
        row = []
        for i, c in enumerate(cats):
            row.append(InlineKeyboardButton(c, callback_data=f"cat_{c}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data="menu")])
        await query.edit_message_text("📂 <b>اختر فئة:</b>", parse_mode='HTML', reply_markup=InlineKeyboardMarkup(buttons))
        return
    
    if data.startswith("cat_"):
        cat = data[4:]
        filtered = [q for q in loader.get_all() if q.category == cat]
        if not filtered:
            await query.answer("لا توجد أسئلة في هذه الفئة.")
            return
        prepare_quiz(user_data, filtered)
        update_user_state(user_id, user_data)
        await show_question(update, context, user_data, loader)
        return
    
    if data == "bookmarks":
        bm_ids = [user_data['current_ids'][i] for i, b in enumerate(user_data['bookmarks']) if b]
        if not bm_ids:
            await query.answer("لا توجد إشارات مرجعية.", show_alert=True)
            return
        bm_qs = [get_question_by_id(qid, loader) for qid in bm_ids if get_question_by_id(qid, loader)]
        prepare_quiz(user_data, bm_qs)
        update_user_state(user_id, user_data)
        await show_question(update, context, user_data, loader)
        return
    
    if data == "wrong":
        wrong_ids = [qid for qid, status in user_data['answer_log'].items() if not status]
        if not wrong_ids:
            await query.answer("🥳 لا توجد أخطاء! أحسنت!", show_alert=True)
            return
        wrong_qs = [get_question_by_id(qid, loader) for qid in wrong_ids if get_question_by_id(qid, loader)]
        prepare_quiz(user_data, wrong_qs)
        update_user_state(user_id, user_data)
        await show_question(update, context, user_data, loader)
        return
    
    if data == "stats":
        stats_text = get_stats(user_data, loader)
        await query.edit_message_text(stats_text, parse_mode='HTML', reply_markup=build_back_button())
        return
    
    if data == "export":
        path = export_user_results(user_id, user_data, loader)
        with open(path, 'rb') as f:
            await query.message.reply_document(f, filename=os.path.basename(path))
        await query.edit_message_text("✅ <b>تم التصدير بنجاح!</b>", parse_mode='HTML', reply_markup=build_back_button())
        return
    
    if data == "reset":
        all_ids = [q.id for q in loader.get_all()]
        user_data['current_ids'] = all_ids.copy()
        user_data['current_index'] = 0
        user_data['answered'] = [False] * len(all_ids)
        user_data['selected'] = [None] * len(all_ids)
        user_data['correct_flags'] = [False] * len(all_ids)
        user_data['bookmarks'] = [False] * len(all_ids)
        user_data['answer_log'] = {}
        update_user_state(user_id, user_data)
        await query.edit_message_text("🔄 <b>تمت إعادة التعيين بنجاح.</b>", parse_mode='HTML', reply_markup=build_back_button())
        return

# ======================== الأوامر النصية ========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    loader = context.bot_data['loader']
    get_user_state(user_id, loader)
    total = len(loader.get_all())
    await update.message.reply_text(
        f"👋 أهلاً بك في بوت الأسئلة الشامل!\nعدد الأسئلة: {total}\nاستخدم الأزرار للتنقل.",
        reply_markup=build_main_menu()
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 <b>الأوامر المتاحة:</b>\n"
        "/start - القائمة الرئيسية\n"
        "/stats - عرض الإحصائيات\n"
        "/reset - إعادة تعيين التقدم\n"
        "/export - تصدير النتائج\n"
        "/shuffle - خلط الأسئلة الحالية\n"
        "استخدم الأزرار للتفاعل.",
        parse_mode='HTML'
    )

async def shuffle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    loader = context.bot_data['loader']
    user_data = get_user_state(user_id, loader)
    current_qs = [get_question_by_id(qid, loader) for qid in user_data['current_ids'] if get_question_by_id(qid, loader)]
    random.shuffle(current_qs)
    prepare_quiz(user_data, current_qs)
    update_user_state(user_id, user_data)
    await update.message.reply_text("🔀 تم خلط الأسئلة.")
    await show_question_message(update, context, user_data, loader)

# ======================== التشغيل الرئيسي ========================
def main():
    loader = DataLoader()
    if not loader.get_all():
        print("خطأ: لم يتم تحميل الأسئلة. تأكد من وجود ملفات JSON.")
        return

    TOKEN = os.getenv("BOT_TOKEN")
    if not TOKEN:
        print("خطأ: لم يتم العثور على BOT_TOKEN في متغيرات البيئة.")
        return

    application = Application.builder().token(TOKEN).build()
    application.bot_data['loader'] = loader

    # محادثة الاختبار المخصص
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(custom_quiz_start, pattern="^custom_quiz$")],
        states={
            CUSTOM_QUIZ_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, custom_quiz_receive)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
    )
    application.add_handler(conv_handler)

    # الأوامر
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("shuffle", shuffle_command))
    application.add_handler(CallbackQueryHandler(button_callback))

    print("البوت يعمل على Render...")
    application.run_polling()

if __name__ == "__main__":
    main()
