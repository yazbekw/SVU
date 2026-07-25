#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import glob
import random
import csv
import re
from datetime import datetime
from collections import defaultdict
from typing import List, Dict, Any, Optional
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters, ConversationHandler

# ======================== التهيئة ========================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

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

# حالات المحادثة للاختبار المحاكي
ASK_COUNT, ASK_TIME = range(2)

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
        'mode': 'normal',  # normal, mock
        'mock_start_time': None,
        'mock_time_limit': 0,
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

# ======================== دوال العرض ========================
async def show_question(update: Update, context: ContextTypes.DEFAULT_TYPE, user_data: dict, loader: DataLoader, show_explanation: bool = False):
    q = get_current_question(user_data, loader)
    if q is None:
        await update.callback_query.answer("لا يوجد سؤال!")
        return
    idx = user_data['current_index']
    total = len(user_data['current_ids'])
    
    text = f"📌 *السؤال {idx+1}/{total}*\n"
    text += f"📂 *الفئة:* {q.category}\n\n"
    text += f"{q.question}\n\n"
    
    if show_explanation and q.explanation:
        text += f"📖 *الشرح:*\n{q.explanation}"
    elif show_explanation and not q.explanation:
        text += "📖 *لا يوجد شرح لهذا السؤال.*"
    
    reply_markup = build_option_buttons(q, user_data, idx)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode='Markdown', reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=reply_markup)

# ======================== دوال الإحصائيات والتصدير ========================
def get_stats(user_data: dict, loader: DataLoader) -> str:
    total = len(user_data['answer_log'])
    correct = sum(1 for v in user_data['answer_log'].values() if v)
    wrong = total - correct
    pct = (correct / total * 100) if total > 0 else 0
    result = f"📊 *الإحصائيات*\n"
    result += f"إجمالي الأجوبة: {total}\n"
    result += f"✅ صحيح: {correct}\n"
    result += f"❌ خطأ: {wrong}\n"
    result += f"🎯 النسبة: {pct:.1f}%\n"
    
    cat_stats = defaultdict(lambda: {'correct': 0, 'wrong': 0})
    for qid, status in user_data['answer_log'].items():
        q = get_question_by_id(qid, loader)
        if q:
            cat = q.category
            if status:
                cat_stats[cat]['correct'] += 1
            else:
                cat_stats[cat]['wrong'] += 1
    if cat_stats:
        result += "\n📂 *حسب الفئة:*\n"
        for cat, vals in cat_stats.items():
            c, w = vals['correct'], vals['wrong']
            if c + w > 0:
                result += f"  {cat}: {c}/{c+w} ({c/(c+w)*100:.0f}%)\n"
    return result

