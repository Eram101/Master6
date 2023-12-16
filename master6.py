import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from telegram import Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext
import time
from cachetools import TTLCache

TELEGRAM_BOT_TOKEN = "6986622662:AAEcaJWizB9Rpy_zdmBJcHxr6lU_HddGMOk"  # Replace with your bot token

UPLOADS_DIR = "uploads"
OUTPUTS_DIR = "outputs"

for directory in [UPLOADS_DIR, OUTPUTS_DIR]:
    if not os.path.exists(directory):
        os.makedirs(directory)

processed_domains = TTLCache(maxsize=100000, ttl=600)  # Cache processed domains for 10 minutes

file_queue = []
processing_now = False
executor = None

ALLOWED_USER_IDS = {6023294627, 5577750831, 1187810967}

def start(update: Update, context: CallbackContext) -> None:
    update.message.reply_text(
        'Welcome to Subdomain Enumeration Bot!\nSend me a file with domains or a single domain to get started.'
    )

def process_file(file_entry, context: CallbackContext) -> None:
    try:
        file_name = file_entry["file_name"]
        chat_id = file_entry["chat_id"]
        output_file_name = f"{file_name.split('.')[0]}_sub-domains.txt"
        output_file_path = os.path.join(OUTPUTS_DIR, output_file_name)

        with open(os.path.join(UPLOADS_DIR, file_name), 'r') as file:
            domains = file.read().splitlines()

        new_domains = list(set(domains) - set(processed_domains.keys()))

        if not new_domains:
            context.bot.send_message(chat_id, "No new domains to process.")
            return

        # Smart retrying with exponential backoff and jitter
        retries = 0
        while retries < 3:
            try:
                subprocess.check_call([
                    'subfinder', '-dL',
                    os.path.join(UPLOADS_DIR, file_name), '-o', output_file_path,
                    '-t', '8',  # Adjust the number of threads based on the available CPU cores
                    '-timeout', '10',  # Adjust the timeout for each request
                ])
                break
            except Exception as subfinder_error:
                print(f"Error running subfinder: {subfinder_error}")
                sleep_time = min(2 ** retries + (retries * 0.1), 60)
                time.sleep(sleep_time)
                retries += 1

        for allowed_chat_id in ALLOWED_USER_IDS:
            try:
                send_document(context.bot, allowed_chat_id, output_file_path)
            except Exception as send_error:
                print(f"Error sending document to chat {allowed_chat_id}: {send_error}")

        for domain in new_domains:
            processed_domains[domain] = time.time()

        os.remove(os.path.join(UPLOADS_DIR, file_name))
        os.remove(output_file_path)

    except Exception as e:
        print(f"Error during enumeration: {e}")
        context.bot.send_message(chat_id, f"Error during enumeration: {e}")

    finally:
        process_file_queue(context)

def process_file_queue(context: CallbackContext) -> None:
    global processing_now
    if file_queue:
        file_entry = file_queue.pop(0)
        executor.submit(process_file, file_entry, context)

def send_document(bot, chat_id, file_path):
    try:
        bot.send_document(chat_id, document=open(file_path, 'rb'))
    except Exception as send_error:
        print(f"Error sending document to chat {chat_id}: {send_error}")

def handle_document(update: Update, context: CallbackContext) -> None:
    global processing_now
    file_id = update.message.document.file_id
    file_name = update.message.document.file_name
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id

    if user_id not in ALLOWED_USER_IDS:
        context.bot.send_message(
            chat_id, "You are not authorized to use this bot.")
        return

    if file_name in [entry["file_name"] for entry in file_queue]:
        context.bot.send_message(
            chat_id, f"You have already uploaded the file '{file_name}'.")
        return

    file = context.bot.get_file(file_id)

    unique_filename = file_name
    upload_file_path = os.path.join(UPLOADS_DIR, unique_filename)

    try:
        file.download(upload_file_path)

        if not processing_now:
            update.message.reply_text("Please wait. This might take some time!")

        file_queue.append({"file_name": file_name, "chat_id": chat_id})

        if not processing_now:
            executor.submit(process_file, file_queue.pop(0), context)
            processing_now = True

    except Exception as e:
        print(f"Error during file processing: {e}")
        context.bot.send_message(chat_id, f"Error during file processing: {e}")

    finally:
        process_file_queue(context)

def handle_text(update: Update, context: CallbackContext) -> None:
    global processing_now
    chat_id = update.message.chat_id
    domain = update.message.text.strip().lower()
    user_id = update.message.from_user.id

    if user_id not in ALLOWED_USER_IDS:
        context.bot.send_message(
            chat_id, "You are not authorized to use this bot.")
        return

    unique_filename = f"{domain.replace('.', '_')}.txt"
    upload_file_path = os.path.join(UPLOADS_DIR, unique_filename)

    try:
        with open(upload_file_path, 'w') as temp_file:
            temp_file.write(domain)

        if not processing_now:
            update.message.reply_text("Please wait. This might take some time!")

        file_queue.append({"file_name": unique_filename, "chat_id": chat_id})

        if not processing_now:
            executor.submit(process_file, file_queue.pop(0), context)
            processing_now = True

    except Exception as e:
        print(f"Error during file processing: {e}")
        context.bot.send_message(chat_id, f"Error during file processing: {e}")

    finally:
        process_file_queue(context)

def main() -> None:
    global executor
    with ThreadPoolExecutor(max_workers=8) as executor:
        updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
        dispatcher = updater.dispatcher

        dispatcher.add_handler(CommandHandler("start", start))
        dispatcher.add_handler(MessageHandler(Filters.document, handle_document))
        dispatcher.add_handler(
            MessageHandler(Filters.text & ~Filters.command, handle_text))

        updater.start_polling()
        updater.idle()

if __name__ == '__main__':
    main()
