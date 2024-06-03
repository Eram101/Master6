import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from telegram import Update, ParseMode
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext
from retrying import retry
import logging

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = "6986622662:AAEcaJWizB9Rpy_zdmBJcHxr6lU_HddGMOk"  # Replace with your bot token
ADMIN_USER_IDS = {6023294627, 5577750831, 1187810967}
REGULAR_USER_IDS = set()
UPLOADS_DIR = "uploads"
OUTPUTS_DIR = "outputs"
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB

# Ensure directories exist
os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(OUTPUTS_DIR, exist_ok=True)

processed_domains = set()
file_queue = []
processing_now = False
executor = ThreadPoolExecutor(max_workers=500)

user_timers = {}


def retry_if_timeout(exception):
  return isinstance(exception, Exception)


@retry(wait_fixed=2000,
       stop_max_attempt_number=3,
       retry_on_exception=retry_if_timeout)
def make_telegram_api_request(method, *args, **kwargs):
  return method(*args, **kwargs)


def start(update: Update, context: CallbackContext) -> None:
  user_id = update.message.from_user.id
  role = "Admin" if user_id in ADMIN_USER_IDS else "Regular User" if user_id in REGULAR_USER_IDS else "Unauthorized User"

  update.message.reply_text(
      f'Welcome to Subdomain Enumeration Bot!\n'
      f'Send me a file with domains or a single domain to get started.\n'
      f'Your role: {role}\n'
      f'To check for remaining time use this /timeleft.')


def process_file(file_entry, context: CallbackContext) -> None:
  try:
    file_name = file_entry["file_name"]
    chat_id = file_entry["chat_id"]
    output_file_name = f"{file_name.split('.')[0]}_sub-domains.txt"
    output_file_path = os.path.join(OUTPUTS_DIR, output_file_name)

    with open(os.path.join(UPLOADS_DIR, file_name), 'r') as file:
      domains = file.read().splitlines()

    new_domains = list(set(domains) - processed_domains)

    if not new_domains:
      context.bot.send_message(chat_id, "No new domains to process.")
      return

    subprocess.check_call([
        'subfinder', '-dL',
        os.path.join(UPLOADS_DIR, file_name), '-o', output_file_path
    ])

    with open(output_file_path, 'rb') as result_file:
      context.bot.send_document(chat_id, document=result_file)

    processed_domains.update(new_domains)
    os.remove(os.path.join(UPLOADS_DIR, file_name))
    os.remove(output_file_path)

  except Exception as e:
    logger.error(f"Error during enumeration: {e}")
    context.bot.send_message(chat_id, f"Error during enumeration: {e}")

  finally:
    process_file_queue(context)


def process_file_queue(context: CallbackContext) -> None:
  global processing_now
  if file_queue:
    file_entry = file_queue.pop(0)
    executor.submit(process_file, file_entry, context)


def add_user(update: Update, context: CallbackContext) -> None:
  try:
    user_id_to_add = int(context.args[0])
    duration_seconds = int(context.args[1])

    if update.message.from_user.id in ADMIN_USER_IDS:
      REGULAR_USER_IDS.add(user_id_to_add)
      user_timers[user_id_to_add] = time.time() + duration_seconds
      update.message.reply_text(
          f"User {user_id_to_add} added for {duration_seconds} seconds as a Regular User."
      )

      # Notify the new user about being added and display the granted time
      make_telegram_api_request(
          context.bot.send_message, user_id_to_add,
          f"You have been granted access for {duration_seconds} seconds as a Regular User."
      )

    else:
      update.message.reply_text("You are not authorized to add users.")
  except (ValueError, IndexError):
    update.message.reply_text(
        "Invalid command. Use /add (user_id) (duration_seconds)")


def handle_document(update: Update, context: CallbackContext) -> None:
  global processing_now
  file_id = update.message.document.file_id
  file_name = update.message.document.file_name
  chat_id = update.message.chat_id
  user_id = update.message.from_user.id

  if not is_user_authorized(user_id):
    context.bot.send_message(chat_id,
                             "You are not authorized to use this bot.")
    return

  if file_name in [entry["file_name"] for entry in file_queue]:
    context.bot.send_message(
        chat_id, f"You have already uploaded the file '{file_name}'.")
    return

  file = make_telegram_api_request(context.bot.get_file, file_id)

  if file.file_size > MAX_FILE_SIZE_BYTES:
    context.bot.send_message(chat_id,
                             "File size exceeds the maximum allowed size.")
    return

  unique_filename = file_name
  upload_file_path = os.path.join(UPLOADS_DIR, unique_filename)

  try:
    file.download(upload_file_path)

    if not processing_now:
      context.bot.send_message(chat_id,
                               "Please wait. This might take some time!")

    file_queue.append({"file_name": file_name, "chat_id": chat_id})

    if not processing_now:
      executor.submit(process_file, file_queue.pop(0), context)
      processing_now = True

  except Exception as e:
    logger.error(f"Error during file processing: {e}")
    context.bot.send_message(chat_id, f"Error during file processing: {e}")

  finally:
    process_file_queue(context)