def export_user_results(user_id: int, user_data: dict, loader: DataLoader) -> str:
    path = os.path.join(USER_DATA_DIR, f"{user_id}_export.csv")
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['id', 'السؤال', 'اختيارك', 'الإجابة الصحيحة', 'صحيح؟', 'الفئة'])
        for idx, qid in enumerate(user_data['current_ids']):
            q = get_question_by_id(qid, loader)
            if not q:
                continue
            chosen = user_data['selected'][idx] if idx < len(user_data['selected']) else None
            user_ans = q.options[chosen] if chosen is not None else ''
            correct = user_data['correct_flags'][idx] if idx < len(user_data['correct_flags']) else False
            writer.writerow([q.id, q.question, user_ans, q.options[q.answer], 'نعم' if correct else 'لا', q.category])
    return path

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
    
    # اختيار إجابة
    if data.startswith("ans_"):
        _, idx_str, opt_str = data.split("_")
        idx, opt = int(idx_str), int(opt_str)
        if user_data['answered'][idx]:
            await query.answer("لقد أجبت بالفعل!", show_alert=True)
            return
        user_data['selected'][idx] = opt
        user_data['answered'][idx] = True
        qid = user_data['current_ids'][idx]
        q = get_question_by_id(qid, loader)
        correct = (opt == q.answer)
        user_data['correct_flags'][idx] = correct
        user_data['answer_log'][qid] = correct
        update_user_state(user_id, user_data)
        await show_question(update, context, user_data, loader, show_explanation=True)
        return
    
    # التنقل
    if data == "next":
        if user_data['current_index'] < len(user_data['current_ids']) - 1:
            user_data['current_index'] += 1
            update_user_state(user_id, user_data)
            await show_question(update, context, user_data, loader)
        else:
            await query.answer("أنت في آخر سؤال!", show_alert=True)
        return
    
    if data == "prev":
        if user_data['current_index'] > 0:
            user_data['current_index'] -= 1
            update_user_state(user_id, user_data)
            await show_question(update, context, user_data, loader)
        else:
            await query.answer("أنت في أول سؤال!", show_alert=True)
        return
    
    # شرح
    if data == "explain":
        await show_question(update, context, user_data, loader, show_explanation=True)
        return
    
    # إشارة مرجعية
    if data == "bookmark":
        idx = user_data['current_index']
        user_data['bookmarks'][idx] = not user_data['bookmarks'][idx]
        update_user_state(user_id, user_data)
        status = "مضافة" if user_data['bookmarks'][idx] else "ملغاة"
        await query.answer(f"⭐ تم {status} الإشارة.", show_alert=True)
        await show_question(update, context, user_data, loader)
        return
    
    # القائمة الرئيسية
    if data == "menu":
        await query.edit_message_text("🏠 *القائمة الرئيسية*\nاختر أحد الخيارات:", parse_mode='Markdown', reply_markup=build_main_menu())
        return
    
    # اختبار مخصص (يطلب عدد الأسئلة والوقت)
    if data == "custom_quiz":
        await query.edit_message_text(
            "⏱ *اختبار مخصص*\n\n"
            "أدخل عدد الأسئلة ثم الوقت بالدقائق، مثال:\n"
            "`20 5`\n\n"
            "(يعني 20 سؤال و 5 دقائق)",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="menu")]])
        )
        context.user_data['waiting_mock'] = True
        return
    
    # تصفية حسب الفئة
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
        await query.edit_message_text("📂 *اختر فئة:*", parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(buttons))
        return
    
    if data.startswith("cat_"):
        cat = data[4:]
        filtered = [q for q in loader.get_all() if q.category == cat]
        if not filtered:
            await query.answer("لا توجد أسئلة في هذه الفئة.", show_alert=True)
            return
        user_data['current_ids'] = [q.id for q in filtered]
        user_data['current_index'] = 0
        user_data['answered'] = [False] * len(filtered)
        user_data['selected'] = [None] * len(filtered)
        user_data['correct_flags'] = [False] * len(filtered)
        user_data['bookmarks'] = [False] * len(filtered)
        update_user_state(user_id, user_data)
        await show_question(update, context, user_data, loader)
        return
    
    # الإشارات المرجعية
    if data == "bookmarks":
        bm_ids = [user_data['current_ids'][i] for i, b in enumerate(user_data['bookmarks']) if b]
        if not bm_ids:
            await query.answer("لا توجد إشارات مرجعية.", show_alert=True)
            return
        user_data['current_ids'] = bm_ids
        user_data['current_index'] = 0
        user_data['answered'] = [False] * len(bm_ids)
        user_data['selected'] = [None] * len(bm_ids)
        user_data['correct_flags'] = [False] * len(bm_ids)
        user_data['bookmarks'] = [False] * len(bm_ids)
        update_user_state(user_id, user_data)
        await show_question(update, context, user_data, loader)
        return
    
    # الأخطاء فقط
    if data == "wrong":
        wrong_ids = [qid for qid, status in user_data['answer_log'].items() if not status]
        if not wrong_ids:
            await query.answer("🥳 لا توجد أخطاء! أحسنت!", show_alert=True)
            return
        user_data['current_ids'] = wrong_ids
        user_data['current_index'] = 0
        user_data['answered'] = [False] * len(wrong_ids)
        user_data['selected'] = [None] * len(wrong_ids)
        user_data['correct_flags'] = [False] * len(wrong_ids)
        user_data['bookmarks'] = [False] * len(wrong_ids)
        update_user_state(user_id, user_data)
        await show_question(update, context, user_data, loader)
        return
    
    # الإحصائيات
    if data == "stats":
        stats_text = get_stats(user_data, loader)
        await query.edit_message_text(stats_text, parse_mode='Markdown', reply_markup=build_back_button())
        return
    
    # التصدير
    if data == "export":
        path = export_user_results(user_id, user_data, loader)
        with open(path, 'rb') as f:
            await query.message.reply_document(f, filename=os.path.basename(path))
        await query.edit_message_text("✅ *تم التصدير بنجاح!*", parse_mode='Markdown', reply_markup=build_back_button())
        return
    
    # إعادة التعيين
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
        await query.edit_message_text("🔄 *تمت إعادة التعيين بنجاح.*", parse_mode='Markdown', reply_markup=build_back_button())
        return

