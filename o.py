import asyncio
import random
import re
import uuid
import httpx
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    CallbackQueryHandler
)

# === CONFIGURATION ===
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

if not TELEGRAM_BOT_TOKEN:
    print("❌ TELEGRAM_BOT_TOKEN is missing!")
    exit(1)
if not OPENAI_API_KEY:
    print("❌ OPENAI_API_KEY is missing!")
    exit(1)

# === MEMORY ===
user_memory = {}
button_mapping = {}

# === COMMANDS ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_memory[user_id] = []

    await update.message.reply_text(
        "👋 Welcome! I’m your English AI assistant.\n\n"
        "🌐 Commands:\n"
        "/reset → Reset chat\n"
        "/status → Show memory info"
    )

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_memory[user_id] = []
    await update.message.reply_text("✅ Chat history cleared!")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    memory_len = len(user_memory.get(user_id, []))
    await update.message.reply_text(f"📊 Messages remembered: {memory_len}")

# === UTILS ===
def create_progress_bar(percent):
    filled = min(int(percent / 5), 20)
    empty = 20 - filled
    return "▰" * filled + "▱" * empty

def random_progress_steps():
    percentages = [20, 40, 60, 80, 100]
    phrases = ["🧠 Analyzing...", "💭 Thinking...", "📝 Drafting...", "✅ Finalizing..."]
    return list(zip(phrases, percentages))

# === HANDLE TEXT ===
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_message = update.message.text.strip()

    if user_id not in user_memory:
        user_memory[user_id] = []

    user_memory[user_id].append({"role": "user", "content": user_message})
    await update.message.chat_action("typing")

    # Progress animation
    progress_steps = random_progress_steps()
    progress_msg = await update.message.reply_text("🧠 Starting analysis... 0%")
    for phrase, percent in progress_steps:
        bar = create_progress_bar(percent)
        await asyncio.sleep(random.uniform(0.3, 0.6))
        await progress_msg.edit_text(f"{phrase}\n[{bar}] {percent}%")
    await progress_msg.delete()

    # Call OpenAI
    system_prompt = "You are a helpful and intelligent English assistant. Always reply in English."
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
    data = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "system", "content": system_prompt}] + user_memory[user_id],
        "temperature": 0.7,
        "max_tokens": 1000
    }

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(OPENAI_URL, headers=headers, json=data)
            response.raise_for_status()
            ai_reply = response.json()["choices"][0]["message"]["content"].strip()

        user_memory[user_id].append({"role": "assistant", "content": ai_reply})

        # Sentence-by-sentence typing
        sentences = re.split(r'(?<=[.!?]) +', ai_reply)
        message_limit = 4000
        current_text = ""
        last_text = ""
        typing_msg = await update.message.reply_text("100%")
        for sentence in sentences:
            sentence = re.sub(r"(?<!\w)([A-Z][a-z]+(?: [A-Z][a-z]+)*)", r"<b>\1</b>", sentence)
            sentence = re.sub(r"(#\d+|\d+)", r"<code>\1</code>", sentence)

            if len(current_text) + len(sentence) + 1 > message_limit:
                if current_text.strip() != last_text:
                    await typing_msg.edit_text(current_text.strip(), parse_mode="HTML")
                typing_msg = await update.message.reply_text("100%")
                current_text = ""
                last_text = ""

            current_text += sentence + " "
            if current_text.strip() != last_text:
                await typing_msg.edit_text(current_text.strip(), parse_mode="HTML")
                last_text = current_text.strip()
            await asyncio.sleep(0.5 if sentence[-1] not in ".!?," else 0.8)

        # Follow-up suggestions
        suggestion_prompt = (
            "Based on the previous answer, create 2 follow-up questions the user might ask next. "
            "Format each suggestion starting with ➥, put the question in monospace using backticks like `example?` "
            "and include a website link or source for learning more."
        )

        data["messages"] = [
            {"role": "system", "content": system_prompt},
            {"role": "assistant", "content": ai_reply},
            {"role": "user", "content": suggestion_prompt}
        ]
        async with httpx.AsyncClient(timeout=30) as client:
            sugg_response = await client.post(OPENAI_URL, headers=headers, json=data)
            if sugg_response.status_code == 200:
                suggestions_text = sugg_response.json()["choices"][0]["message"]["content"].strip()
                questions, links_list = [], []
                for line in suggestions_text.splitlines():
                    if line.startswith("➥"):
                        match = re.search(r"(https?://\S+)", line)
                        link = match.group(1) if match else None
                        question_text_only = re.sub(r"\s*\[source: https?://\S+\]", "", line.replace("➥", "").strip())
                        if link: links_list.append(link)
                        questions.append(question_text_only)
                questions = questions[:2]
                links_list = links_list[:2]

                keyboard = [[InlineKeyboardButton(q, callback_data=str(uuid.uuid4())[:8])] for q in questions]
                reply_markup = InlineKeyboardMarkup(keyboard)
                for i, q in enumerate(questions):
                    button_mapping[keyboard[i][0].callback_data] = q

                await update.message.reply_text("Here are more questions you could ask:", reply_markup=reply_markup)
                if links_list:
                    links_text = "\n".join([f'<a href="{link}">source</a>' for link in links_list])
                    await update.message.reply_text(links_text.strip(), parse_mode="HTML")

        await update.message.reply_text("◌")

    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {str(e)}")

# === BUTTON HANDLER ===
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    button_id = query.data
    question_text = button_mapping.get(button_id)
    if question_text:
        class FakeMessage:
            def __init__(self, chat_id, from_user_id, text):
                self.chat_id = chat_id
                self.from_user = type('User', (), {'id': from_user_id})()
                self.text = text
            async def reply_text(self, text, **kwargs):
                return await context.bot.send_message(chat_id=self.chat_id, text=text, **kwargs)
            async def reply_chat_action(self, action):
                return await context.bot.send_chat_action(chat_id=self.chat_id, action=action)

        fake_message = FakeMessage(update.effective_chat.id, query.from_user.id, question_text)
        fake_update = type('FakeUpdate', (), {'message': fake_message})()
        await handle_text(fake_update, context)

# === MAIN ===
async def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(button_handler))
    print("🤖 Bot is running on PTB v22+ and Python 3.13")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