def handle_text(update: Update, context: CallbackContext) -> None:
  global processing_now
  chat_id = update.message.chat_id
  domain = update.message.text.strip().lower()
  user_id = update.message.from_user.id

  if is_user_authorized(user_id):
    unique_filename = f"{domain.replace('.', '_')}.txt"
    upload_file_path = os.path.join(UPLOADS_DIR, unique_filename)

    try:
      with open(upload_file_path, 'w') as temp_file:
        temp_file.write(domain)

      if not processing_now:
        context.bot.send_message(chat_id,
                                 "Please wait. This might take some time!")

      file_queue.append({"file_name": unique_filename, "chat_id": chat_id})

      if not processing_now:
        executor.submit(process_file, file_queue.pop(0), context)
        processing_now = True

    except Exception as e:
      logger.error(f"Error during file processing: {e}")
      context.bot.send_message(chat_id, f"Error during file processing: {e}")

    finally:
      process_file_queue(context)
  else:
    context.bot.send_message(chat_id,
                             "You are not authorized to use this bot.")


def is_user_authorized(user_id):
  return (user_id in ADMIN_USER_IDS or
          (user_id in REGULAR_USER_IDS and
           (user_id not in user_timers or time.time() < user_timers[user_id])))


def time_left(update: Update, context: CallbackContext) -> None:
  user_id = update.message.from_user.id

  if user_id in ADMIN_USER_IDS and user_id in user_timers:
    current_time = time.time()
    expiration_time = user_timers[user_id]

    if current_time < expiration_time:
      time_remaining = int(expiration_time - current_time)
      make_telegram_api_request(update.message.reply_text,
                                f"You have {time_remaining} seconds left.")
    else:
      make_telegram_api_request(update.message.reply_text,
                                "Your access has expired.")
  else:
    make_telegram_api_request(update.message.reply_text,
                              "You are not authorized to use this command.")


def view_processed_domains(update: Update, context: CallbackContext) -> None:
  user_id = update.message.from_user.id

  if user_id in ADMIN_USER_IDS:
    processed_domains_list = list(processed_domains)
    message = "\n".join(
        processed_domains_list
    ) if processed_domains_list else "No domains processed yet."
    make_telegram_api_request(update.message.reply_text,
                              f"Processed domains:\n{message}")
  else:
    make_telegram_api_request(update.message.reply_text,
                              "You are not authorized to use this command.")


def clear_processed_domains(update: Update, context: CallbackContext) -> None:
  user_id = update.message.from_user.id

  if user_id in ADMIN_USER_IDS:
    processed_domains.clear()
    make_telegram_api_request(update.message.reply_text,
                              "Processed domains list cleared.")
  else:
    make_telegram_api_request(update.message.reply_text,
                              "You are not authorized to use this command.")


def list_users(update: Update, context: CallbackContext) -> None:
  user_id = update.message.from_user.id

  if user_id in ADMIN_USER_IDS:
    admins = list(ADMIN_USER_IDS)
    regular_users_info = []

    for user_id in REGULAR_USER_IDS:
      time_remaining = user_timers.get(user_id, 0) - time.time()
      time_remaining_text = (f"{int(time_remaining)} seconds left"
                             if time_remaining > 0 else "No time limit")
      regular_users_info.append((user_id, time_remaining_text))

    message = f"Admins: {admins}\nRegular Users:\n"
    for user_info in regular_users_info:
      message += f"{user_info[0]} - {user_info[1]}\n"

    make_telegram_api_request(update.message.reply_text, message)
  else:
    make_telegram_api_request(update.message.reply_text,
                              "You are not authorized to use this command.")


def help_command(update: Update, context: CallbackContext) -> None:
  make_telegram_api_request(
      update.message.reply_text, "Available commands:\n"
      "/start - Start the bot\n"
      "/add (user_id) (duration_seconds) - Add a user with limited access\n"
      "/timeleft - Check remaining time (admin only)\n"
      "/viewprocessed - View processed domains (admin only)\n"
      "/clearprocessed - Clear processed domains list (admin only)\n"
      "/listusers - List admins and regular users (admin only)\n"
      "/help - Display this help message")


if __name__ == '__main__':
  updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
  dispatcher = updater.dispatcher

  dispatcher.add_handler(CommandHandler("start", start))
  dispatcher.add_handler(CommandHandler("add", add_user))
  dispatcher.add_handler(CommandHandler("timeleft", time_left))
  dispatcher.add_handler(
      CommandHandler("viewprocessed", view_processed_domains))
  dispatcher.add_handler(
      CommandHandler("clearprocessed", clear_processed_domains))
  dispatcher.add_handler(CommandHandler("listusers", list_users))
  dispatcher.add_handler(CommandHandler("help", help_command))
  dispatcher.add_handler(MessageHandler(Filters.document, handle_document))
  dispatcher.add_handler(
      MessageHandler(Filters.text & ~Filters.command, handle_text))

  updater.start_polling()
  updater.idle()
