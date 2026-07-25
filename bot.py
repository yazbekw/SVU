#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import glob
import random
import pickle
import csv
from datetime import datetime
from collections import defaultdict
from typing import List, Dict, Any, Optional
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

# ======================== التهيئة ========================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# تحديد المسار الأساسي للمشروع (حيث توجد ملفات JSON)
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
        # البحث عن جميع ملفات JSON في المجلد الحالي
        pattern = os.path.join(BASE_DIR, '*.json')
        files = glob.glob(pattern)
        # استبعاد ملفات data الخاصة بالمستخدمين لو وجدت في نفس المجلد (احتياطي)
        files = [f for f in files if not f.endswith('_export.csv') and not 'user_data' in f]
        
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

def build_main_menu():
    keyboard = [
        [InlineKeyboardButton("📝 اختبار (10)", callback_data="quiz_10")],
        [InlineKeyboardButton("📂 تصفية حسب الفئة", callback_data="categories")],
        [InlineKeyboardButton("📊 إحصائيات", callback_data="stats")],
        [InlineKeyboardButton("⭐ إشاراتي", callback_data="bookmarks")],
        [InlineKeyboardButton("❌ الأخطاء", callback_data="wrong")],
        [InlineKeyboardButton("🔄 إعادة تعيين", callback_data="reset")],
        [InlineKeyboardButton("📥 تصدير", callback_data="export")],
    ]
    return InlineKeyboardMarkup(keyboard)

async def show_question(update: Update, context: ContextTypes.DEFAULT_TYPE, user_data: dict, loader: DataLoader):
    q = get_current_question(user_data, loader)
    if q is None:
        await update.callback_query.answer("لا يوجد سؤال!")
        return
    idx = user_data['current_index']
    total = len(user_data['current_ids'])
    text = f"📌 *السؤال {idx+1}/{total}*\n*الفئة:* {q.category}\n\n{q.question}"
    reply_markup = build_option_buttons(q, user_data, idx)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode='Markdown', reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=reply_markup)

def get_stats(user_data: dict, loader: DataLoader) -> str:
    total = len(user_data['answer_log'])
    correct = sum(1 for v in user_data['answer_log'].values() if v)
    wrong = total - correct
    pct = (correct / total * 100) if total > 0 else 0
    result = f"📊 *الإحصائيات*\nإجمالي: {total}\n✅ صحيح: {correct}\n❌ خطأ: {wrong}\n🎯 النسبة: {pct:.1f}%\n"
    cat_stats = defaultdict(lambda: {'correct': 0, 'wrong': 0})
    for qid, status in user_data['answer_log'].items():
        q = get_question_by_id(qid, loader)
        if q:
            cat = q.category
            if status: cat_stats[cat]['correct'] += 1
            else: cat_stats[cat]['wrong'] += 1
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
            if not q: continue
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
    
    if data == "noop": return
    
    if data.startswith("ans_"):
        _, idx_str, opt_str = data.split("_")
        idx, opt = int(idx_str), int(opt_str)
        if user_data['answered'][idx]: return
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
        return
    if data == "prev":
        if user_data['current_index'] > 0:
            user_data['current_index'] -= 1
            update_user_state(user_id, user_data)
            await show_question(update, context, user_data, loader)
        return
    if data == "explain":
        q = get_current_question(user_data, loader)
        await query.answer(q.explanation[:200] if q and q.explanation else "لا يوجد شرح.", show_alert=True)
        return
    if data == "bookmark":
        idx = user_data['current_index']
        user_data['bookmarks'][idx] = not user_data['bookmarks'][idx]
        update_user_state(user_id, user_data)
        await show_question(update, context, user_data, loader)
        return
    if data == "menu":
        await query.edit_message_text("🏠 القائمة الرئيسية", reply_markup=build_main_menu())
        return
    if data.startswith("quiz_"):
        count = min(int(data.split("_")[1]), len(loader.get_all()))
        selected = random.sample(loader.get_all(), count)
        user_data['current_ids'] = [q.id for q in selected]
        user_data['current_index'] = 0
        user_data['answered'] = [False] * count
        user_data['selected'] = [None] * count
        user_data['correct_flags'] = [False] * count
        user_data['bookmarks'] = [False] * count
        update_user_state(user_id, user_data)
        await show_question(update, context, user_data, loader)
        return
    if data == "categories":
        cats = loader.get_categories()
        buttons = [[InlineKeyboardButton(c, callback_data=f"cat_{c}")] for c in cats]
        buttons.append([InlineKeyboardButton("🔙", callback_data="menu")])
        await query.edit_message_text("اختر فئة:", reply_markup=InlineKeyboardMarkup(buttons))
        return
    if data.startswith("cat_"):
        cat = data[4:]
        filtered = [q for q in loader.get_all() if q.category == cat]
        if not filtered: return
        user_data['current_ids'] = [q.id for q in filtered]
        user_data['current_index'] = 0
        user_data['answered'] = [False] * len(filtered)
        user_data['selected'] = [None] * len(filtered)
        user_data['correct_flags'] = [False] * len(filtered)
        user_data['bookmarks'] = [False] * len(filtered)
        update_user_state(user_id, user_data)
        await show_question(update, context, user_data, loader)
        return
    if data == "stats":
        await query.edit_message_text(get_stats(user_data, loader), parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("رجوع", callback_data="menu")]]))
        return
    if data == "bookmarks":
        bm_ids = [user_data['current_ids'][i] for i, b in enumerate(user_data['bookmarks']) if b]
        if not bm_ids: await query.answer("لا توجد إشارات."); return
        user_data['current_ids'] = bm_ids
        user_data['current_index'] = 0
        user_data['answered'] = [False] * len(bm_ids)
        user_data['selected'] = [None] * len(bm_ids)
        user_data['correct_flags'] = [False] * len(bm_ids)
        user_data['bookmarks'] = [False] * len(bm_ids)
        update_user_state(user_id, user_data)
        await show_question(update, context, user_data, loader)
        return
    if data == "wrong":
        wrong_ids = [qid for qid, status in user_data['answer_log'].items() if not status]
        if not wrong_ids: await query.answer("🥳 لا توجد أخطاء!"); return
        user_data['current_ids'] = wrong_ids
        user_data['current_index'] = 0
        user_data['answered'] = [False] * len(wrong_ids)
        user_data['selected'] = [None] * len(wrong_ids)
        user_data['correct_flags'] = [False] * len(wrong_ids)
        user_data['bookmarks'] = [False] * len(wrong_ids)
        update_user_state(user_id, user_data)
        await show_question(update, context, user_data, loader)
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
        await query.edit_message_text("تمت إعادة التعيين.", reply_markup=build_main_menu())
        return
    if data == "export":
        path = export_user_results(user_id, user_data, loader)
        with open(path, 'rb') as f:
            await query.message.reply_document(f, filename=os.path.basename(path))
        await query.edit_message_text("تم التصدير.", reply_markup=build_main_menu())
        return

# ======================== الأوامر النصية ========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    loader = context.bot_data['loader']
    get_user_state(user_id, loader)
    await update.message.reply_text(
        f"👋 أهلاً! عدد الأسئلة: {len(loader.get_all())}\nاستخدم الأزرار للتنقل.",
        reply_markup=build_main_menu()
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("/start - القائمة\n/quiz - اختبار عشوائي\n/stats - إحصائيات\n/reset - تعيين")

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

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(button_callback))

    print("البوت يعمل على Render...")
    application.run_polling()

if __name__ == "__main__":
    main()