# ======================== معالج الرسائل النصية (لاختبار المحاكي) ========================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.user_data.get('waiting_mock'):
        return
    
    text = update.message.text.strip()
    parts = text.split()
    if len(parts) != 2:
        await update.message.reply_text("❌ الصيغة غير صحيحة. استخدم: `20 5` (عدد الأسئلة ثم الوقت بالدقائق)", parse_mode='Markdown')
        return
    
    try:
        count = int(parts[0])
        minutes = int(parts[1])
        if count <= 0 or minutes <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ يجب إدخال أرقام موجبة.")
        return
    
    loader = context.bot_data['loader']
    all_q = loader.get_all()
    if count > len(all_q):
        count = len(all_q)
        await update.message.reply_text(f"⚠️ عدد الأسئلة المتاحة هو {len(all_q)}، سيتم استخدام العدد الأقصى.")
    
    selected = random.sample(all_q, count)
    user_data = get_user_state(user_id, loader)
    user_data['current_ids'] = [q.id for q in selected]
    user_data['current_index'] = 0
    user_data['answered'] = [False] * count
    user_data['selected'] = [None] * count
    user_data['correct_flags'] = [False] * count
    user_data['bookmarks'] = [False] * count
    user_data['mode'] = 'mock'
    user_data['mock_start_time'] = datetime.now().isoformat()
    user_data['mock_time_limit'] = minutes * 60
    update_user_state(user_id, user_data)
    
    context.user_data['waiting_mock'] = False
    context.user_data['mock_end_time'] = datetime.now().timestamp() + minutes * 60
    
    await update.message.reply_text(f"⏱ *بدأ الاختبار المحاكي*\nعدد الأسئلة: {count}\nالوقت المحدد: {minutes} دقيقة\n\nأجب على الأسئلة، سيتم إنهاء الاختبار تلقائياً عند انتهاء الوقت.",
                                     parse_mode='Markdown')
    
    # إرسال السؤال الأول
    await show_question(update, context, user_data, loader)
    
    # جدولة إنهاء الاختبار تلقائياً
    context.job_queue.run_once(mock_timeout, minutes * 60, context={'user_id': user_id})

async def mock_timeout(context: ContextTypes.DEFAULT_TYPE):
    user_id = context.job.context['user_id']
    loader = context.bot_data['loader']
    user_data = get_user_state(user_id, loader)
    if user_data.get('mode') == 'mock':
        stats = get_stats(user_data, loader)
        await context.bot.send_message(user_id, f"⏰ *انتهى الوقت!*\n\n{stats}", parse_mode='Markdown', reply_markup=build_back_button())
        user_data['mode'] = 'normal'
        update_user_state(user_id, user_data)

# ======================== أوامر البوت ========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    loader = context.bot_data['loader']
    get_user_state(user_id, loader)
    await update.message.reply_text(
        f"👋 *أهلاً بك في بوت الأسئلة الشامل!*\n"
        f"📚 عدد الأسئلة: {len(loader.get_all())}\n"
        f"استخدم الأزرار للتنقل.",
        parse_mode='Markdown',
        reply_markup=build_main_menu()
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 *الأوامر المتاحة:*\n"
        "/start - القائمة الرئيسية\n"
        "/quiz - اختبار مخصص (يطلب عدد الأسئلة والوقت)\n"
        "/stats - عرض الإحصائيات\n"
        "/reset - إعادة التعيين\n"
        "/export - تصدير النتائج",
        parse_mode='Markdown'
    )

async def quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # نفس آلية custom_quiz لكن من الأمر النصي
    await update.message.reply_text(
        "⏱ *اختبار مخصص*\n\n"
        "أدخل عدد الأسئلة ثم الوقت بالدقائق، مثال:\n"
        "`20 5`",
        parse_mode='Markdown'
    )
    context.user_data['waiting_mock'] = True

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    loader = context.bot_data['loader']
    user_data = get_user_state(user_id, loader)
    stats_text = get_stats(user_data, loader)
    await update.message.reply_text(stats_text, parse_mode='Markdown', reply_markup=build_back_button())

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    loader = context.bot_data['loader']
    user_data = get_user_state(user_id, loader)
    all_ids = [q.id for q in loader.get_all()]
    user_data['current_ids'] = all_ids.copy()
    user_data['current_index'] = 0
    user_data['answered'] = [False] * len(all_ids)
    user_data['selected'] = [None] * len(all_ids)
    user_data['correct_flags'] = [False] * len(all_ids)
    user_data['bookmarks'] = [False] * len(all_ids)
    user_data['answer_log'] = {}
    update_user_state(user_id, user_data)
    await update.message.reply_text("🔄 *تمت إعادة التعيين.*", parse_mode='Markdown', reply_markup=build_back_button())

async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    loader = context.bot_data['loader']
    user_data = get_user_state(user_id, loader)
    path = export_user_results(user_id, user_data, loader)
    with open(path, 'rb') as f:
        await update.message.reply_document(f, filename=os.path.basename(path))
    await update.message.reply_text("✅ *تم التصدير.*", parse_mode='Markdown', reply_markup=build_back_button())

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

    # الأوامر
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("quiz", quiz_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("reset", reset_command))
    application.add_handler(CommandHandler("export", export_command))

    # معالج الأزرار
    application.add_handler(CallbackQueryHandler(button_callback))

    # معالج الرسائل النصية (للاختبار المخصص)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # جدولة المهام
    application.job_queue = application.job_queue

    print("البوت يعمل...")
    application.run_polling()

if __name__ == "__main__":
    main()
